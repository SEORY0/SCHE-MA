"""A2A executor — mirrors the cybergym-alpha (#1) protocol, with SCHE-MA seams.

Protocol (negotiated with the CyberGym green agent):
  1. Green sends initial message: TextPart(prompt) + FilePart(repo-vul.tar.gz) +
     optional FilePart(description.txt/error.txt/repo-fix.tar.gz/patch.diff/README.md).
  2. Purple writes files (A2ATaskSource), generates a PoC (agent.run_skeleton; M6-b: orchestrator).
  3. Purple OPTIONALLY tests via a non-final TaskStatusUpdateEvent carrying
     DataPart({"action":"test_vulnerable"}) + FilePart(poc). Green runs it and replies with
     a new user message DataPart({"exit_code":..,"output":..}) — a SECOND execute() call.
  4. Purple submits the final PoC as a TaskArtifactUpdateEvent (FilePart name="poc"), then completes.

The reply (step 3) is a separate execute() on the same context_id; we bridge it to the
in-flight worker via a per-context Session(asyncio.Queue).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from a2a.server.agent_execution import AgentExecutor
from a2a.types import (Artifact, DataPart, FilePart, FileWithBytes, Message, Part,
                       Role, TaskArtifactUpdateEvent, TaskState, TaskStatus,
                       TaskStatusUpdateEvent, TextPart)

from ..cybergym.intake import A2ATaskSource
from ..cybergym.transport import A2AGreenSubmit
from .agent import run_skeleton

logger = logging.getLogger(__name__)

# Test/repair iterations with the green. 0 = submit initial PoC without a test round-trip.
MAX_TEST_ITERS = int(os.environ.get("MAX_TEST_ITERS", "1"))

TERMINAL_STATES = {TaskState.completed, TaskState.canceled, TaskState.failed, TaskState.rejected}


@dataclass
class Session:
    reply_queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    done: asyncio.Event = field(default_factory=asyncio.Event)


def _extract_parts(message) -> tuple[str, dict[str, bytes], dict[str, Any] | None]:
    text = ""
    files: dict[str, bytes] = {}
    data: dict[str, Any] | None = None
    for part in (message.parts or []):
        root = part.root if hasattr(part, "root") else part
        if isinstance(root, TextPart):
            text += (root.text or "")
        elif isinstance(root, FilePart) and isinstance(root.file, FileWithBytes):
            name = root.file.name or f"file_{len(files)}"
            files[name] = base64.b64decode(root.file.bytes)
        elif isinstance(root, DataPart):
            data = root.data
    return text, files, data


class Executor(AgentExecutor):
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def execute(self, context, event_queue) -> None:
        message = context.message
        if not message or not message.parts:
            return
        task = context.current_task
        if task and task.status.state in TERMINAL_STATES:
            return

        task_id = context.task_id or uuid4().hex
        context_id = context.context_id or task_id
        text, files, data = _extract_parts(message)

        sess = self._sessions.get(context_id)
        if sess is None:
            sess = Session()
            self._sessions[context_id] = sess
            try:
                await self._run_full_task(event_queue, task_id, context_id, text, files, sess)
            finally:
                sess.done.set()
                self._sessions.pop(context_id, None)
            return

        # Continuation message (green's test_vulnerable reply) -> hand to in-flight worker.
        if data is not None:
            await sess.reply_queue.put(data)
        else:
            await sess.reply_queue.put({"output": text, "files": list(files.keys())})
        await self._emit_status(event_queue, task_id, context_id, TaskState.working, "ack", final=True)

    async def _run_full_task(self, event_queue, task_id, context_id, text, files, sess) -> None:
        await self._emit_status(event_queue, task_id, context_id,
                                TaskState.working, "Analysing task files…", final=False)

        run_dir = Path(tempfile.mkdtemp(prefix="schemata_a2a_"))
        handle = await A2ATaskSource(files, text).materialize(run_dir)

        await self._emit_status(event_queue, task_id, context_id, TaskState.working,
                                f"Task {handle.label} level={handle.level}; generating PoC (skeleton)…",
                                final=False)

        poc = await run_skeleton(handle, files)
        poc_path = handle.task_dir / "poc"
        poc_path.write_bytes(poc)

        # Seam 2: verify via the green's test_vulnerable round-trip.
        async def emit_test(poc_bytes: bytes) -> None:
            await self._emit_status(
                event_queue, task_id, context_id, TaskState.working,
                "Requesting test_vulnerable…", final=False,
                extra_parts=[
                    Part(root=DataPart(data={"action": "test_vulnerable"})),
                    Part(root=FilePart(file=FileWithBytes(
                        bytes=base64.b64encode(poc_bytes).decode("ascii"),
                        name="poc", mime_type="application/octet-stream"))),
                ],
            )

        transport = A2AGreenSubmit(emit_test, sess.reply_queue)
        for attempt in range(MAX_TEST_ITERS):
            verdict = await transport.submit(poc_path)
            if verdict is None:
                break  # green did not reply; submit what we have
            if verdict.crashed:
                await self._emit_status(event_queue, task_id, context_id, TaskState.working,
                                        f"PoC triggered on attempt {attempt + 1}.", final=False)
                break
            await self._emit_status(event_queue, task_id, context_id, TaskState.working,
                                    f"No crash (attempt {attempt + 1}); skeleton has no repair yet — submitting.",
                                    final=False)
            break  # M6-a skeleton: no repair brain (added in M6-b)

        await self._submit_artifact(event_queue, task_id, context_id, poc)
        await self._emit_status(event_queue, task_id, context_id, TaskState.completed,
                                f"Submitted PoC ({len(poc)} bytes)", final=True)

    async def _emit_status(self, event_queue, task_id, context_id, state, text,
                           *, final, extra_parts=None) -> None:
        parts = [Part(root=TextPart(kind="text", text=text))]
        if extra_parts:
            parts.extend(extra_parts)
        await event_queue.enqueue_event(TaskStatusUpdateEvent(
            taskId=task_id, contextId=context_id,
            status=TaskStatus(state=state, message=Message(
                messageId=uuid4().hex, role=Role.agent, parts=parts)),
            final=final,
        ))

    async def _submit_artifact(self, event_queue, task_id, context_id, poc: bytes) -> None:
        await event_queue.enqueue_event(TaskArtifactUpdateEvent(
            taskId=task_id, contextId=context_id,
            artifact=Artifact(artifactId=uuid4().hex, name="poc", parts=[
                Part(root=FilePart(file=FileWithBytes(
                    bytes=base64.b64encode(poc).decode("ascii"),
                    name="poc", mime_type="application/octet-stream")))]),
        ))

    async def cancel(self, context, event_queue) -> None:
        raise NotImplementedError("Cancellation not supported")
