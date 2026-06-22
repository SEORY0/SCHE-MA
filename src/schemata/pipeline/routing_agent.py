"""LLM-based routing agent: classify task and decide pipeline configuration.

Two intervention points per task:
  1. plan()   — pre-pipeline: Haiku reads description + harness contract → PipelinePlan
  2. refine() — post-recon: Haiku reads recon output → adjusted PipelinePlan

Falls back to a hardcoded medium-difficulty plan on any LLM/parsing failure.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..backends.base import MODEL_IDS, cost_of
from ..core.config import Settings
from ..core.cost_tracker import CostTracker
from ..core.models import PipelinePlan, TaskMeta, Usage
from ..core.util import extract_last_json
from ..knowledge import atomic_vulns
from .harness import harness_contract as compute_harness_contract

log = logging.getLogger("schemata.routing_agent")

_VALID_DIFFICULTIES = {"easy", "medium", "hard"}
_VALID_STAGES = {"recon", "analyze", "generate"}
_VALID_MODELS = {"haiku", "sonnet", "opus"}
_VALID_STRATEGIES = {
    "seed-mutate", "format-skeleton-grow", "fdp-carve", "libfuzzer-minimal",
}
_VALID_BUDGETS = {"low", "normal", "high"}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = """\
You are a vulnerability reproduction pipeline router. Given a task description, \
harness contract, and metadata, classify the task and choose the optimal pipeline.

## Pipeline options
- **stages**: ["recon", "generate"] (skip analyze — for simple, direct bugs) \
or ["recon", "analyze", "generate"] (full — when localization needs a stronger model)
- **generate_model**: "sonnet" (cheaper, fast — for standard bugs) or "opus" \
(expensive, thorough — for complex multi-step bugs, deep format parsing)
- **thinking**: true (extended reasoning for complex code analysis) or false
- **instrument**: true (Docker container for local crash validation) or false
- **minimize_info**: true (lean context, skip knowledge base — for trivial bugs) or false

## Difficulty criteria
- **easy**: Direct crash path, standard format, small codebase. Single field mutation. \
Common pattern (heap-buffer-overflow, null-deref). Seeds available.
- **medium**: Requires understanding control flow. Multiple fields interact. Needs format \
knowledge or parser understanding. Standard vulnerability pattern but non-trivial trigger.
- **hard**: Multi-step exploitation path. Complex format (nested containers, checksums, \
protocol layers). Large codebase with deep call chains. Unusual vulnerability pattern.

## Construction strategies
- seed-mutate: In-repo seeds exist → copy and mutate the triggering field
- format-skeleton-grow: Build minimal valid file from format spec, set violation field
- fdp-carve: Map FuzzedDataProvider consumption order, set violation position
- libfuzzer-minimal: Build bytes >= min_size, set violation byte(s)

## Atomic vulnerability types
{vuln_menu}

Respond with ONLY a JSON object — no markdown fences, no explanation:
{{"difficulty":"easy|medium|hard","vuln_classes":["type-id"],"stages":["recon","generate"],"generate_model":"sonnet|opus","thinking":false,"instrument":true,"minimize_info":false,"generate_strategy_hint":"seed-mutate|null","budget_hint":"normal","reasoning":"one line"}}"""

_REFINE_SYSTEM = """\
You are the post-recon pipeline adjuster. Given the current plan and recon results, \
decide whether to adjust the pipeline. You can:
- Remove "analyze" from stages (if recon already localized the bug well)
- Add "analyze" to stages (if recon failed to localize — no suspected files/functions)
- Change generate_model to "opus" (if the bug looks harder than initially estimated)
- Change budget_hint (if format complexity warrants more turns)
- Update vuln_classes (if recon's classification differs from pre-pipeline)
- Update generate_strategy_hint (if recon found seeds or identified harness convention)

Respond with ONLY a JSON object containing the fields you want to CHANGE. \
Omit fields that stay the same. Always include "reasoning".
Example: {{"stages":["recon","analyze","generate"],"reasoning":"recon failed to localize"}}"""


def _build_plan_user(
    meta: TaskMeta,
    description: str,
    contract: dict,
    crash_classes: list[str],
    desc_classes: list[str],
) -> str:
    seed_count = len(contract.get("seed_candidates") or [])
    gates = (contract.get("format_gates") or [])[:4]
    calls = (contract.get("parser_calls") or [])[:8]
    return f"""\
## Task
project: {meta.project}
crash_type: {meta.crash_type}
crash_type_category: {meta.crash_type_category}
sanitizer: {meta.sanitizer or 'unknown'}
input_format: {meta.input_format}
project_complexity: {meta.project_complexity}

## Description
{description[:1500]}

## Harness contract
entry_point: {contract.get('entry_point', 'unknown')}
input_mode: {contract.get('input_mode', 'unknown')}
fuzzer_convention: {contract.get('fuzzer_convention', 'unknown')}
input_is_whole_file_format: {contract.get('input_is_whole_file_format', False)}
min_realistic_size: {contract.get('min_realistic_size', 0)}
format_gates: {json.dumps(gates)}
parser_calls: {json.dumps(calls)}
seed_candidates: {seed_count} found

## Pre-classification (deterministic)
crash_type_match: {json.dumps(crash_classes)}
description_match: {json.dumps(desc_classes)}"""


def _build_refine_user(plan: PipelinePlan, recon_output: dict) -> str:
    localized = bool(
        recon_output.get("suspected_files")
        or recon_output.get("suspected_functions")
        or recon_output.get("entry_point")
        or (isinstance(recon_output.get("harness"), dict)
            and recon_output["harness"].get("entry_point"))
    )
    harness = recon_output.get("harness", {})
    seeds = harness.get("seed_candidates") or recon_output.get("seed_candidates") or []
    return f"""\
## Current plan
{json.dumps(plan.model_dump(exclude_defaults=True), indent=2)}

## Recon output
vuln_classes: {json.dumps(recon_output.get('vuln_classes', []))}
localized: {localized}
suspected_files: {json.dumps(recon_output.get('suspected_files', [])[:3])}
suspected_functions: {json.dumps(recon_output.get('suspected_functions', [])[:3])}
entry_point: {recon_output.get('entry_point', 'unknown')}
fuzzer_convention: {harness.get('fuzzer_convention', 'unknown')}
input_is_whole_file_format: {harness.get('input_is_whole_file_format', False)}
seed_candidates: {len(seeds)} found"""


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

async def _call_haiku(
    system: str, user: str, settings: Settings,
) -> tuple[str, Usage]:
    import anthropic
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    rcfg = settings.raw.get("routing", {})
    model_alias = rcfg.get("model", "haiku")
    model_id = MODEL_IDS.get(model_alias, MODEL_IDS["haiku"])
    max_tokens = int(rcfg.get("max_tokens", 512))
    timeout = int(rcfg.get("timeout_s", 30))

    system_blocks = [{"type": "text", "text": system}]
    if rcfg.get("cache_system_prompt", True):
        system_blocks[0]["cache_control"] = {"type": "ephemeral"}

    resp = await client.messages.create(
        model=model_id,
        max_tokens=max_tokens,
        system=system_blocks,
        messages=[{"role": "user", "content": user}],
        timeout=timeout,
    )
    text = "".join(
        b.text for b in resp.content if getattr(b, "type", None) == "text"
    )
    u = resp.usage
    usage = Usage(
        model=model_id,
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return text.strip(), usage


# ---------------------------------------------------------------------------
# Parsing & validation
# ---------------------------------------------------------------------------

def _parse_plan_response(text: str) -> dict | None:
    raw = extract_last_json(text)
    if not raw or "difficulty" not in raw:
        return None
    return raw


def _coerce(raw: dict) -> dict:
    """Validate and coerce LLM output fields to safe values."""
    out = dict(raw)
    if out.get("difficulty") not in _VALID_DIFFICULTIES:
        out["difficulty"] = "medium"
    stages = out.get("stages")
    if not isinstance(stages, list) or "generate" not in stages:
        out["stages"] = ["recon", "analyze", "generate"]
    if "recon" not in out["stages"]:
        out["stages"].insert(0, "recon")
    out["stages"] = [s for s in out["stages"] if s in _VALID_STAGES]
    if out.get("generate_model") not in _VALID_MODELS:
        out["generate_model"] = "sonnet"
    for bool_key in ("thinking", "instrument", "minimize_info"):
        if not isinstance(out.get(bool_key), bool):
            out[bool_key] = bool_key == "instrument"  # instrument defaults True
    if out.get("generate_strategy_hint") not in _VALID_STRATEGIES:
        out["generate_strategy_hint"] = None
    if out.get("budget_hint") not in _VALID_BUDGETS:
        out["budget_hint"] = "normal"
    if not isinstance(out.get("vuln_classes"), list):
        out["vuln_classes"] = []
    out["reasoning"] = str(out.get("reasoning") or "")[:200]
    return out


def _to_pipeline_plan(decision: dict, settings: Settings) -> PipelinePlan:
    difficulty = decision["difficulty"]
    stages = decision["stages"]
    stage_models: dict[str, str] = {}
    for s in stages:
        if s == "recon":
            stage_models[s] = "haiku"
        elif s == "analyze":
            stage_models[s] = settings.model_for("analyze", difficulty)
        elif s == "generate":
            stage_models[s] = decision.get("generate_model") or settings.model_for("generate", difficulty)

    return PipelinePlan(
        difficulty=difficulty,
        stages=stages,
        stage_models=stage_models,
        has_instrument=decision.get("instrument", True),
        has_mcp_index=False,
        thinking=decision.get("thinking", False),
        minimize_info=decision.get("minimize_info", False),
        routing_source="llm",
        vuln_classes=decision.get("vuln_classes", []),
        generate_strategy_hint=decision.get("generate_strategy_hint"),
        budget_hint=decision.get("budget_hint"),
        routing_reasoning=decision.get("reasoning"),
    )


def _default_plan(meta: TaskMeta, settings: Settings) -> PipelinePlan:
    """Hardcoded medium-route fallback — equivalent to the former rule-based router."""
    stages = ["recon", "analyze", "generate"]
    stage_models = {s: settings.model_for(s, "medium") for s in stages}
    return PipelinePlan(
        difficulty="medium",
        stages=stages,
        stage_models=stage_models,
        has_instrument=True,
        has_mcp_index=False,
        thinking=False,
        minimize_info=False,
        routing_source="default",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def plan(
    meta: TaskMeta,
    task_dir: Path,
    settings: Settings,
    cost: CostTracker | None = None,
    contract: dict | None = None,
) -> PipelinePlan:
    """Pre-pipeline routing: one Haiku call to decide the full pipeline."""
    if contract is None:
        contract = compute_harness_contract(task_dir)

    desc_path = Path(task_dir) / "description.txt"
    try:
        description = desc_path.read_text(errors="replace")[:2000] if desc_path.is_file() else ""
    except OSError:
        description = ""

    crash_classes = atomic_vulns.classify_from_crash_type(meta.crash_type) if meta.crash_type != "unknown" else []
    desc_classes = atomic_vulns.classify_from_description(description) if description else []

    system = _PLAN_SYSTEM.format(vuln_menu=atomic_vulns.menu())
    user = _build_plan_user(meta, description, contract, crash_classes, desc_classes)

    try:
        text, usage = await _call_haiku(system, user, settings)
        if cost:
            cost.add(meta.task_id, "routing", usage, cost_of(usage, "haiku"))
        raw = _parse_plan_response(text)
        if raw is None:
            log.warning("routing_agent.plan: failed to parse LLM JSON, using default")
            return _default_plan(meta, settings)
        decision = _coerce(raw)
        result = _to_pipeline_plan(decision, settings)
        log.info("routing_agent.plan: %s → %s (%s)", meta.task_id, result.difficulty, result.routing_reasoning)
        return result
    except Exception as e:
        log.warning("routing_agent.plan failed (%s), using default plan", e)
        return _default_plan(meta, settings)


async def refine(
    current_plan: PipelinePlan,
    recon_output: dict,
    meta: TaskMeta,
    settings: Settings,
    cost: CostTracker | None = None,
) -> PipelinePlan:
    """Post-recon refinement: one Haiku call to adjust the plan based on recon results."""
    user = _build_refine_user(current_plan, recon_output)

    try:
        text, usage = await _call_haiku(_REFINE_SYSTEM, user, settings)
        if cost:
            cost.add(meta.task_id, "routing_refine", usage, cost_of(usage, "haiku"))
        raw = extract_last_json(text)
        if not raw:
            log.info("routing_agent.refine: no changes from LLM")
            return current_plan
        return _apply_refinement(current_plan, raw, recon_output, settings)
    except Exception as e:
        log.warning("routing_agent.refine failed (%s), keeping current plan", e)
        return current_plan


def _apply_refinement(
    current: PipelinePlan, delta: dict, recon_output: dict, settings: Settings,
) -> PipelinePlan:
    """Merge LLM delta into the current plan."""
    updated = current.model_copy()

    if "stages" in delta:
        raw_stages = delta["stages"]
        if isinstance(raw_stages, list):
            stages = [s for s in raw_stages if s in _VALID_STAGES]
            if "recon" not in stages:
                stages.insert(0, "recon")
            if "generate" in stages:
                updated.stages = stages
                updated.stage_models = {
                    s: settings.model_for(s, updated.difficulty)
                    for s in stages
                }

    if "generate_model" in delta and delta["generate_model"] in _VALID_MODELS:
        updated.stage_models["generate"] = delta["generate_model"]

    if "difficulty" in delta and delta["difficulty"] in _VALID_DIFFICULTIES:
        updated.difficulty = delta["difficulty"]

    if "thinking" in delta and isinstance(delta["thinking"], bool):
        updated.thinking = delta["thinking"]

    if "instrument" in delta and isinstance(delta["instrument"], bool):
        updated.has_instrument = delta["instrument"]

    if "minimize_info" in delta and isinstance(delta["minimize_info"], bool):
        updated.minimize_info = delta["minimize_info"]

    if "budget_hint" in delta and delta["budget_hint"] in _VALID_BUDGETS:
        updated.budget_hint = delta["budget_hint"]

    if "generate_strategy_hint" in delta:
        hint = delta["generate_strategy_hint"]
        updated.generate_strategy_hint = hint if hint in _VALID_STRATEGIES else None

    if "vuln_classes" in delta and isinstance(delta["vuln_classes"], list):
        updated.vuln_classes = delta["vuln_classes"]
    elif recon_output.get("vuln_classes"):
        updated.vuln_classes = recon_output["vuln_classes"]

    updated.routing_source = "llm_refined"
    updated.routing_reasoning = str(delta.get("reasoning") or updated.routing_reasoning or "")[:200]

    return updated
