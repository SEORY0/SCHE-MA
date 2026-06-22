"""Tests for the LLM-based routing agent (routing_agent.py).

Covers: JSON parsing / coercion, PipelinePlan construction, default fallback,
refinement merging, and async plan/refine with mocked Haiku calls.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from schemata.core.config import load_settings
from schemata.core.models import PipelinePlan, TaskMeta, Usage
from schemata.pipeline.routing_agent import (
    _apply_refinement,
    _coerce,
    _default_plan,
    _parse_plan_response,
    _to_pipeline_plan,
    plan,
    refine,
)


def _settings():
    return load_settings()


def _meta(**kw):
    defaults = dict(task_id="arvo:1", project="proj", crash_type="heap-buffer-overflow")
    defaults.update(kw)
    return TaskMeta(**defaults)


# ---------------------------------------------------------------------------
# _parse_plan_response
# ---------------------------------------------------------------------------

def test_parse_valid_json():
    text = '{"difficulty":"easy","stages":["recon","generate"],"generate_model":"sonnet"}'
    result = _parse_plan_response(text)
    assert result is not None
    assert result["difficulty"] == "easy"


def test_parse_json_with_markdown_fences():
    text = '```json\n{"difficulty":"hard","stages":["recon","analyze","generate"]}\n```'
    result = _parse_plan_response(text)
    assert result is not None
    assert result["difficulty"] == "hard"


def test_parse_garbage_returns_none():
    assert _parse_plan_response("not json at all") is None


def test_parse_json_without_difficulty_returns_none():
    assert _parse_plan_response('{"stages":["recon","generate"]}') is None


# ---------------------------------------------------------------------------
# _coerce
# ---------------------------------------------------------------------------

def test_coerce_bad_difficulty_defaults_medium():
    result = _coerce({"difficulty": "impossible", "stages": ["recon", "generate"]})
    assert result["difficulty"] == "medium"


def test_coerce_missing_recon_prepends():
    result = _coerce({"difficulty": "easy", "stages": ["generate"]})
    assert result["stages"][0] == "recon"
    assert "generate" in result["stages"]


def test_coerce_missing_generate_gets_full_pipeline():
    result = _coerce({"difficulty": "medium", "stages": ["recon", "analyze"]})
    assert result["stages"] == ["recon", "analyze", "generate"]


def test_coerce_invalid_strategy_nulled():
    result = _coerce({"difficulty": "easy", "stages": ["recon", "generate"],
                       "generate_strategy_hint": "invalid-thing"})
    assert result["generate_strategy_hint"] is None


def test_coerce_valid_strategy_kept():
    result = _coerce({"difficulty": "easy", "stages": ["recon", "generate"],
                       "generate_strategy_hint": "seed-mutate"})
    assert result["generate_strategy_hint"] == "seed-mutate"


def test_coerce_bad_budget_defaults_normal():
    result = _coerce({"difficulty": "easy", "stages": ["recon", "generate"],
                       "budget_hint": "extreme"})
    assert result["budget_hint"] == "normal"


def test_coerce_bool_defaults():
    result = _coerce({"difficulty": "easy", "stages": ["recon", "generate"]})
    assert result["instrument"] is True
    assert result["thinking"] is False
    assert result["minimize_info"] is False


# ---------------------------------------------------------------------------
# _to_pipeline_plan
# ---------------------------------------------------------------------------

def test_easy_plan_has_correct_shape():
    decision = _coerce({
        "difficulty": "easy", "stages": ["recon", "generate"],
        "generate_model": "sonnet", "instrument": False, "minimize_info": True,
        "thinking": False, "vuln_classes": ["heap-buffer-overflow-read"],
        "generate_strategy_hint": "seed-mutate", "budget_hint": "low",
        "reasoning": "simple overflow",
    })
    p = _to_pipeline_plan(decision, _settings())
    assert p.difficulty == "easy"
    assert p.stages == ["recon", "generate"]
    assert p.stage_models["recon"] == "haiku"
    assert p.stage_models["generate"] == "sonnet"
    assert p.routing_source == "llm"
    assert p.generate_strategy_hint == "seed-mutate"
    assert p.budget_hint == "low"


def test_hard_plan_uses_opus():
    decision = _coerce({
        "difficulty": "hard", "stages": ["recon", "analyze", "generate"],
        "generate_model": "opus", "thinking": True, "instrument": True,
    })
    p = _to_pipeline_plan(decision, _settings())
    assert p.stage_models["generate"] == "opus"
    assert p.thinking is True


# ---------------------------------------------------------------------------
# _default_plan
# ---------------------------------------------------------------------------

def test_default_plan_is_medium_route():
    p = _default_plan(_meta(), _settings())
    assert p.difficulty == "medium"
    assert p.stages == ["recon", "analyze", "generate"]
    assert p.routing_source == "default"
    assert p.has_instrument is True


# ---------------------------------------------------------------------------
# _apply_refinement
# ---------------------------------------------------------------------------

def test_refine_removes_analyze():
    base = PipelinePlan(
        difficulty="medium", stages=["recon", "analyze", "generate"],
        stage_models={"recon": "haiku", "analyze": "sonnet", "generate": "sonnet"},
    )
    delta = {"stages": ["recon", "generate"], "reasoning": "recon localized well"}
    result = _apply_refinement(base, delta, {}, _settings())
    assert "analyze" not in result.stages
    assert result.routing_source == "llm_refined"
    assert "localized" in result.routing_reasoning


def test_refine_adds_analyze():
    base = PipelinePlan(
        difficulty="easy", stages=["recon", "generate"],
        stage_models={"recon": "haiku", "generate": "sonnet"},
    )
    delta = {"stages": ["recon", "analyze", "generate"], "reasoning": "recon failed to localize"}
    result = _apply_refinement(base, delta, {}, _settings())
    assert "analyze" in result.stages


def test_refine_upgrades_model():
    base = PipelinePlan(
        difficulty="medium", stages=["recon", "analyze", "generate"],
        stage_models={"recon": "haiku", "analyze": "sonnet", "generate": "sonnet"},
    )
    delta = {"generate_model": "opus", "reasoning": "complex format needs opus"}
    result = _apply_refinement(base, delta, {}, _settings())
    assert result.stage_models["generate"] == "opus"


def test_refine_picks_up_recon_vuln_classes():
    base = PipelinePlan(
        difficulty="medium", stages=["recon", "analyze", "generate"],
        stage_models={"recon": "haiku", "analyze": "sonnet", "generate": "sonnet"},
    )
    recon_output = {"vuln_classes": ["use-of-uninitialized-value"]}
    delta = {"reasoning": "no change"}
    result = _apply_refinement(base, delta, recon_output, _settings())
    assert result.vuln_classes == ["use-of-uninitialized-value"]


def test_refine_ignores_invalid_stages():
    base = PipelinePlan(
        difficulty="medium", stages=["recon", "analyze", "generate"],
        stage_models={"recon": "haiku", "analyze": "sonnet", "generate": "sonnet"},
    )
    delta = {"stages": ["recon", "bogus", "generate"], "reasoning": "test"}
    result = _apply_refinement(base, delta, {}, _settings())
    assert "bogus" not in result.stages
    assert "generate" in result.stages


# ---------------------------------------------------------------------------
# Async plan() / refine() with mocked LLM
# ---------------------------------------------------------------------------

_MOCK_PLAN_JSON = json.dumps({
    "difficulty": "easy",
    "stages": ["recon", "generate"],
    "generate_model": "sonnet",
    "thinking": False,
    "instrument": True,
    "minimize_info": True,
    "vuln_classes": ["heap-buffer-overflow-read"],
    "generate_strategy_hint": "seed-mutate",
    "budget_hint": "low",
    "reasoning": "simple heap overflow with seeds",
})

_MOCK_USAGE = Usage(model="haiku", input_tokens=100, output_tokens=50)


def test_plan_calls_haiku_and_parses(tmp_path):
    (tmp_path / "description.txt").write_text("heap-buffer-overflow in parse_header")
    meta = _meta()

    async def _run():
        with patch("schemata.pipeline.routing_agent._call_haiku",
                   new_callable=AsyncMock, return_value=(_MOCK_PLAN_JSON, _MOCK_USAGE)):
            return await plan(meta, tmp_path, _settings())

    result = asyncio.run(_run())
    assert result.difficulty == "easy"
    assert result.routing_source == "llm"
    assert result.stages == ["recon", "generate"]


def test_plan_fallback_on_api_error(tmp_path):
    (tmp_path / "description.txt").write_text("some bug")
    meta = _meta()

    async def _run():
        with patch("schemata.pipeline.routing_agent._call_haiku",
                   new_callable=AsyncMock, side_effect=Exception("API timeout")):
            return await plan(meta, tmp_path, _settings())

    result = asyncio.run(_run())
    assert result.routing_source == "default"
    assert result.difficulty == "medium"


_MOCK_REFINE_JSON = json.dumps({
    "stages": ["recon", "analyze", "generate"],
    "reasoning": "recon did not localize, adding analyze",
})


def test_refine_with_recon_output():
    base = PipelinePlan(
        difficulty="easy", stages=["recon", "generate"],
        stage_models={"recon": "haiku", "generate": "sonnet"},
        routing_source="llm",
    )
    recon_output = {"vuln_classes": ["heap-buffer-overflow-read"]}

    async def _run():
        with patch("schemata.pipeline.routing_agent._call_haiku",
                   new_callable=AsyncMock, return_value=(_MOCK_REFINE_JSON, _MOCK_USAGE)):
            return await refine(base, recon_output, _meta(), _settings())

    result = asyncio.run(_run())
    assert "analyze" in result.stages
    assert result.routing_source == "llm_refined"


def test_refine_fallback_on_error():
    base = PipelinePlan(
        difficulty="medium", stages=["recon", "analyze", "generate"],
        stage_models={"recon": "haiku", "analyze": "sonnet", "generate": "sonnet"},
        routing_source="llm",
    )

    async def _run():
        with patch("schemata.pipeline.routing_agent._call_haiku",
                   new_callable=AsyncMock, side_effect=Exception("timeout")):
            return await refine(base, {}, _meta(), _settings())

    result = asyncio.run(_run())
    assert result is base


def test_cost_tracked_as_routing_stage(tmp_path):
    (tmp_path / "description.txt").write_text("heap-buffer-overflow in parse_header")
    meta = _meta()
    from schemata.core.cost_tracker import CostTracker
    cost = CostTracker(total_budget_usd=100.0, per_task_soft_usd=10.0)

    async def _run():
        with patch("schemata.pipeline.routing_agent._call_haiku",
                   new_callable=AsyncMock, return_value=(_MOCK_PLAN_JSON, _MOCK_USAGE)):
            await plan(meta, tmp_path, _settings(), cost=cost)

    asyncio.run(_run())
    routing_entries = [e for e in cost.entries if e["stage"] == "routing"]
    assert len(routing_entries) == 1
    assert routing_entries[0]["task_id"] == "arvo:1"
