"""Unit tests for the A2A brain (src/schemata/legacy/a2a/agent.py — M6-b).

The executor-level integration is covered by test_a2a_executor.py with fake brains.
Here we exercise the real `run()` plumbing in isolation: backend + prompt_loader are
patched so no API calls happen, but the recon -> generate stage flow, the submit_fn
wiring, and the PoC extraction (artifacts.poc_path -> last submission -> fallback)
are all driven end-to-end on the actual module.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from schemata.core.models import Artifacts, StageResult, SubmissionRecord
from schemata.legacy.a2a.agent import (
    SKELETON_POC,
    _a2a_plan,
    _read_poc,
    run,
    run_skeleton,
)

pytestmark = pytest.mark.skip(
    reason="arena/a2a retired; moved to schemata.legacy (local CyberGym only)")


# ---- run_skeleton (M6-a fallback) ------------------------------------------------

def test_run_skeleton_returns_placeholder():
    out = asyncio.run(run_skeleton(handle=None, files={}))
    assert out == SKELETON_POC
    assert len(SKELETON_POC) == 8


# ---- _a2a_plan -------------------------------------------------------------------

def test_a2a_plan_shape():
    settings = SimpleNamespace(model_for=lambda stage, diff: f"{stage}-{diff}")
    plan = _a2a_plan(settings, difficulty="hard")
    assert plan.stages == ["recon", "analyze", "generate"]
    assert plan.difficulty == "hard"
    assert plan.stage_models == {
        "recon": "recon-hard", "analyze": "analyze-hard", "generate": "generate-hard",
    }
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
    s1 = tmp_path / "s1.bin"
    s1.write_bytes(b"S1")
    s2 = tmp_path / "s2.bin"
    s2.write_bytes(b"S2")
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
    import schemata.pipeline.prompt_loader as pl_mod

    monkeypatch.setattr(api_mod, "ClaudeApiBackend", lambda settings: backend)

    def _fake_build(stage, plan, meta, handle, prior, settings, backend_name, **kw):
        from schemata.core.models import StageRequest
        return StageRequest(
            stage=stage,
            system_prompt="sys", kickoff="go",
            cwd=Path(handle.task_dir), model=plan.stage_models[stage],
            allowed_tools=[], permission_tier="read_only",
        )
    monkeypatch.setattr(pl_mod, "build_request", _fake_build)


def _settings(arena=None):
    return SimpleNamespace(model_for=lambda stage, diff: f"{stage}-model", arena=arena or {})


def test_assume_level1_bypasses_level3_fastpath(tmp_path: Path, monkeypatch):
    """Default [arena].assume_level1=true: a level3 task still runs the full level1
    pipeline (Haiku recon -> analyze -> generate); the mechanical fast-path is NOT used."""
    import schemata.legacy.a2a.agent as agent_mod
    called = []
    monkeypatch.setattr(agent_mod, "extract_level3_recon",
                        lambda d: called.append(d) or {"crash_type": "x", "suspected_files": ["f"]})
    poc = tmp_path / "p.bin"
    poc.write_bytes(b"PWN")
    results = {
        "recon": StageResult(stage="recon", structured_output={"summary": "r"}),
        "analyze": StageResult(stage="analyze"),
        "generate": StageResult(stage="generate", stop_reason="crash_found",
                                artifacts=Artifacts(poc_path=str(poc))),
    }
    fb = _FakeBackend(results)
    _patch_brain(monkeypatch, fb)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m", level="level3")

    out = asyncio.run(run(handle, {}, _settings(), transport=None))  # default arena={} -> assume_level1
    assert out == b"PWN"
    assert called == []                                       # extract_level3_recon NOT called
    assert [c.stage for c in fb.calls] == ["recon", "analyze", "generate"]  # full level1 pipeline


def test_level3_fastpath_when_assume_level1_false(tmp_path: Path, monkeypatch):
    """[arena].assume_level1=false re-enables the level3 fast-path: mechanical recon,
    recon+analyze skipped, straight to generate."""
    import schemata.legacy.a2a.agent as agent_mod
    monkeypatch.setattr(agent_mod, "extract_level3_recon",
                        lambda d: {"crash_type": "x", "suspected_files": ["f"]})
    poc = tmp_path / "p.bin"
    poc.write_bytes(b"PWN")
    results = {"generate": StageResult(stage="generate", stop_reason="crash_found",
                                       artifacts=Artifacts(poc_path=str(poc)))}
    fb = _FakeBackend(results)
    _patch_brain(monkeypatch, fb)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m", level="level3")

    out = asyncio.run(run(handle, {}, _settings({"assume_level1": False}), transport=None))
    assert out == b"PWN"
    assert [c.stage for c in fb.calls] == ["generate"]        # recon+analyze skipped


def test_run_returns_poc_bytes_from_generate(tmp_path: Path, monkeypatch):
    poc = tmp_path / "final.bin"
    poc.write_bytes(b"PWN")
    results = {
        "recon": StageResult(stage="recon", structured_output={"summary": "heap-bof"}),
        "analyze": StageResult(stage="analyze", structured_output={"plan": "len=1 LOOP"}),
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
    assert stages == ["recon", "analyze", "generate"]
    # no transport -> submit_fn stays unset
    gen_req = next(c for c in fb.calls if c.stage == "generate")
    assert gen_req.submit_fn is None
    # generate stage saw recon's structured_output threaded in
    # (the patched build_request ignores `prior`, but run() must still pass it; the
    # real check is that emit was called for each stage)
    assert len(emits) == 5  # 3 stage starts + 1 generate-summary + 1 metrics line
    assert emits[-1].startswith("metrics:")  # per-task METRICS summary always emitted last


def test_run_wires_submit_fn_when_transport_set(tmp_path: Path, monkeypatch):
    results = {
        "recon": StageResult(stage="recon"),
        "analyze": StageResult(stage="analyze"),
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
        "analyze": StageResult(stage="analyze"),
        "generate": StageResult(stage="generate", artifacts=Artifacts()),  # no poc, no submissions
    }
    _patch_brain(monkeypatch, _FakeBackend(results))

    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m")
    out = asyncio.run(run(handle, {}, _settings(), transport=None, emit=None))
    assert out == SKELETON_POC


class _SeqBackend:
    """Returns canned results per stage in order — each stage call advances the queue
    for that stage. Used to model 'second generate call returns a PoC' after the
    no-PoC retry promotes to opus."""
    def __init__(self, by_stage_seq):
        self.by_stage_seq = {k: list(v) for k, v in by_stage_seq.items()}
        self.calls = []

    async def run_stage(self, req):
        self.calls.append(req)
        return self.by_stage_seq[req.stage].pop(0)


def test_run_retries_with_opus_when_no_poc_and_transport_set(tmp_path: Path, monkeypatch):
    """First generate produces no PoC; the safety net re-runs generate with opus, and the
    retry attempt yields a winning PoC that becomes the returned bytes."""
    retry_poc = tmp_path / "retry.bin"
    retry_poc.write_bytes(b"WIN")
    backend = _SeqBackend({
        "recon":   [StageResult(stage="recon", structured_output={"summary": "heap"})],
        "analyze": [StageResult(stage="analyze", structured_output={"plan": "p"})],
        "generate": [
            StageResult(stage="generate", artifacts=Artifacts()),                  # 1st: no PoC
            StageResult(stage="generate", artifacts=Artifacts(poc_path=str(retry_poc))),  # 2nd: PoC
        ],
    })
    _patch_brain(monkeypatch, backend)

    transport = SimpleNamespace(submit=lambda *a, **k: None)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m")

    out = asyncio.run(run(handle, {}, _settings(), transport=transport, emit=None))
    assert out == b"WIN"

    stages = [c.stage for c in backend.calls]
    # original 3 stages + retry generate (analyze already in plan, so retry skips it)
    assert stages == ["recon", "analyze", "generate", "generate"]
    # retry generate must use opus
    assert backend.calls[-1].model == "opus"


def test_run_retries_with_opus_when_poc_but_no_crash(tmp_path: Path, monkeypatch):
    """② fix: a PoC that produced bytes but NEVER crashed must still trigger the Opus retry.
    The old `poc is None` gate self-suppressed it (because _read_poc returns the non-crashing
    PoC's bytes), so exactly the hard tasks needing Opus blocked their own escalation. This
    test FAILS on the old gate (no retry -> out == b"NOCRASH") and passes on the crash-based one."""
    import schemata.pipeline.discriminate as disc_mod
    monkeypatch.setattr(disc_mod, "discriminate_enabled", lambda s: False)  # isolate the retry gate

    cand = tmp_path / "cand.bin"
    cand.write_bytes(b"NOCRASH")
    win = tmp_path / "win.bin"
    win.write_bytes(b"WIN")
    backend = _SeqBackend({
        "recon":   [StageResult(stage="recon",
                                structured_output={"vuln_classes": ["heap-buffer-overflow-read"]})],
        "analyze": [StageResult(stage="analyze", structured_output={})],
        "generate": [
            # 1st: PoC bytes exist but the submission did NOT crash (exit_code 0).
            StageResult(stage="generate", stop_reason="early_stop", artifacts=Artifacts(
                poc_path=str(cand),
                submissions=[SubmissionRecord(poc_path=str(cand), exit_code=0)])),
            # 2nd (opus retry): finally crashes.
            StageResult(stage="generate", stop_reason="crash_found", artifacts=Artifacts(
                poc_path=str(win),
                submissions=[SubmissionRecord(poc_path=str(win), exit_code=1)])),
        ],
    })
    _patch_brain(monkeypatch, backend)
    transport = SimpleNamespace(submit=lambda *a, **k: None)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m")

    out = asyncio.run(run(handle, {}, _settings(), transport=transport, emit=None))
    assert out == b"WIN"                                            # retry's crashing PoC wins
    stages = [c.stage for c in backend.calls]
    assert stages == ["recon", "analyze", "generate", "generate"]  # retry generate fired
    assert backend.calls[-1].model == "opus"                       # escalated to opus


def test_run_skips_retry_when_no_transport(tmp_path: Path, monkeypatch):
    """No transport -> retry must NOT fire (would break local/offline modes that intentionally
    skip the green submit round-trip)."""
    backend = _SeqBackend({
        "recon":    [StageResult(stage="recon")],
        "analyze":  [StageResult(stage="analyze")],
        "generate": [StageResult(stage="generate", artifacts=Artifacts())],  # no PoC
    })
    _patch_brain(monkeypatch, backend)
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t", masked_id="m")

    out = asyncio.run(run(handle, {}, _settings(), transport=None, emit=None))
    assert out == SKELETON_POC

    stages = [c.stage for c in backend.calls]
    assert stages == ["recon", "analyze", "generate"]  # no retry generate


def test_metrics_line_reports_atomic_and_poc_no_crash(tmp_path: Path, monkeypatch, capsys):
    """The per-task METRICS breadcrumb must surface the measure-first signals so a flat
    arena run can be attributed: atomic_examples (did vuln_classes reach generate),
    gen_model (was a hard task stuck on sonnet), and poc_no_crash (bytes produced but no
    crash — the state where the `poc is None` retry gate wrongly suppresses Opus)."""
    poc = tmp_path / "cand.bin"
    poc.write_bytes(b"NOCRASH")
    results = {
        "recon": StageResult(stage="recon",
                             structured_output={"vuln_classes": ["heap-buffer-overflow-read"]}),
        "analyze": StageResult(stage="analyze", structured_output={}),
        "generate": StageResult(stage="generate", stop_reason="early_stop",
                                artifacts=Artifacts(
                                    poc_path=str(poc),
                                    submissions=[SubmissionRecord(poc_path=str(poc), exit_code=0)])),
    }
    _patch_brain(monkeypatch, _FakeBackend(results))
    handle = SimpleNamespace(task_dir=str(tmp_path), label="t-metrics", masked_id="m")

    out = asyncio.run(run(handle, {}, _settings(), transport=None, emit=None))
    assert out == b"NOCRASH"  # a non-crashing PoC's bytes are still returned (and would block retry)

    line = next(ln for ln in capsys.readouterr().err.splitlines() if "METRICS" in ln)
    m = json.loads(line.split("METRICS", 1)[1])
    assert m["atomic_examples"] == "YES"          # vuln_classes flowed -> recipes injected
    assert m["gen_model"] == "generate-model"     # from _settings().model_for; proves model routing
    assert m["crashes"] == 0
    assert m["poc_no_crash"] is True              # bytes but no crash -> the ② Opus-suppression signal
    assert m["skeleton"] is False
