"""Claude API backend — runs each stage as a multi-turn Anthropic Messages tool loop.

Mirror of the Claude Code backend's *output* contract (StageResult), built on the
Anthropic SDK instead of the `claude` CLI:

  1. messages.stream(system=cached, tools=stage toolset, tool_choice=auto, thinking?)
  2. while stop_reason == "tool_use": dispatcher.execute() each tool_use -> tool_result
  3. accumulate usage every turn (incl. cache_creation/cache_read); stop on a final text
     turn, max_turns, a crash (submit_poc exit_code != 0), Stage-3 early-stop, or budget.

The agent submits PoCs via the `submit_poc` tool (SubmitClient); the orchestrator still
re-confirms the winner independently. Caching + model params come from prompt_cache.
"""
from __future__ import annotations

from ..models import Artifacts, StageRequest, StageResult, Usage
from ..util import extract_last_json, truncate
from . import prompt_cache
from .base import AgentBackend, cost_of
from .tools import permissions
from .tools.dispatcher import Dispatcher


def _usage_of(msg, model: str) -> Usage:
    u = getattr(msg, "usage", None)
    if u is None:
        return Usage(model=model)
    return Usage(
        model=model,
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

    def __init__(self, settings, client=None):
        super().__init__(settings)
        if client is not None:
            self.client = client
        else:
            import anthropic  # lazy: tests inject a fake client and never import the SDK
            self.client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def run_stage(self, req: StageRequest) -> StageResult:
        toolset = permissions.tools_for(req)
        disp = Dispatcher(req, self.settings)
        system = prompt_cache.system_blocks(req)
        params = prompt_cache.model_params(req, self.settings)

        messages: list[dict] = [{"role": "user", "content": req.kickoff}]
        usage = Usage(model=req.model)
        last_text = ""
        stop = "max_turns"
        error: str | None = None

        for _turn in range(req.max_turns):
            prompt_cache.with_breakpoints(messages)
            try:
                async with self.client.messages.stream(
                    system=system, tools=toolset, tool_choice={"type": "auto"},
                    messages=messages, **params,
                ) as stream:
                    msg = await stream.get_final_message()
            except Exception as e:  # network / API / SDK error -> match claude_code's error path
                return StageResult(
                    stage=req.stage, usage=usage, cost_usd=cost_of(usage, req.model),
                    stop_reason="error", error=truncate(f"anthropic: {e}", 1500, 500),
                )

            usage = usage + _usage_of(msg, req.model)
            messages.append({"role": "assistant", "content": msg.content})
            txt = _text_of(msg)
            if txt:
                last_text = txt

            if msg.stop_reason != "tool_use":
                if msg.stop_reason == "max_tokens":
                    stop = "max_turns"
                elif msg.stop_reason == "refusal":
                    stop, error = "error", "model refused"
                else:
                    stop = "completed"
                break

            tool_uses = [b for b in msg.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                stop = "completed"
                break

            results = []
            for tu in tool_uses:
                out, is_err = await disp.execute(tu.name, dict(tu.input or {}))
                results.append({
                    "type": "tool_result", "tool_use_id": tu.id,
                    "content": out, "is_error": is_err,
                })
            messages.append({"role": "user", "content": results})

            if disp.crash_found:
                stop = "crash_found"
                break
            if disp.should_early_stop():
                stop = "early_stop"
                break
            if req.max_budget_usd and cost_of(usage, req.model) >= req.max_budget_usd:
                stop = "early_stop"
                break

        cost = cost_of(usage, req.model)
        structured = extract_last_json(last_text)
        artifacts = Artifacts(submissions=disp.submissions)
        if req.stage == "generate":
            artifacts.poc_path = disp.winning_poc or structured.get("winning_poc_path")

        return StageResult(
            stage=req.stage,
            structured_output=structured,
            raw_transcript_tail=truncate(last_text, 3000, 1000),
            usage=usage,
            cost_usd=cost,
            artifacts=artifacts,
            stop_reason=stop,
            error=error,
        )
