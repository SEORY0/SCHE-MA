"""A2A executor — mirrors the cybergym-alpha (#1) protocol, with SCHE-MA seams.

Protocol (negotiated with the CyberGym green agent):
  1. Green sends initial message: TextPart(prompt) + FilePart(repo-vul.tar.gz) +
     optional FilePart(description.txt/error.txt/repo-fix.tar.gz/patch.diff/README.md).
  2. Purple writes files (A2ATaskSource), then the brain generates a PoC. The brain owns the
     green round-trip: its generate stage submits via test_vulnerable (A2AGreenSubmit) to get
     crash feedback and repair.
  3. test_vulnerable is a non-final TaskStatusUpdateEvent carrying DataPart({"action":...}) +
     FilePart(poc). Green runs it and replies with a new user message DataPart({"exit_code",
     "output"}) — a SECOND execute() call, bridged to the in-flight worker via the session queue.
  4. Purple submits the final PoC as a TaskArtifactUpdateEvent (FilePart name="poc"), then completes.

The brain is pluggable (default: agent.run, the real claude_api pipeline); tests inject a
fake brain to exercise the plumbing offline.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import shutil
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
from .agent import SKELETON_POC, run as default_brain

logger = logging.getLogger(__name__)

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
    def __init__(self, brain=None, settings=None) -> None:
        self._sessions: dict[str, Session] = {}
        self._brain = brain or default_brain
        self._settings = settings  # lazy-loaded on first task if None

    def _get_settings(self):
        if self._settings is None:
            from ..config import load_settings
            self._settings = load_settings()
        return self._settings

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

        # Per-task scratch dir (repo-vul is extracted here). MUST be removed after the task —
        # otherwise 49 untarred repos accumulate in the container and fill the runner disk
        # ("No space left on device"), especially at high num_workers. See finally.
        run_dir = Path(tempfile.mkdtemp(prefix="schemata_a2a_"))
        try:
            handle = await A2ATaskSource(files, text).materialize(run_dir)
            files.clear()  # repo bytes are on disk now; the brain reads from handle.task_dir,
                           # not from this dict — free the RAM (repo-vul can be 100s of MB).

            await self._emit_status(event_queue, task_id, context_id, TaskState.working,
                                    f"Task {handle.label} level={handle.level}; generating PoC…", final=False)

            # Seam 2: the brain submits PoCs to the green via test_vulnerable for crash feedback.
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

            async def emit_progress(msg: str) -> None:
                await self._emit_status(event_queue, task_id, context_id, TaskState.working, msg, final=False)

            transport = A2AGreenSubmit(emit_test, sess.reply_queue)
            try:
                poc = await self._brain(handle, files, self._get_settings(), transport, emit_progress)
            except Exception as e:  # never fail the A2A task — submit a fallback so the green still scores it
                logger.exception("brain failed")
                await emit_progress(f"brain error: {e}; submitting fallback PoC")
                poc = SKELETON_POC

            await self._submit_artifact(event_queue, task_id, context_id, poc)
            await self._emit_status(event_queue, task_id, context_id, TaskState.completed,
                                    f"Submitted PoC ({len(poc)} bytes)", final=True)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

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
