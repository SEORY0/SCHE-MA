"""Render-contract tests for the level1 redesign wiring (prompt_loader.build_request).

Guards that: description.txt is injected inline (P1/P2/P3 all need it), the recon harness
packet (P2) and analyze localization (P3) schemas are present, the discriminate stage (P1)
renders and resolves its model via fallback (it is not in plan.stage_models), and the
generate kickoff is backend-aware (arena=submit_poc tool, local=submit.sh).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from schemata.core.config import load_settings
from schemata.core.models import PipelinePlan, TaskMeta
from schemata.pipeline.prompt_loader import build_request

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


def test_harness_convention_and_format_advice_injected(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    meta = TaskMeta(task_id="t", project="binutils", input_format="elf")
    prior = {"recon": {"harness": {"fuzzer_convention": "libfuzzer"},
                       "vuln_classes": ["heap-buffer-overflow-read"]}}
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert "harness_convention" in req.system_prompt
    assert "format_template" in req.system_prompt
    assert "ELF" in req.system_prompt


def test_description_fallback_classification(tmp_path):
    (tmp_path / "description.txt").write_text("heap-buffer-overflow in parse_chunk")
    settings, plan, meta, handle = _ctx(tmp_path)
    handle = SimpleNamespace(task_dir=tmp_path, masked_id=None, agent_id=None,
                             checksum=None, server_url=None)
    prior = {}
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert "Heap-buffer-overflow READ" in req.system_prompt or "Heap-buffer-overflow WRITE" in req.system_prompt


def test_sanitizer_hint_injected_for_msan_types(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    prior = {"recon": {"vuln_classes": ["use-of-uninitialized-value"]}}
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert "sanitizer_warning" in req.system_prompt
    assert "MSan" in req.system_prompt


def test_no_sanitizer_hint_for_asan_types(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    prior = {"recon": {"vuln_classes": ["heap-buffer-overflow-read"]}}
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert "sanitizer_warning" not in req.system_prompt


def test_adaptive_max_turns_whole_file_no_seeds(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    prior = {
        "harness_contract": {
            "input_is_whole_file_format": True,
            "seed_candidates": [],
        },
        "recon": {"vuln_classes": ["heap-buffer-overflow-read"]},
    }
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert req.max_turns == 45


def test_default_max_turns_with_seeds(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    prior = {
        "harness_contract": {
            "input_is_whole_file_format": True,
            "seed_candidates": [{"path": "seed.bin", "size": 100}],
        },
        "recon": {"vuln_classes": ["heap-buffer-overflow-read"]},
    }
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert req.max_turns == 30


def test_default_max_turns_libfuzzer_bytes(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    prior = {
        "harness_contract": {
            "input_is_whole_file_format": False,
            "seed_candidates": [],
        },
        "recon": {"vuln_classes": ["heap-buffer-overflow-read"]},
    }
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert req.max_turns == 30


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


def test_retarget_kickoff_includes_failure_class(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    prior = {
        "discriminate": {
            "verdict": "REJECT",
            "failure_class": "wrong_crash_type",
            "retarget_instruction": "Target the heap-buffer-overflow, not stack-overflow.",
        },
        "recon": {"vuln_classes": ["heap-buffer-overflow-read"]},
    }
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert "RETARGET" in req.kickoff
    assert "wrong_crash_type" in req.kickoff
    assert "heap-buffer-overflow-read" in req.kickoff
    assert "Target the heap-buffer-overflow" in req.kickoff


def test_retarget_kickoff_any_crash_generic(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    prior = {
        "discriminate": {
            "verdict": "REJECT",
            "failure_class": "any_crash_generic",
            "retarget_instruction": "Input was too corrupt.",
        },
    }
    req = build_request("generate", plan, meta, handle, prior, settings, "claude_api")
    assert "RETARGET" in req.kickoff
    assert "structurally VALID" in req.kickoff


def test_normal_kickoff_without_discriminate(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    req = build_request("generate", plan, meta, handle, {}, settings, "claude_api")
    assert "RETARGET" not in req.kickoff
    assert "submit_poc" in req.kickoff


def test_analysis_tools_advice_injected(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    req = build_request("generate", plan, meta, handle, {}, settings, "claude_api")
    assert "tool_skill" in req.system_prompt
    assert "construct" in req.system_prompt
    assert "pwntools" in req.system_prompt or "pwn" in req.system_prompt


def test_analysis_tools_with_instrument_container(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    req = build_request("generate", plan, meta, handle, {}, settings, "claude_api",
                        instrument_container="test_container")
    assert "gdb" in req.system_prompt.lower()
    assert "coverage_check" in req.system_prompt


def test_analysis_tools_not_in_recon(tmp_path):
    settings, plan, meta, handle = _ctx(tmp_path)
    req = build_request("recon", plan, meta, handle, {}, settings, "claude_api")
    assert "tool_skill" not in req.system_prompt
