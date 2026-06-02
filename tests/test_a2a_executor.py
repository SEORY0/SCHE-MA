"""Integration test for the A2A executor — drives it as a mock CyberGym green agent.

No HTTP, no API, no real green: we build A2A messages, call execute() directly with a
collecting fake event queue, and (for the handshake) feed the test_vulnerable reply as a
second execute() on the same context_id. A fake brain is injected so the executor's
plumbing (intake -> brain -> green round-trip -> poc artifact) is exercised offline.
"""
import asyncio
import base64
from pathlib import Path
from uuid import uuid4

from a2a.types import (DataPart, FilePart, FileWithBytes, Message, Part, Role,
                       TaskArtifactUpdateEvent, TaskStatusUpdateEvent, TextPart)

from schemata.a2a.executor import Executor


class CollectEQ:
    def __init__(self):
        self.events = []

    async def enqueue_event(self, e):
        self.events.append(e)

    async def close(self):
        pass


class FakeCtx:
    def __init__(self, message, task_id, context_id):
        self.message = message
        self.task_id = task_id
        self.context_id = context_id
        self.current_task = None


def _file_part(name, raw, mime="application/octet-stream"):
    return Part(root=FilePart(file=FileWithBytes(
        bytes=base64.b64encode(raw).decode("ascii"), name=name, mime_type=mime)))


def _initial_message():
    return Message(messageId=uuid4().hex, role=Role.user, parts=[
        Part(root=TextPart(kind="text", text="Generate the exploit PoC for arvo:10400.")),
        _file_part("repo-vul.tar.gz", b"VULSRC", "application/gzip"),
        _file_part("description.txt", b"heap-buffer-overflow in ReadImage()", "text/plain"),
    ])


def _reply_message(payload):
    return Message(messageId=uuid4().hex, role=Role.user,
                   parts=[Part(root=DataPart(data=payload))])


def _is_test_request(ev) -> bool:
    if not isinstance(ev, TaskStatusUpdateEvent):
        return False
    parts = ev.status.message.parts if ev.status and ev.status.message else []
    has_action = any(isinstance(p.root, DataPart) and (p.root.data or {}).get("action") == "test_vulnerable"
                     for p in parts)
    has_poc = any(isinstance(p.root, FilePart) for p in parts)
    return has_action and has_poc


def _artifact_bytes(events):
    for ev in events:
        if isinstance(ev, TaskArtifactUpdateEvent) and ev.artifact and ev.artifact.name == "poc":
            for p in ev.artifact.parts:
                if isinstance(p.root, FilePart) and isinstance(p.root.file, FileWithBytes):
                    return base64.b64decode(p.root.file.bytes)
    return None


# -- fake brains (stand in for agent.run; no API) --------------------------------

async def _skeleton_brain(handle, files, settings, transport, emit):
    return b"\x00\x01\x02\x03"


async def _handshake_brain(handle, files, settings, transport, emit):
    """Write a candidate and test it against the green once, then submit a final PoC."""
    poc = Path(handle.task_dir) / "poc"
    poc.write_bytes(b"CANDIDATE")
    verdict = await transport.submit(str(poc))   # emits test_vulnerable, awaits green reply
    assert verdict is not None and verdict.crashed
    return b"FINALPOC"


def test_initial_to_artifact_no_handshake():
    """Brain that submits nothing -> executor still emits the poc artifact, no test round-trip."""
    async def go():
        ex = Executor(brain=_skeleton_brain, settings=object())
        eq = CollectEQ()
        await ex.execute(FakeCtx(_initial_message(), "t1", "c1"), eq)
        return eq.events

    events = asyncio.run(go())
    poc = _artifact_bytes(events)
    assert poc == b"\x00\x01\x02\x03"
    assert not any(_is_test_request(e) for e in events)


def test_test_vulnerable_handshake():
    """Brain submits to the green; we (green) reply on the same context -> 2nd execute()."""
    async def go():
        ex = Executor(brain=_handshake_brain, settings=object())
        eq1 = CollectEQ()
        worker = asyncio.create_task(ex.execute(FakeCtx(_initial_message(), "t1", "c1"), eq1))

        for _ in range(400):
            if any(_is_test_request(e) for e in eq1.events):
                break
            await asyncio.sleep(0.005)
        else:
            worker.cancel()
            raise AssertionError("executor never emitted test_vulnerable")

        eq2 = CollectEQ()
        await ex.execute(FakeCtx(
            _reply_message({"exit_code": 1, "output": "AddressSanitizer: heap-buffer-overflow"}),
            "t1b", "c1"), eq2)
        await asyncio.wait_for(worker, timeout=10)
        return eq1.events

    events = asyncio.run(go())
    assert any(_is_test_request(e) for e in events)
    assert _artifact_bytes(events) == b"FINALPOC"
