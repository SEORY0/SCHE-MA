"""The backend abstraction — the crux that keeps orchestrator/router/stages
backend-agnostic. A stage is ONE agentic session (multi-turn, with tools)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..core.models import StageRequest, StageResult

# Anthropic public pricing ($/MTok): (input, output, cache_read, cache_write_5m)
PRICES = {
    "opus":   (5.0, 25.0, 0.50, 6.25),
    "sonnet": (3.0, 15.0, 0.30, 3.75),
    "haiku":  (1.0,  5.0, 0.10, 1.25),
}

# alias -> full model id (used by both backends; CLI accepts the alias too).
# Opus pinned to 4-6: 4.8 false-positive-refused the generate stage on hard tasks
# (arvo:368, oss-fuzz:370689421) even with authorized-context framing; 4.6/4.7 are less
# conservative on this PoC-reproduction prompt. Pricing tier matches across 4.x opus.
MODEL_IDS = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}


def alias_of(model: str) -> str:
    for a in PRICES:
        if model == a or a in model:
            return a
    return "opus"


def cost_of(usage, model_alias: str) -> float:
    a = alias_of(model_alias or usage.model)
    pin, pout, pcr, pcw = PRICES[a]
    return (
        usage.input_tokens * pin
        + usage.output_tokens * pout
        + usage.cache_read_tokens * pcr
        + usage.cache_write_tokens * pcw
    ) / 1_000_000.0


class AgentBackend(ABC):
    name: str = "base"

    def __init__(self, settings):
        self.settings = settings

    @abstractmethod
    async def run_stage(self, req: StageRequest) -> StageResult:
        ...
