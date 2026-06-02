"""Prompt-cache placement + model params for the Claude API backend (M3).

Two cache levers, both within a single stage's multi-turn loop:
  1. system + tools — one breakpoint on the last system block caches tools+system
     together (render order is tools -> system -> messages). Constant across the
     whole stage, so turns 2..N read it instead of re-paying for it.
  2. growing transcript — one ROLLING breakpoint on the last tool_result of the most
     recent turn, so each request reuses the prior conversation prefix. We keep at
     most one message breakpoint (system=1, message=1 -> 2 total, well under the 4 cap)
     and move it forward each turn, staying inside the 20-block lookback window.

Model params map req.thinking -> adaptive thinking + effort. budget_tokens is NOT used:
it is deprecated on Opus 4.6 / Sonnet 4.6 (removed on 4.7+), and effort errors on Haiku,
which only ever runs Recon (no thinking) — so effort is gated to opus/sonnet stages.
"""
from __future__ import annotations

import logging

from ..models import StageRequest
from .base import MODEL_IDS, alias_of

log = logging.getLogger("schemata.prompt_cache")

# Minimum cacheable prefix per model family (tokens). Below this the API silently
# does not write the cache (cache_creation_input_tokens stays 0) — no error.
_MIN_CACHE_TOKENS = {"opus": 4096, "sonnet": 2048, "haiku": 4096}
_CHARS_PER_TOKEN = 4  # rough heuristic for the offline size warning only


def system_blocks(req: StageRequest) -> list[dict]:
    """The system prompt as a single cached text block (caches tools+system)."""
    _warn_if_too_small(req.system_prompt, req.model)
    return [{
        "type": "text",
        "text": req.system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]


def model_params(req: StageRequest, settings) -> dict:
    """model / max_tokens / thinking / effort kwargs for messages.stream()."""
    alias = alias_of(req.model)
    toks = settings.tokens
    params: dict = {
        "model": MODEL_IDS.get(alias, req.model),
        "max_tokens": int(toks.get("max_output_tokens", 8000)),
    }
    if req.thinking is not None:
        params["thinking"] = {"type": "adaptive"}
        # give adaptive thinking room so a turn isn't truncated mid-thought
        params["max_tokens"] = int(toks.get("max_output_tokens_thinking", 16000))
        if alias in ("opus", "sonnet"):  # effort errors on Haiku (Recon is the only Haiku stage)
            params["output_config"] = {"effort": toks.get("effort_hard", "high")}
    return params


def with_breakpoints(messages: list[dict]) -> list[dict]:
    """Keep exactly one rolling cache breakpoint on the last tool_result block.

    Mutates the running messages list in place: strips any prior message-level
    cache_control, then marks the last dict block of the last message. Assistant
    turns hold SDK block objects (not dicts) and are left untouched.
    """
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            for blk in c:
                if isinstance(blk, dict):
                    blk.pop("cache_control", None)
    if messages:
        c = messages[-1].get("content")
        if isinstance(c, list):
            dict_blocks = [b for b in c if isinstance(b, dict)]
            if dict_blocks:
                dict_blocks[-1]["cache_control"] = {"type": "ephemeral"}
    return messages


def _warn_if_too_small(text: str, model: str) -> None:
    floor = _MIN_CACHE_TOKENS.get(alias_of(model), 4096)
    approx = len(text) // _CHARS_PER_TOKEN
    if approx < floor:
        log.warning(
            "system prompt ~%d tok < %d cache floor for %s; prompt cache may not write "
            "(cache_creation_input_tokens=0). Enlarge the shared prompt or accept no caching.",
            approx, floor, alias_of(model),
        )
