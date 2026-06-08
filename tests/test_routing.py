"""Adaptive generate-stage routing (config.difficulty_from_signals) + retarget escalation.

Re-activates the Opus tier in the arena (the A2A plan was hardwired 'medium' so by_difficulty.hard
never fired) and escalates model+thinking on no_crash retargets — without burning Opus on FPs.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from schemata.a2a import agent as brain_mod
from schemata.config import load_settings


def test_difficulty_from_signals():
    f = load_settings().difficulty_from_signals
    hi = {"localization": {"overall_confidence": 0.9}}
    assert f({"crash_type": "heap-buffer-overflow"}, hi) == "medium"
    assert f({"crash_type": "use-after-free"}, hi) == "hard"           # hard crash class
    assert f({"crash_type": "type-confusion"}, {}) == "hard"
    assert f({"crash_type": "heap-buffer-overflow"},
             {"localization": {"overall_confidence": 0.3}}) == "hard"  # low-confidence localization
    assert f({"crash_type": "heap-buffer-overflow", "harness": {"seed_candidates": [{"path": "x"}]}},
             hi) == "easy"                                             # seed + high confidence
    assert f({}, {}) == "medium"                                       # missing signals -> prior behavior
    assert f(None, None) == "medium"                                   # malformed -> safe


class _ModelRecordingBackend:
    """Records the model on each generate request + the stage sequence; generate always crashes."""
    def __init__(self, verdicts):
        self.stages: list[str] = []
        self.gen_models: list[str] = []
        self._v = list(verdicts)

    async def run_stage(self, req):
        from schemata.models import Artifacts, StageResult, SubmissionRecord
        self.stages.append(req.stage)
        if req.stage == "generate":
            self.gen_models.append(req.model)
            (Path(req.cwd) / "poc").write_bytes(b"CRASHDATA")
            return StageResult(stage="generate", artifacts=Artifacts(submissions=[
                SubmissionRecord(poc_path="poc", exit_code=1, output_excerpt="ASan: heap-buffer-overflow")]))
        if req.stage == "discriminate":
            v = self._v.pop(0) if self._v else {"verdict": "ACCEPT"}
            return StageResult(stage="discriminate", structured_output=v)
        return StageResult(stage=req.stage)


async def _noop(_p):
    return None


def _run(tmp_path, backend, monkeypatch):
    import schemata.backends.claude_api as api_mod
    monkeypatch.setattr(api_mod, "ClaudeApiBackend", lambda s: backend)
    (tmp_path / "description.txt").write_text("heap-buffer-overflow in foo")
    handle = SimpleNamespace(task_dir=tmp_path, label="t", masked_id=None, level="level1",
                             agent_id=None, checksum=None, server_url=None)
    return asyncio.run(brain_mod.run(handle, {}, load_settings(),
                                     transport=SimpleNamespace(submit=_noop), emit=None))


def test_escalates_generate_to_opus_on_no_crash_retarget(tmp_path, monkeypatch):
    backend = _ModelRecordingBackend([
        {"verdict": "REJECT", "submit_decision": "REGENERATE", "failure_class": "no_crash"},
        {"verdict": "ACCEPT"},
    ])
    _run(tmp_path, backend, monkeypatch)
    assert backend.gen_models[0] == "sonnet"   # first try cheap (medium signals)
    assert backend.gen_models[1] == "opus"     # no_crash retarget -> escalated to opus


def test_no_escalation_on_any_crash_generic(tmp_path, monkeypatch):
    # any_crash_generic = referee caught a false positive; do NOT burn Opus chasing it.
    backend = _ModelRecordingBackend([
        {"verdict": "REJECT", "submit_decision": "REGENERATE", "failure_class": "any_crash_generic"},
        {"verdict": "ACCEPT"},
    ])
    _run(tmp_path, backend, monkeypatch)
    assert backend.gen_models == ["sonnet", "sonnet"]
