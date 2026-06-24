"""The backend abstraction — the crux that keeps orchestrator/router/stages
backend-agnostic. A stage is ONE agentic session (multi-turn, with tools).

Model identity lives in ONE registry (`_REGISTRY`): alias -> provider, model id,
price, and capability flags. `/model`, the cost tracker, the routing agent, and the
per-stage fallback all read through it, so they never see divergent tables.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..core.models import StageRequest, StageResult


@dataclass(frozen=True)
class ModelSpec:
    """One model alias's full identity. `price` is $/MTok (input, output, cache_read,
    cache_write). `supports_effort` gates the reasoning-effort param (errors on Haiku);
    `supports_cache` gates Anthropic prompt-cache breakpoints (OpenAI caches server-side)."""
    alias: str
    provider: str  # "anthropic" | "openai"
    model_id: str
    price: tuple[float, float, float, float]
    supports_effort: bool = True
    supports_cache: bool = True


# Single source of truth. Anthropic public pricing; OpenAI pricing per the GPT-5.5 model page.
# Opus pinned to 4-6: 4.8 false-positive-refused the generate stage on hard tasks (arvo:368,
# oss-fuzz:370689421) even with authorized-context framing; 4.6/4.7 are less conservative on this
# PoC-reproduction prompt. Pricing tier matches across 4.x opus.
# gpt5.model_id is the DEFAULT id; config [models.openai] may override the concrete string
# (the OpenAI backend resolves it from settings, falling back to this default).
_REGISTRY: dict[str, ModelSpec] = {
    "opus":   ModelSpec("opus",   "anthropic", "claude-opus-4-6",   (5.0, 25.0, 0.50, 6.25)),
    "sonnet": ModelSpec("sonnet", "anthropic", "claude-sonnet-4-6", (3.0, 15.0, 0.30, 3.75)),
    "haiku":  ModelSpec("haiku",  "anthropic", "claude-haiku-4-5",  (1.0,  5.0, 0.10, 1.25),
                        supports_effort=False),
    # GPT-5.5: input $5 / output $30 / cached-input $0.50 per MTok. No separate cache-write price
    # (input caching is automatic, server-side) and no Anthropic-style cache breakpoints.
    # NOTE: confirm against OpenAI's current model page; large-input tiers may carry a multiplier.
    "gpt5":   ModelSpec("gpt5",   "openai",    "gpt-5.5",           (5.0, 30.0, 0.50, 0.0),
                        supports_cache=False),
}


def spec_of(alias: str) -> ModelSpec:
    try:
        return _REGISTRY[alias]
    except KeyError:
        raise ValueError(
            f"unknown model alias: {alias!r} (known: {sorted(_REGISTRY)})"
        ) from None


def known_aliases() -> tuple[str, ...]:
    return tuple(_REGISTRY)


def model_id_of(alias: str) -> str:
    return spec_of(alias).model_id


def provider_of(alias: str) -> str:
    return spec_of(alias).provider


def price_of(alias: str) -> tuple[float, float, float, float]:
    return spec_of(alias).price


def alias_of(model: str) -> str:
    """Map an alias OR a concrete model id back to its alias.

    Fail loud on anything unrecognized — in the routed era a silent fallback to `opus`
    would quietly send an unknown model to the wrong provider/price.
    """
    if model in _REGISTRY:
        return model
    for alias, spec in _REGISTRY.items():
        if model == spec.model_id or alias in model or spec.model_id in model:
            return alias
    raise ValueError(f"unknown model {model!r}; not a known alias or model id")


def cost_of(usage, model_alias: str) -> float:
    a = alias_of(model_alias or usage.model)
    pin, pout, pcr, pcw = price_of(a)
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
