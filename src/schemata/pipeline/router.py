"""Adaptive router: TaskMeta -> PipelinePlan, using config/routing_rules.json
and config [models]. Stage 1 Recon is always Haiku; Stage 2/3 model by difficulty."""
from __future__ import annotations

import json
from functools import lru_cache

from ..core.config import PKG_ROOT, Settings
from ..core.models import PipelinePlan, TaskMeta

ROUTING_RULES = PKG_ROOT / "config" / "routing_rules.json"
ROUTING_RULES_TEMPLATE = PKG_ROOT / "config" / "templates" / "routing_rules.json"


@lru_cache(maxsize=1)
def _rules() -> dict:
    path = ROUTING_RULES if ROUTING_RULES.exists() else ROUTING_RULES_TEMPLATE
    with open(path) as f:
        return json.load(f)


def plan(meta: TaskMeta, settings: Settings) -> PipelinePlan:
    diff = meta.difficulty_estimate if meta.difficulty_estimate in ("easy", "medium", "hard") else "medium"
    rule = _rules()[diff]
    stages = list(rule["stages"])
    stage_models = {s: settings.model_for(s, diff) for s in stages}
    return PipelinePlan(
        difficulty=diff,
        stages=stages,
        stage_models=stage_models,
        has_instrument=bool(rule.get("has_instrument", False)),
        has_mcp_index=bool(rule.get("has_mcp_index", False)),
        thinking=bool(rule.get("thinking", False)),
        minimize_info=bool(rule.get("minimize_info", False)),
    )
