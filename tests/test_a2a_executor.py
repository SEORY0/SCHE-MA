"""Integration test for the A2A executor — drives it as a mock CyberGym green agent.

No HTTP, no API, no real green: we build A2A messages, call execute() directly with a
collecting fake event queue, and (for the handshake) feed the test_vulnerable reply as a
second execute() on the same context_id. Mirrors the real green protocol.
"""
import asyncio
import base64
from uuid import uuid4

from a2a.types import (DataPart, FilePart, FileWithBytes, Message, Part, Role,
                       TaskArtifactUpdateEvent, TaskStatusUpdateEvent, TextPart)

import schemata.a2a.executor as exmod
from schemata.a2a.executor import Executor


class CollectEQ:
    """Minimal EventQueue stand-in: collect enqueued events."""
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


def test_s1_initial_to_artifact_no_handshake(monkeypatch):
    """MAX_TEST_ITERS=0: initial message -> poc artifact directly (skeleton plumbing)."""
    monkeypatch.setattr(exmod, "MAX_TEST_ITERS", 0)

    async def go():
        ex = Executor()
        eq = CollectEQ()
        await ex.execute(FakeCtx(_initial_message(), "t1", "c1"), eq)
        return eq.events

    events = asyncio.run(go())
    poc = _artifact_bytes(events)
    assert poc is not None and len(poc) > 0           # a PoC artifact was submitted
    assert not any(_is_test_request(e) for e in events)  # no test round-trip at iters=0


def test_s2_test_vulnerable_handshake(monkeypatch):
    """MAX_TEST_ITERS=1: executor emits test_vulnerable, we (green) reply, it submits poc."""
    monkeypatch.setattr(exmod, "MAX_TEST_ITERS", 1)

    async def go():
        ex = Executor()
        eq1 = CollectEQ()
        worker = asyncio.create_task(ex.execute(FakeCtx(_initial_message(), "t1", "c1"), eq1))

        # Wait until the executor emits the test_vulnerable request (then it blocks on reply).
        for _ in range(400):
            if any(_is_test_request(e) for e in eq1.events):
                break
            await asyncio.sleep(0.005)
        else:
            worker.cancel()
            raise AssertionError("executor never emitted test_vulnerable")

        # Green reply on the SAME context_id -> a second execute() call.
        eq2 = CollectEQ()
        await ex.execute(FakeCtx(_reply_message({"exit_code": 1, "output": "AddressSanitizer: heap-buffer-overflow"}),
                                 "t1b", "c1"), eq2)
        await asyncio.wait_for(worker, timeout=10)
        return eq1.events

    events = asyncio.run(go())
    assert any(_is_test_request(e) for e in events)     # test round-trip happened
    poc = _artifact_bytes(events)
    assert poc is not None and len(poc) > 0             # final poc artifact submitted
