"""Render-contract tests for the level1 redesign wiring (prompt_loader.build_request).

Guards that: description.txt is injected inline (P1/P2/P3 all need it), the recon harness
packet (P2) and analyze localization (P3) schemas are present, the discriminate stage (P1)
renders and resolves its model via fallback (it is not in plan.stage_models), and the
generate kickoff is backend-aware (arena=submit_poc tool, local=submit.sh).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from schemata.config import load_settings
from schemata.models import PipelinePlan, TaskMeta
from schemata.prompt_loader import build_request

_DESC = "UNIQUEDESC heap-buffer-overflow in mng_get_long"


def _ctx(tmp_path: Path):
    (tmp_path / "description.txt").write_text(_DESC)
    settings = load_settings()
    plan = PipelinePlan(
        difficulty="medium", stages=["recon", "analyze", "generate"],
        stage_models={"recon": "haiku", "analyze": "sonnet", "generate": "sonnet"})
    meta = TaskMeta(task_id="t", project="proj")
    handle = SimpleNamespace(task_dir=tmp_path, masked_id=None, agent_id=None,
                             checksum=None, server_url=None)
    return settings, plan, meta, handle


def test_recon_has_harness_packet_and_description(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    req = build_request("recon", plan, meta, handle, {}, settings, "claude_api")
    assert _DESC in req.system_prompt          # description injected inline (P2)
    assert "Stage 1 — Recon" in req.system_prompt
    assert "harness" in req.system_prompt      # harness packet schema (P2)


def test_analyze_has_localization(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    req = build_request("analyze", plan, meta, handle, {"recon": {"harness": {}}}, settings, "claude_api")
    assert "Localize & Plan" in req.system_prompt
    assert "localization" in req.system_prompt  # localization schema (P3)
    assert _DESC in req.system_prompt


def test_discriminate_renders_and_resolves_model(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    # 'discriminate' is intentionally absent from plan.stage_models -> must fall back.
    req = build_request("discriminate", plan, meta, handle, {}, settings, "claude_api")
    assert "Discriminate" in req.system_prompt
    assert _DESC in req.system_prompt
    assert req.model == "sonnet"               # settings.model_for fallback


def test_generate_kickoff_is_backend_aware(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    api = build_request("generate", plan, meta, handle, {}, settings, "claude_api")
    cc = build_request("generate", plan, meta, handle, {}, settings, "claude_code")
    assert "submit_poc" in api.kickoff         # arena: tool
    assert "submit.sh" in cc.kickoff           # local: script


def test_global_knowledge_base_included_and_gated(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    # full route (minimize_info=False) carries the global KB (P4)
    req = build_request("analyze", plan, meta, handle, {}, settings, "claude_api")
    assert "knowledge_base" in req.system_prompt
    # lean route (minimize_info=True) drops it — enables clean ablation
    lean = PipelinePlan(difficulty="easy", stages=["recon", "generate"],
                        stage_models={"recon": "haiku", "generate": "sonnet"}, minimize_info=True)
    req2 = build_request("generate", lean, meta, handle, {}, settings, "claude_api")
    assert "knowledge_base" not in req2.system_prompt
