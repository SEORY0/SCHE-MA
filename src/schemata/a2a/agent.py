"""The agent 'brain' invoked by the A2A executor.

M6-a (skeleton): returns a deterministic placeholder PoC so the full A2A round-trip
(task intake -> test_vulnerable -> poc artifact) can be exercised end-to-end against
the real green agent WITHOUT the real PoC-generation logic.

M6-b will replace `run_skeleton` with a call into orchestrator.run_task(...), injecting
A2ATaskSource + A2AGreenSubmit, running the real Stage 1-3 pipeline on the Claude API
backend.
"""
from __future__ import annotations

# Deterministic placeholder; bytes need not trigger a crash at skeleton stage.
SKELETON_POC = bytes(range(8))


async def run_skeleton(handle, files: dict[str, bytes]) -> bytes:
    """Return placeholder PoC bytes. `handle` is a cybergym.intake.TaskHandle."""
    return SKELETON_POC
