"""The agent 'brain' invoked by the A2A executor.

M6-b: `run()` drives the real claude_api recon->generate pipeline on the green-supplied
task. In the arena the purple container has no target binary/docker, so the only crash
feedback is the green's `test_vulnerable` round-trip — the generate stage's submit_poc
tool is wired to it (via StageRequest.submit_fn = transport.submit), giving the agent a
real submit/repair loop. Returns the winning PoC bytes (falls back to a placeholder so the
A2A task still completes if generation yields nothing).

`run_skeleton` / SKELETON_POC remain as the deterministic fallback.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..models import PipelinePlan, TaskMeta

logger = logging.getLogger(__name__)

# Deterministic placeholder PoC (fallback only).
SKELETON_POC = bytes(range(8))


async def run_skeleton(handle, files: dict[str, bytes]) -> bytes:
    """Return placeholder PoC bytes (M6-a fallback)."""
    return SKELETON_POC


def _a2a_plan(settings, difficulty: str = "medium") -> PipelinePlan:
    """Arena route: recon (find the bug) -> generate (craft + submit to green). No local
    instrument/MCP — the purple container has no target image; crash feedback is the green's."""
    return PipelinePlan(
        difficulty=difficulty,
        stages=["recon", "generate"],
        stage_models={
            "recon": settings.model_for("recon", difficulty),
            "generate": settings.model_for("generate", difficulty),
        },
        has_instrument=False, has_mcp_index=False, thinking=False, minimize_info=False,
    )


def _read_poc(handle, res) -> bytes | None:
    """Best PoC bytes from a generate StageResult: the crashing winner, else the last submission."""
    cand = res.artifacts.poc_path
    if not cand and res.artifacts.submissions:
        cand = res.artifacts.submissions[-1].poc_path
    if not cand:
        return None
    p = Path(cand)
    p = p if p.is_absolute() else (Path(handle.task_dir) / cand)
    return p.read_bytes() if p.is_file() else None


async def run(handle, files, settings, transport=None, emit=None) -> bytes:
    """Run recon->generate on the claude_api backend; return the winning PoC bytes.

    transport: the green submit transport (A2AGreenSubmit). When set, the generate stage's
    submit_poc tests/repairs against the green. emit: optional async status reporter.
    """
    from .. import prompt_loader
    from ..backends.claude_api import ClaudeApiBackend

    backend = ClaudeApiBackend(settings)
    meta = TaskMeta(task_id=handle.label, difficulty_estimate="medium")
    plan = _a2a_plan(settings)
    prior: dict[str, dict] = {}
    poc: bytes | None = None

    for stage in plan.stages:
        req = prompt_loader.build_request(stage, plan, meta, handle, prior, settings, "claude_api")
        if stage == "generate" and transport is not None:
            req.submit_fn = transport.submit          # submit_poc -> green test_vulnerable
        if emit:
            await emit(f"stage {stage} ({req.model})…")
        res = await backend.run_stage(req)
        prior[stage] = res.structured_output
        if stage == "generate":
            poc = _read_poc(handle, res)
            if emit:
                await emit(f"generate {res.stop_reason}: {len(res.artifacts.submissions)} test(s)")

    return poc or SKELETON_POC
