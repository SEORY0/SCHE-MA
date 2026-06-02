"""Seam 2 — PoC submit/verify transport.

Two implementations behind one `SubmitTransport`:
- LocalHttpSubmit: dev mode — wraps SubmitClient (POST /submit-vul to a local server).
- A2AGreenSubmit: AgentBeats mode — the green agent's in-conversation `test_vulnerable`
  round-trip. We emit a non-final status update carrying DataPart({"action":"test_vulnerable"})
  + FilePart(poc); the green runs it and replies (a 2nd execute() call) with
  {exit_code, output}, delivered here via an asyncio.Queue owned by the executor session.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Awaitable, Callable, Optional, Protocol

from ..models import Verdict

# Substrings in green output that indicate a sanitizer crash even if exit_code is ambiguous.
_CRASH_KW = ("sanitizer", "runtime error", "segmentation", "aborted", "addresssanitizer")


class SubmitTransport(Protocol):
    async def submit(self, poc_path) -> Optional[Verdict]: ...


class LocalHttpSubmit:
    """Dev mode: wrap the synchronous SubmitClient in a thread."""

    def __init__(self, client):
        self._client = client

    async def submit(self, poc_path) -> Optional[Verdict]:
        return await asyncio.to_thread(self._client.submit, poc_path)


class A2AGreenSubmit:
    """AgentBeats mode: test the PoC via the green's test_vulnerable round-trip.

    `emit_test(poc_bytes)` enqueues the test_vulnerable status update on the live
    A2A event queue; `reply_queue` is the executor session's queue that the green's
    reply message is pushed onto.
    """

    def __init__(
        self,
        emit_test: Callable[[bytes], Awaitable[None]],
        reply_queue: "asyncio.Queue",
        timeout: float = 600.0,
    ):
        self._emit_test = emit_test
        self._q = reply_queue
        self._timeout = timeout

    async def submit(self, poc_path) -> Optional[Verdict]:
        poc = Path(poc_path).read_bytes()
        await self._emit_test(poc)
        try:
            fb = await asyncio.wait_for(self._q.get(), self._timeout)
        except asyncio.TimeoutError:
            return None
        ec = fb.get("exit_code")
        out = fb.get("output") or ""
        low = out.lower()
        crashed = (ec not in (0, None)) or any(k in low for k in _CRASH_KW)
        # Verdict.crashed is derived from exit_code (!= 0). When the sanitizer output
        # signals a crash but the green reported exit_code 0/None (libFuzzer quirk),
        # promote to 1 so the agent's crash decision matches the evidence
        # (mirrors cybergym-alpha). Authoritative scoring is still the green's via the artifact.
        exit_code = ec if (ec not in (0, None)) else (1 if crashed else 0)
        return Verdict(exit_code=exit_code, output=out, poc_id=fb.get("poc_id"))
