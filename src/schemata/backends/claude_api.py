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

import json
from datetime import datetime, timezone

from ..core.config import RUNS_DIR
from ..core.models import Artifacts, StageRequest, StageResult, Usage
from ..core.util import extract_last_json, truncate
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


def _jsonable(value):
    """Best-effort serializer for Anthropic SDK blocks and test doubles."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {
            k: _jsonable(v)
            for k, v in vars(value).items()
            if not k.startswith("_")
        }
    return repr(value)


def _under(path, root) -> bool:
    try:
        p = path.resolve()
        r = root.resolve()
        return p == r or r in p.parents
    except Exception:
        return False


class StageTrace:
    """Append-only local trace of the API/tool loop for post-mortem debugging.

    This records only content the backend actually sees: prompts, assistant content
    blocks, tool calls/results, usage, and final parsed JSON. It cannot expose hidden
    model reasoning that the provider does not return.
    """
    def __init__(self, req: StageRequest, settings):
        base = req.cwd.parent if req.cwd.name == "task" else req.cwd
        self.path = base / f"stage_{req.stage}_trace.jsonl"
        cfg = (getattr(settings, "raw", {}) or {}).get("logging", {})
        configured = cfg.get("trace_api_messages")
        self.enabled = bool(configured) if configured is not None else _under(base, RUNS_DIR)

    def write(self, event: str, **fields) -> None:
        if not self.enabled:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            row = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event,
                **fields,
            }
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_jsonable(row), ensure_ascii=False) + "\n")
        except Exception:
            # Tracing is diagnostic only; never break a benchmark run because disk
            # logging failed.
            pass


# Forces the contract JSON when a stage burned its whole tool budget (or finished talking)
# without ever emitting it — see the flush block in run_stage.
_FLUSH_MSG = (
    "You have used your tool budget — do NOT call any more tools. Emit ONLY the final JSON "
    "block required by this stage's output contract, right now, from what you ACTUALLY found. "
    "Rules: (1) ALWAYS fill `vuln_classes` by classifying description.txt against the menu — "
    "that needs no code reading. (2) For localization fields (suspected_files, "
    "suspected_functions, sink, harness): include ONLY what you genuinely verified and leave "
    "the rest empty — do NOT invent a sink/file you did not confirm. A later, stronger stage "
    "finishes localization, and a confident wrong guess would mislead it; an empty field "
    "correctly signals 'not found yet'. Emit the JSON object now."
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
        trace = StageTrace(req, self.settings)

        messages: list[dict] = [{"role": "user", "content": req.kickoff}]
        usage = Usage(model=req.model)
        last_text = ""
        stop = "max_turns"
        error: str | None = None
        trace.write(
            "stage_start",
            stage=req.stage,
            model=req.model,
            cwd=str(req.cwd),
            max_turns=req.max_turns,
            max_budget_usd=req.max_budget_usd,
            allowed_tools=req.allowed_tools,
            permission_tier=req.permission_tier,
            thinking=req.thinking.model_dump() if req.thinking else None,
            system=system,
            params=params,
            kickoff=req.kickoff,
            prior_results=req.prior_results,
        )

        for _turn in range(req.max_turns):
            prompt_cache.with_breakpoints(messages)
            try:
                trace.write(
                    "api_request",
                    turn=_turn,
                    tool_choice={"type": "auto"},
                    message_count=len(messages),
                    messages=messages,
                )
                async with self.client.messages.stream(
                    system=system, tools=toolset, tool_choice={"type": "auto"},
                    messages=messages, **params,
                ) as stream:
                    msg = await stream.get_final_message()
            except Exception as e:  # network / API / SDK error -> match claude_code's error path
                err = truncate(f"anthropic: {e}", 1500, 500)
                trace.write("api_error", turn=_turn, error=err)
                return StageResult(
                    stage=req.stage, usage=usage, cost_usd=cost_of(usage, req.model),
                    stop_reason="error", error=err,
                )

            turn_usage = _usage_of(msg, req.model)
            usage = usage + turn_usage
            messages.append({"role": "assistant", "content": msg.content})
            txt = _text_of(msg)
            if txt:
                last_text = txt
            trace.write(
                "assistant_message",
                turn=_turn,
                stop_reason=msg.stop_reason,
                usage=turn_usage.model_dump(),
                content=msg.content,
                text=txt,
            )

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
            trace_results = []
            for tu in tool_uses:
                out, is_err = await disp.execute(tu.name, dict(tu.input or {}))
                results.append({
                    "type": "tool_result", "tool_use_id": tu.id,
                    "content": out, "is_error": is_err,
                })
                trace_results.append({
                    "tool_use_id": tu.id,
                    "name": tu.name,
                    "input": dict(tu.input or {}),
                    "result": out,
                    "is_error": is_err,
                })
            messages.append({"role": "user", "content": results})
            trace.write(
                "tool_results",
                turn=_turn,
                results=trace_results,
                crash_found=disp.crash_found,
                winning_poc=disp.winning_poc,
                failures=disp.failures,
                consecutive_nocrash=disp.consec_nocrash,
            )

            # In local mode the cybergym server's "crashed" verdict IS the scoring signal,
            # so we can stop immediately. In A2A (arena) mode the green only tests against
            # the vul binary — a crash there might be a false positive (also crashes fix,
            # scoring 0). Let the agent see the verdict, compare the sanitizer trace to
            # error_intel.summary, and decide whether to stop or refine. max_iters bounds
            # the loop. See skills/stages/generate.md, critical_scoring_rule.
            if disp.crash_found and req.submit_fn is None:
                stop = "crash_found"
                trace.write("stage_stop_trigger", turn=_turn, reason=stop)
                break
            if disp.should_early_stop():
                stop = "early_stop"
                trace.write("stage_stop_trigger", turn=_turn, reason=stop)
                break
            if req.max_budget_usd and cost_of(usage, req.model) >= req.max_budget_usd:
                stop = "early_stop"
                trace.write("stage_stop_trigger", turn=_turn, reason="budget")
                break

        cost = cost_of(usage, req.model)
        structured = extract_last_json(last_text)

        # JSON-flush fallback: a multi-turn stage can burn its entire tool budget exploring and
        # exit (stop_reason=max_turns) — or finish talking — without ever emitting its contract
        # JSON, leaving structured_output empty and starving the next stage (e.g. recon on a huge
        # repo never emits vuln_classes, so Stage-3 example injection gets nothing). One final
        # no-tools turn forces the deliverable from what it already found. Cheap (no tool calls)
        # and only fires when we'd otherwise return empty.
        if not structured and stop in ("max_turns", "completed") and error is None:
            try:
                if (messages and messages[-1]["role"] == "user"
                        and isinstance(messages[-1]["content"], list)):
                    messages[-1]["content"].append({"type": "text", "text": _FLUSH_MSG})
                else:
                    messages.append({"role": "user", "content": _FLUSH_MSG})
                prompt_cache.with_breakpoints(messages)
                flush_params = {k: v for k, v in params.items() if k != "thinking"}
                trace.write(
                    "json_flush_request",
                    tool_choice={"type": "none"},
                    message_count=len(messages),
                    messages=messages,
                )
                async with self.client.messages.stream(
                    system=system, tools=toolset, tool_choice={"type": "none"},
                    messages=messages, **flush_params,
                ) as stream:
                    fmsg = await stream.get_final_message()
                flush_usage = _usage_of(fmsg, req.model)
                usage = usage + flush_usage
                cost = cost_of(usage, req.model)
                ftext = _text_of(fmsg)
                if ftext:
                    last_text = ftext
                    structured = extract_last_json(ftext) or structured
                trace.write(
                    "json_flush_response",
                    stop_reason=fmsg.stop_reason,
                    usage=flush_usage.model_dump(),
                    content=fmsg.content,
                    text=ftext,
                    structured_output=structured,
                )
            except Exception as e:
                trace.write("json_flush_error", error=truncate(str(e), 1500, 500))
                pass  # keep the empty structured — no worse than before the flush

        artifacts = Artifacts(submissions=disp.submissions)
        if req.stage == "generate":
            artifacts.poc_path = disp.winning_poc or structured.get("winning_poc_path")
        trace.write(
            "stage_end",
            stop_reason=stop,
            error=error,
            cost_usd=cost,
            usage=usage.model_dump(),
            structured_output=structured,
            artifacts=artifacts.model_dump(),
            transcript_tail=truncate(last_text, 3000, 1000),
        )

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
