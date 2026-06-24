"""OpenAI API backend — Responses adapter for the shared agentic-stage driver.

The multi-turn policy lives in `_agentic.run_agentic_stage`. This module supplies the OpenAI
Responses wire format: translating the shared tool dicts to function tools, calling
`responses.create`, and shaping the running `input` list (echoing the model's own output items
back so call_id/phase linkage survives, and appending `function_call_output` tool results).
"""
from __future__ import annotations

import json

from ..core.models import StageRequest, StageResult, Usage
from ._agentic import Turn, run_agentic_stage
from .base import AgentBackend, model_id_of, provider_of, spec_of
from .tools import permissions


def _get(obj, key, default=None):
    """Field access that works for both dicts and SDK/namespace objects."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _to_openai_tools(anthropic_tools: list[dict]) -> list[dict]:
    """Translate the shared Messages-API tool dicts to Responses function tools."""
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t["input_schema"],
        }
        for t in anthropic_tools
    ]


def _text_of(resp) -> str:
    parts: list[str] = []
    for item in _get(resp, "output", []) or []:
        if _get(item, "type") == "message":
            for c in _get(item, "content", []) or []:
                if _get(c, "type") == "output_text":
                    parts.append(_get(c, "text", "") or "")
    return "".join(parts)


def _tool_calls_of(resp) -> list:
    """Normalize function_call output items to {id, name, input(dict)}."""
    calls = []
    for it in _get(resp, "output", []) or []:
        if _get(it, "type") != "function_call":
            continue
        raw = _get(it, "arguments")
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (ValueError, TypeError):
            args = {}
        calls.append({"id": _get(it, "call_id"), "name": _get(it, "name"), "input": args})
    return calls


def _usage_of(resp) -> tuple[Usage, int]:
    """Map Responses usage -> Usage. Cached input is split out of input_tokens so cost_of
    (which prices input and cache_read on disjoint buckets) does not double-count. Returns
    (usage, reasoning_tokens); reasoning tokens are diagnostic-only (already in output_tokens)."""
    u = _get(resp, "usage")
    if u is None:
        return Usage(), 0
    cached = int(_get(_get(u, "input_tokens_details"), "cached_tokens", 0) or 0)
    reasoning = int(_get(_get(u, "output_tokens_details"), "reasoning_tokens", 0) or 0)
    input_total = int(_get(u, "input_tokens", 0) or 0)
    return (
        Usage(
            input_tokens=max(0, input_total - cached),
            output_tokens=int(_get(u, "output_tokens", 0) or 0),
            cache_read_tokens=cached,
            cache_write_tokens=0,
        ),
        reasoning,
    )


def _incomplete_maxed(resp) -> bool:
    """True when the turn stopped because it hit the output-token cap (≈ Anthropic max_tokens)."""
    if _get(resp, "status") != "incomplete":
        return False
    return _get(_get(resp, "incomplete_details"), "reason") == "max_output_tokens"


class OpenAiApiBackend(AgentBackend):
    name = "openai_api"
    provider = "openai"
    AUTO = "auto"
    NONE = "none"

    def __init__(self, settings, client=None):
        super().__init__(settings)
        if client is not None:
            self.client = client
        else:
            key = getattr(settings, "openai_api_key", None)
            if not key:
                raise RuntimeError(
                    "OPENAI_API_KEY is not set, but a stage routed to an OpenAI model "
                    "(gpt5). Set it in .env or switch the stage's model alias."
                )
            import openai  # lazy: only needed when gpt5 actually runs; tests inject a fake
            self.client = openai.AsyncOpenAI(api_key=key)

    async def run_stage(self, req: StageRequest) -> StageResult:
        if provider_of(req.model) != "openai":
            raise ValueError(
                f"OpenAiApiBackend received a non-OpenAI model alias: {req.model!r}"
            )
        return await run_agentic_stage(self, req)

    def _model_id(self, alias: str) -> str:
        """Resolve the concrete OpenAI model id, honoring a [models.openai] config override."""
        override = (self.settings.raw.get("models", {}).get("openai", {}) or {}).get(alias)
        return override or model_id_of(alias)

    # -- Adapter hooks ------------------------------------------------------------
    def build_tools(self, req: StageRequest) -> list[dict]:
        return _to_openai_tools(permissions.tools_for(req))

    def build_system(self, req: StageRequest) -> str:
        return req.system_prompt

    def build_params(self, req: StageRequest) -> dict:
        toks = self.settings.tokens
        params: dict = {
            "model": self._model_id(req.model),
            "max_output_tokens": int(toks.get("max_output_tokens", 8000)),
        }
        if req.thinking is not None:
            params["max_output_tokens"] = int(toks.get("max_output_tokens_thinking", 16000))
            if spec_of(req.model).supports_effort:
                params["reasoning"] = {"effort": toks.get("effort_hard", "high")}
        return params

    def initial_messages(self, req: StageRequest) -> list:
        return [{"role": "user", "content": req.kickoff}]

    def before_call(self, messages: list) -> None:
        pass  # OpenAI caches input server-side; no explicit breakpoints

    def flush_params(self, params: dict) -> dict:
        return {k: v for k, v in params.items() if k != "reasoning"}

    async def call(self, system, tools, messages, params, tool_choice) -> Turn:
        resp = await self.client.responses.create(
            instructions=system, tools=tools, tool_choice=tool_choice,
            input=messages, **params,
        )
        # Echo the model's own output items (reasoning + message + function_call) back into the
        # running input so the next turn preserves call_id/phase linkage.
        messages += list(_get(resp, "output", []) or [])
        usage, reasoning = _usage_of(resp)
        calls = _tool_calls_of(resp)
        if calls:
            stop = "tool_use"
        elif _incomplete_maxed(resp):
            stop = "max_turns"
        else:
            stop = "completed"
        return Turn(text=_text_of(resp), tool_calls=calls, usage=usage, stop=stop,
                    extra={"status": _get(resp, "status"), "reasoning_tokens": reasoning})

    def append_tool_results(self, messages: list, results: list) -> None:
        for r in results:
            messages.append({
                "type": "function_call_output", "call_id": r["id"], "output": r["content"],
            })

    def append_user_text(self, messages: list, text: str) -> None:
        messages.append({"role": "user", "content": text})
