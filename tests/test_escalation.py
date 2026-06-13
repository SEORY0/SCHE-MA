"""Bounded escalation: when cheap recon fails to LOCALIZE, the orchestrator promotes the
analyze stage (stronger localizer) before generate — once. vuln_classes alone does not count
as localization (that's classification, recovered by the JSON-flush fallback).
"""
import asyncio
from types import SimpleNamespace

from schemata import orchestrator
from schemata.models import Artifacts, StageResult, TaskMeta, Usage


# ---- _recon_localized (pure decision) ---------------------------------------------------
def test_localized_true_on_suspected_file():
    assert orchestrator._recon_localized({"suspected_files": ["coders/png.c"]}) is True


def test_localized_true_on_harness_entry_point():
    assert orchestrator._recon_localized({"harness": {"entry_point": "LLVMFuzzerTestOneInput"}}) is True


def test_localized_false_on_empty():
    assert orchestrator._recon_localized({}) is False
    assert orchestrator._recon_localized(None) is False


def test_localized_false_when_only_vuln_classes():
    # classification without localization must NOT suppress escalation
    assert orchestrator._recon_localized({"vuln_classes": ["heap-buffer-overflow-read"]}) is False


# ---- end-to-end escalation through run_task ---------------------------------------------
class _FakeBackend:
    def __init__(self, by_stage):
        self.by_stage, self.seen = by_stage, []

    async def run_stage(self, req):
        self.seen.append(req.stage)
        return self.by_stage[req.stage]


class _FakeCost:
    def add(self, *a, **k): pass
    def over_task_soft_cap(self, t): return False
    def over_global_budget(self): return False
    def task_cost(self, t): return 0.0


class _FakeInstr:
    def __init__(self, *a, **k): pass
    def start(self, *a, **k): return None
    def cleanup(self, *a, **k): pass


def _result(stage, structured, stop="completed"):
    return StageResult(stage=stage, structured_output=structured, stop_reason=stop,
                       usage=Usage(model="haiku"), artifacts=Artifacts())


def _wire(monkeypatch, tmp_path, by_stage):
    def fake_gen_task(settings, task_id, task_dir):
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "description.txt").write_text("heap-buffer-overflow in ReadMNGImage")
        return SimpleNamespace(task_dir=task_dir, masked_id="m", agent_id="a",
                               checksum="c", server_url="http://x")

    backend = _FakeBackend(by_stage)
    monkeypatch.setattr(orchestrator, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(orchestrator.ids, "lookup",
                        lambda tid: TaskMeta(task_id=tid, difficulty_estimate="easy",
                                             project="graphicsmagick", crash_type="heap-buffer-overflow"))
    monkeypatch.setattr(orchestrator, "gen_task", fake_gen_task)
    monkeypatch.setattr(orchestrator, "make_backend", lambda name, s: backend)
    monkeypatch.setattr(orchestrator, "Instrumenter", _FakeInstr)
    monkeypatch.setattr(orchestrator, "_confirm_winner", lambda *a, **k: None)
    return backend


def test_thin_recon_promotes_analyze(tmp_path, monkeypatch):
    backend = _wire(monkeypatch, tmp_path, {
        "recon": _result("recon", {"vuln_classes": ["heap-buffer-overflow-read"]}, stop="max_turns"),
        "analyze": _result("analyze", {"suspected_files": ["coders/png.c"]}),
        "generate": _result("generate", {}, stop="crash_found"),
    })
    from schemata.config import load_settings
    outcome = asyncio.run(orchestrator.run_task("arvo:10400", "claude_api", load_settings(),
                                                _FakeCost(), "testrun"))

    assert outcome.escalated is True
    assert backend.seen == ["recon", "analyze", "generate"]          # analyze actually ran
    assert outcome.stages_run == ["recon", "analyze", "generate"]
    assert (tmp_path / "runs" / "testrun" / "arvo_10400" / "escalation.json").is_file()


def test_localized_recon_skips_analyze(tmp_path, monkeypatch):
    backend = _wire(monkeypatch, tmp_path, {
        "recon": _result("recon", {"suspected_files": ["coders/png.c"],
                                    "harness": {"entry_point": "LLVMFuzzerTestOneInput"}}),
        "generate": _result("generate", {}, stop="crash_found"),
    })
    from schemata.config import load_settings
    outcome = asyncio.run(orchestrator.run_task("arvo:10400", "claude_api", load_settings(),
                                                _FakeCost(), "testrun"))

    assert outcome.escalated is False
    assert backend.seen == ["recon", "generate"]                     # analyze NOT promoted
    assert "analyze" not in outcome.stages_run
