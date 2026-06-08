"""Unit tests for the A2A brain (src/schemata/a2a/agent.py — M6-b).

The executor-level integration is covered by test_a2a_executor.py with fake brains.
Here we exercise the real `run()` plumbing in isolation: backend + prompt_loader are
patched so no API calls happen, but the recon -> generate stage flow, the submit_fn
wiring, and the PoC extraction (artifacts.poc_path -> last submission -> fallback)
are all driven end-to-end on the actual module.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from schemata.a2a import agent as brain_mod
from schemata.a2a.agent import (SKELETON_POC, _a2a_plan, _read_poc, run,
                                run_skeleton)
from schemata.models import Artifacts, StageResult, SubmissionRecord


# ---- run_skeleton (M6-a fallback) ------------------------------------------------

def test_run_skeleton_returns_placeholder():
    out = asyncio.run(run_skeleton(handle=None, files={}))
    assert out == SKELETON_POC
    assert len(SKELETON_POC) == 8


# ---- _a2a_plan -------------------------------------------------------------------

def test_a2a_plan_shape():
    settings = SimpleNamespace(model_for=lambda stage, diff: f"{stage}-{diff}")
    plan = _a2a_plan(settings, difficulty="hard")
    assert plan.stages == ["recon", "generate"]
    assert plan.difficulty == "hard"
    assert plan.stage_models == {"recon": "recon-hard", "generate": "generate-hard"}
    # arena has no local instrument / mcp / minimization
    assert plan.has_instrument is False
    assert plan.has_mcp_index is False
    assert plan.thinking is False
    assert plan.minimize_info is False


def test_a2a_plan_defaults_medium():
    settings = SimpleNamespace(model_for=lambda stage, diff: diff)
    assert _a2a_plan(settings).difficulty == "medium"


# ---- _read_poc -------------------------------------------------------------------

def test_read_poc_absolute(tmp_path: Path):
    poc = tmp_path / "winner.bin"
    poc.write_bytes(b"WIN")
    res = StageResult(stage="generate", artifacts=Artifacts(poc_path=str(poc)))
    handle = SimpleNamespace(task_dir=str(tmp_path / "other"))
    assert _read_poc(handle, res) == b"WIN"


def test_read_poc_relative_to_task_dir(tmp_path: Path):
    (tmp_path / "poc.bin").write_bytes(b"REL")
    res = StageResult(stage="generate", artifacts=Artifacts(poc_path="poc.bin"))
    handle = SimpleNamespace(task_dir=str(tmp_path))
    assert _read_poc(handle, res) == b"REL"


def test_read_poc_falls_back_to_last_submission(tmp_path: Path):
    s1 = tmp_path / "s1.bin"; s1.write_bytes(b"S1")
    s2 = tmp_path / "s2.bin"; s2.write_bytes(b"S2")
    res = StageResult(stage="generate", artifacts=Artifacts(
        poc_path=None,
        submissions=[
            SubmissionRecord(poc_path=str(s1)),
            SubmissionRecord(poc_path=str(s2)),
        ],
    ))
    handle = SimpleNamespace(task_dir=str(tmp_path))
    assert _read_poc(handle, res) == b"S2"


def test_read_poc_none_when_no_candidates():
    res = StageResult(stage="generate", artifacts=Artifacts())
    assert _read_poc(SimpleNamespace(task_dir="/nope"), res) is None


def test_read_poc_none_when_path_missing(tmp_path: Path):
    res = StageResult(stage="generate",
                      artifacts=Artifacts(poc_path=str(tmp_path / "ghost.bin")))
    assert _read_poc(SimpleNamespace(task_dir=str(tmp_path)), res) is None


# ---- run() — drives the real recon->generate flow with patched backend -----------

class _FakeBackend:
    """Records each StageRequest and returns canned StageResults per stage."""
    def __init__(self, results_by_stage):
        self.results = results_by_stage
        self.calls: list = []

    def __init_with_settings__(self, settings):  # noqa
        return self

    async def run_stage(self, req):
        self.calls.append(req)
        return self.results[req.stage]


def _patch_brain(monkeypatch, backend, request_factory=None):
    """Patch ClaudeApiBackend(settings) -> backend, and prompt_loader.build_request."""
    import schemata.backends.claude_api as api_mod
    import schemata.prompt_loader as pl_mod

    monkeypatch.setattr(api_mod, "ClaudeApiBackend", lambda settings: backend)

    def _fake_build(stage, plan, meta, handle, prior, settings, backend_name, **kw):
        from schemata.models import StageRequest
        return StageRequest(
            stage=stage,
            system_prompt="sys", kickoff="go",
            cwd=Path(handle.task_dir), model=plan.stage_models[stage],
            allowed_tools=[], permission_tier="read_only",
        )
    monkeypatch.setattr(pl_mod, "build_request", _fake_build)


def _settings():
    return SimpleNamespace(model_for=lambda stage, diff: f"{stage}-model")


def test_run_returns_poc_bytes_from_generate(tmp_path: Path, monkeypatch):
    poc = tmp_path / "final.bin"; poc.write_bytes(b"PWN")
    results = {
        "recon": StageResult(stage="recon", structured_output={"summary": "heap-bof"}),
        "generate": StageResult(stage="generate",
                                stop_reason="crash_found",
                                artifacts=Artifacts(poc_path=str(poc))),
    }
    fb = _FakeBackend(results)
    _patch_brain(monkeypatch, fb)

    handle = SimpleNamespace(task_dir=str(tmp_path), label="task-1", masked_id="m1")
    emits = []
    async def emit(msg): emits.append(msg)

    out = asyncio.run(run(handle, {}, _settings(), transport=None, emit=emit))
    assert out == b"PWN"

    stages = [c.stage for c in fb.calls]
    assert stages == ["recon", "generate"]
    # no transport -> submit_fn stays unset
    gen_req = next(c for c in fb.calls if c.stage == "generate")
    assert gen_req.submit_fn is None
    # generate stage saw recon's structured_output threaded in
    # (the patched build_request ignores `prior`, but run() must still pass it; the
    # real check is that emit was called for each stage)
    assert len(emits) == 3  # 2 stage starts + 1 generate-summary


def test_run_wires_submit_fn_when_transport_set(tmp_path: Path, monkeypatch):
    results = {
        "recon": StageResult(stage="recon"),
        "generate": StageResult(stage="generate",
                                artifacts=Artifacts(poc_path=str(tmp_path / "p"))),
    }
    (tmp_path / "p").write_bytes(b"X")
    fb = _FakeBackend(results)
    _patch_brain(monkeypatch, fb)

    sentinel_submit = object()
    transport = SimpleNamespace(submit=sentinel_submit)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m")

    asyncio.run(run(handle, {}, _settings(), transport=transport, emit=None))

    recon_req = next(c for c in fb.calls if c.stage == "recon")
    gen_req = next(c for c in fb.calls if c.stage == "generate")
    assert recon_req.submit_fn is None  # only generate gets the transport
    assert gen_req.submit_fn is sentinel_submit


def test_run_falls_back_to_skeleton_when_no_poc(tmp_path: Path, monkeypatch):
    results = {
        "recon": StageResult(stage="recon"),
        "generate": StageResult(stage="generate", artifacts=Artifacts()),  # no poc, no submissions
    }
    _patch_brain(monkeypatch, _FakeBackend(results))

    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m")
    out = asyncio.run(run(handle, {}, _settings(), transport=None, emit=None))
    assert out == SKELETON_POC
