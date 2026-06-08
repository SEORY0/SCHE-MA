"""Tests for Stage 4 — the independent discriminator (src/schemata/discriminate.py) and
its retarget loop wired into the A2A brain (src/schemata/a2a/agent.py).

The discriminator is the FP killer: it judges whether an achieved crash is the bug named
in description.txt or an any-crash that also crashes the fix (scoring 0), and on REJECT it
drives a bounded extra generate round. These tests exercise the loop with a scripted
backend (no real Anthropic calls) against the REAL prompt_loader/build_request wiring.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from schemata.a2a import agent as brain_mod
from schemata.config import load_settings
from schemata.discriminate import parse_verdict


# ---- parse_verdict (normalization + fail-safe) -----------------------------------

def test_parse_verdict_accept():
    v = parse_verdict({"verdict": "ACCEPT"})
    assert v["accept"] is True and v["verdict"] == "ACCEPT"


def test_parse_verdict_reject():
    v = parse_verdict({"verdict": "REJECT", "submit_decision": "REGENERATE",
                       "failure_class": "any_crash_generic", "retarget_instruction": "hit bar()"})
    assert v["accept"] is False
    assert v["failure_class"] == "any_crash_generic"
    assert v["retarget_instruction"] == "hit bar()"


def test_parse_verdict_emit_decision_accepts():
    assert parse_verdict({"submit_decision": "EMIT_AS_FINAL"})["accept"] is True


def test_parse_verdict_empty_is_failsafe_accept():
    # An unparseable / missing verdict must NOT throw away a crash we already have.
    assert parse_verdict({})["accept"] is True
    assert parse_verdict(None)["accept"] is True


# ---- scripted backend ------------------------------------------------------------

class _DiscBackend:
    """Records each stage; generate optionally yields a crashing submission; discriminate
    returns scripted verdicts in order."""

    def __init__(self, verdicts, crash=True):
        self.stages: list[str] = []
        self._verdicts = list(verdicts)
        self._crash = crash

    async def run_stage(self, req):
        from schemata.models import Artifacts, StageResult, SubmissionRecord
        self.stages.append(req.stage)
        if req.stage == "generate":
            subs = []
            if self._crash:
                (Path(req.cwd) / "poc").write_bytes(b"CRASHDATA")
                subs = [SubmissionRecord(
                    poc_path="poc", exit_code=1,
                    output_excerpt="==1==ERROR: AddressSanitizer: heap-buffer-overflow in foo /a.c:9")]
            return StageResult(stage="generate", artifacts=Artifacts(submissions=subs))
        if req.stage == "discriminate":
            v = self._verdicts.pop(0) if self._verdicts else {"verdict": "ACCEPT"}
            return StageResult(stage="discriminate", structured_output=v)
        return StageResult(stage=req.stage)


async def _noop_submit(_poc):  # transport.submit — assigned but never called by the fake backend
    return None


def _handle(tmp_path: Path, level="level1"):
    return SimpleNamespace(task_dir=tmp_path, label="t", masked_id=None, level=level,
                           agent_id=None, checksum=None, server_url=None)


def _run(tmp_path, backend, monkeypatch, *, transport=True):
    import schemata.backends.claude_api as api_mod
    monkeypatch.setattr(api_mod, "ClaudeApiBackend", lambda settings: backend)
    (tmp_path / "description.txt").write_text("heap-buffer-overflow in foo()")
    settings = load_settings()  # real config: discriminate.enabled=true, max_retarget=1
    tp = SimpleNamespace(submit=_noop_submit) if transport else None
    return asyncio.run(brain_mod.run(_handle(tmp_path), {}, settings, transport=tp, emit=None))


# ---- the loop --------------------------------------------------------------------

def test_discriminate_accepts_first_crash(tmp_path, monkeypatch):
    backend = _DiscBackend(verdicts=[{"verdict": "ACCEPT", "submit_decision": "EMIT_AS_FINAL"}])
    poc = _run(tmp_path, backend, monkeypatch)
    assert backend.stages == ["recon", "analyze", "generate", "discriminate"]
    assert poc == b"CRASHDATA"          # emitted the accepted crash


def test_discriminate_reject_then_retarget_then_accept(tmp_path, monkeypatch):
    backend = _DiscBackend(verdicts=[
        {"verdict": "REJECT", "submit_decision": "REGENERATE", "failure_class": "any_crash_generic",
         "retarget_instruction": "achieved crash in main; hit foo() instead"},
        {"verdict": "ACCEPT"},
    ])
    poc = _run(tmp_path, backend, monkeypatch)
    # one extra generate + discriminate after the REJECT
    assert backend.stages == ["recon", "analyze", "generate", "discriminate", "generate", "discriminate"]
    assert poc == b"CRASHDATA"


def test_discriminate_stops_at_retarget_budget(tmp_path, monkeypatch):
    # All rounds REJECT -> bounded by max_retarget=2 -> still emit the best crash we have.
    backend = _DiscBackend(verdicts=[
        {"verdict": "REJECT", "submit_decision": "REGENERATE"},
        {"verdict": "REJECT", "submit_decision": "REGENERATE"},
        {"verdict": "REJECT", "submit_decision": "REGENERATE"},
    ])
    poc = _run(tmp_path, backend, monkeypatch)
    assert backend.stages == ["recon", "analyze", "generate", "discriminate",
                              "generate", "discriminate", "generate", "discriminate"]
    assert poc == b"CRASHDATA"          # a crash still beats the skeleton


def test_no_crash_skips_discriminate(tmp_path, monkeypatch):
    backend = _DiscBackend(verdicts=[], crash=False)
    poc = _run(tmp_path, backend, monkeypatch)
    assert backend.stages == ["recon", "analyze", "generate"]   # nothing to judge
    assert poc == brain_mod.SKELETON_POC


class _RaisingDiscBackend(_DiscBackend):
    """generate crashes normally, but the discriminate stage blows up."""
    async def run_stage(self, req):
        if req.stage == "discriminate":
            self.stages.append(req.stage)
            raise RuntimeError("boom")
        return await super().run_stage(req)


def test_discriminate_error_keeps_best_crash(tmp_path, monkeypatch):
    # Hard invariant: a referee failure must NOT downgrade the outcome to the skeleton.
    backend = _RaisingDiscBackend(verdicts=[])
    poc = _run(tmp_path, backend, monkeypatch)
    assert backend.stages == ["recon", "analyze", "generate", "discriminate"]
    assert poc == b"CRASHDATA"          # crash from generate is preserved, not lost
    assert poc != brain_mod.SKELETON_POC


def test_no_transport_skips_discriminate(tmp_path, monkeypatch):
    # Local/no-feedback mode (transport=None): the referee needs submit feedback, so skip.
    backend = _DiscBackend(verdicts=[{"verdict": "ACCEPT"}])
    poc = _run(tmp_path, backend, monkeypatch, transport=False)
    assert backend.stages == ["recon", "analyze", "generate"]
    assert poc == b"CRASHDATA"          # still emits the crash from generate
