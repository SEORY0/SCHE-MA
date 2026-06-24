"""Claude API backend — Anthropic Messages adapter for the shared agentic-stage driver.

The multi-turn policy (checkpoints, auto-probe, early-stop, JSON-flush, StageResult assembly)
lives in `_agentic.run_agentic_stage`. This module supplies the Anthropic wire format: building
the cached system blocks + tools, calling `messages.stream`, and shaping assistant/tool_result
messages and the rolling prompt-cache breakpoint.
"""
from __future__ import annotations

from ..core.models import StageRequest, StageResult, Usage
from . import prompt_cache
from ._agentic import Turn, run_agentic_stage
from .base import AgentBackend
from .tools import permissions


def _usage_of(msg) -> Usage:
    u = getattr(msg, "usage", None)
    if u is None:
        return Usage()
    return Usage(
        input_tokens=int(getattr(u, "input_tokens", 0) or 0),
        output_tokens=int(getattr(u, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
        cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
    )


def _text_of(msg) -> str:
    return "".join(
        getattr(b, "text", "") for b in msg.content
        if getattr(b, "type", None) == "text"
    )


class ClaudeApiBackend(AgentBackend):
    name = "claude_api"
    provider = "anthropic"
    AUTO = {"type": "auto"}
    NONE = {"type": "none"}

    def __init__(self, settings, client=None):
        super().__init__(settings)
        if client is not None:
            self.client = client
        else:
            import anthropic  # lazy: tests inject a fake client and never import the SDK
            self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def run_stage(self, req: StageRequest) -> StageResult:
        return await run_agentic_stage(self, req)

    # -- Adapter hooks ------------------------------------------------------------
    def build_tools(self, req: StageRequest) -> list[dict]:
        return permissions.tools_for(req)

    def build_system(self, req: StageRequest):
        return prompt_cache.system_blocks(req)

    def build_params(self, req: StageRequest) -> dict:
        return prompt_cache.model_params(req, self.settings)

    def initial_messages(self, req: StageRequest) -> list:
        return [{"role": "user", "content": req.kickoff}]

    def before_call(self, messages: list) -> None:
        prompt_cache.with_breakpoints(messages)

    def flush_params(self, params: dict) -> dict:
        return {k: v for k, v in params.items() if k != "thinking"}

    async def call(self, system, tools, messages, params, tool_choice) -> Turn:
        async with self.client.messages.stream(
            system=system, tools=tools, tool_choice=tool_choice,
            messages=messages, **params,
        ) as stream:
            msg = await stream.get_final_message()
        messages.append({"role": "assistant", "content": msg.content})
        tool_uses = [b for b in msg.content if getattr(b, "type", None) == "tool_use"]
        calls = [{"id": tu.id, "name": tu.name, "input": dict(tu.input or {})} for tu in tool_uses]
        if msg.stop_reason == "tool_use":
            stop = "tool_use" if calls else "completed"
        elif msg.stop_reason == "max_tokens":
            stop = "max_turns"
        elif msg.stop_reason == "refusal":
            stop = "refusal"
        else:
            stop = "completed"
        return Turn(text=_text_of(msg), tool_calls=calls, usage=_usage_of(msg), stop=stop,
                    extra={"stop_reason": msg.stop_reason})

    def append_tool_results(self, messages: list, results: list) -> None:
        messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": r["id"],
             "content": r["content"], "is_error": r["is_error"]}
            for r in results
        ]})

    def append_user_text(self, messages: list, text: str) -> None:
        if (messages and messages[-1]["role"] == "user"
                and isinstance(messages[-1]["content"], list)):
            messages[-1]["content"].append({"type": "text", "text": text})
        else:
            messages.append({"role": "user", "content": text})
