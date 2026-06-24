"""Shared multi-turn agentic-stage driver for the API backends.

Both API backends (Anthropic Messages, OpenAI Responses) run the SAME stage policy and differ
only in wire format. The policy lives here:

  1. call the model with the stage toolset until it stops asking for tools
  2. dispatch each tool call through the provider-agnostic Dispatcher -> tool results
  3. accumulate usage; nudge the generate stage at 30/50/70% if it still has 0 submissions;
     auto-probe at 80%; stop on a crash (local mode), early-stop, budget, or a final turn
  4. if the stage ended without emitting its contract JSON, force it with one no-tools flush turn

Each backend supplies a thin `Adapter` (the backend object itself) that owns the wire format:
building the request, calling the SDK, and mutating the running message list. The driver never
touches provider-specific shapes — it speaks only `Turn` and normalized tool-call dicts.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..core.config import RUNS_DIR
from ..core.models import Artifacts, StageRequest, StageResult, Usage
from ..core.util import extract_last_json, truncate
from .base import cost_of, model_id_of, provider_of
from .tools.dispatcher import Dispatcher


@dataclass
class Turn:
    """One model turn, normalized across providers.

    `tool_calls` is a list of {"id", "name", "input"} dicts (input already decoded to a dict).
    `stop` is one of: "tool_use" (asked for tools), "completed", "max_turns", "refusal".
    """
    text: str
    tool_calls: list
    usage: Usage
    stop: str
    extra: dict = field(default_factory=dict)  # provider diagnostics for the trace


def _jsonable(value):
    """Best-effort serializer for SDK blocks and test doubles."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return _jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {k: _jsonable(v) for k, v in vars(value).items() if not k.startswith("_")}
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

    Records only content the backend actually sees: prompts, assistant text, tool calls/results,
    usage, and final parsed JSON. It cannot expose hidden model reasoning the provider withholds.
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
            row = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_jsonable(row), ensure_ascii=False) + "\n")
        except Exception:
            # Tracing is diagnostic only; never break a benchmark run because disk logging failed.
            pass


# Forces the contract JSON when a stage burned its whole tool budget (or finished talking)
# without ever emitting it — see the flush block in run_agentic_stage.
_CHECKPOINT_EARLY = (
    "📋 PROGRESS CHECK ({pct}% of turns used, {turn}/{max_turns}): You have 0 submissions so far. "
    "Start transitioning from analysis to PoC construction. Identify the exact bytes you need "
    "to write and the tool/command to build them. If in-repo seeds exist, copy one now as your "
    "starting point — seed mutation is faster than building from scratch."
)

_CHECKPOINT_MID = (
    "⚠ CHECKPOINT ({pct}% of turns used, {turn}/{max_turns}): Still 0 submissions. "
    "STOP reading code — switch to PoC construction NOW.\n\n"
    "Use what you already know:\n"
    "1. Build a PoC from the prior stages' `poc_structure` / `construction_plan` / "
    "`generation_strategy` — they are in the prior JSON above. Do NOT re-derive them.\n"
    "2. `python3 -c 'import sys; sys.stdout.buffer.write(...)' > poc`\n"
    "3. Submit immediately via `submit_poc`. An imperfect attempt that returns a "
    "sanitizer trace is infinitely more valuable than more code reading.\n"
    "4. Use the trace from the first submit to refine subsequent attempts."
)

_CHECKPOINT_LATE = (
    "🚨 CRITICAL ({pct}% of turns used, {turn}/{max_turns}): Still 0 submissions — "
    "you WILL score 0 if you don't submit. SUBMIT YOUR BEST CANDIDATE RIGHT NOW.\n\n"
    "Even a minimal/imperfect PoC is better than nothing:\n"
    "- If you have ANY file ready: `submit_poc` it immediately.\n"
    "- If you have nothing: write the simplest possible trigger "
    "(e.g. a 1-byte file, a seed copy, the magic bytes + minimal header with one bad field) "
    "and submit. The server's sanitizer output will guide your next attempt.\n\n"
    "If you have not submitted by the end of this stage, the task scores 0."
)

_CHECKPOINT_FORCED = (
    "🚨 AUTO-PROBE ({pct}% of turns used, {turn}/{max_turns}): You still have 0 submissions. "
    "The harness auto-submitted a minimal probe to get server feedback.\n\n"
    "**Probe result:**\n```\n{probe_output}\n```\n\n"
    "Use this server output to understand what the binary expects. "
    "Build a PoC based on this feedback and SUBMIT IT in the remaining turns."
)

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


async def run_agentic_stage(backend, req: StageRequest) -> StageResult:
    """Drive one stage to a StageResult. `backend` implements the Adapter hooks below."""
    settings = backend.settings
    trace = StageTrace(req, settings)
    toolset = backend.build_tools(req)
    disp = Dispatcher(req, settings)
    system = backend.build_system(req)
    params = backend.build_params(req)

    messages = backend.initial_messages(req)
    usage = Usage(model=req.model)
    last_text = ""
    stop = "max_turns"
    error: str | None = None
    trace.write(
        "stage_start", stage=req.stage, model=req.model, provider=provider_of(req.model),
        model_id=model_id_of(req.model), cwd=str(req.cwd), max_turns=req.max_turns,
        max_budget_usd=req.max_budget_usd, allowed_tools=req.allowed_tools,
        permission_tier=req.permission_tier,
        thinking=req.thinking.model_dump() if req.thinking else None,
        params=params, kickoff=req.kickoff, prior_results=req.prior_results,
    )

    for _turn in range(req.max_turns):
        backend.before_call(messages)
        try:
            trace.write("api_request", turn=_turn, tool_choice=backend.AUTO,
                        message_count=len(messages))
            turn = await backend.call(system, toolset, messages, params, backend.AUTO)
        except Exception as e:  # network / API / SDK error
            err = truncate(f"{backend.provider}: {e}", 1500, 500)
            trace.write("api_error", turn=_turn, error=err)
            return StageResult(stage=req.stage, usage=usage,
                               cost_usd=cost_of(usage, req.model),
                               stop_reason="error", error=err)

        usage = usage + turn.usage
        if turn.text:
            last_text = turn.text
        trace.write("assistant_message", turn=_turn, stop=turn.stop,
                    usage=turn.usage.model_dump(), text=turn.text,
                    tool_calls=len(turn.tool_calls), **turn.extra)

        if turn.stop != "tool_use":
            if turn.stop == "refusal":
                stop, error = "error", "model refused"
            elif turn.stop == "max_turns":
                stop = "max_turns"
            else:
                stop = "completed"
            break
        if not turn.tool_calls:
            stop = "completed"
            break

        results, trace_results = [], []
        for tc in turn.tool_calls:
            out, is_err = await disp.execute(tc["name"], tc["input"])
            results.append({"id": tc["id"], "content": out, "is_error": is_err})
            trace_results.append({"id": tc["id"], "name": tc["name"],
                                  "input": tc["input"], "result": out, "is_error": is_err})
        backend.append_tool_results(messages, results)
        trace.write("tool_results", turn=_turn, results=trace_results,
                    crash_found=disp.crash_found, winning_poc=disp.winning_poc,
                    failures=disp.failures, consecutive_nocrash=disp.consec_nocrash)

        # Graduated checkpoints: nudge at 30/50/70% of turns if generate still has 0 submissions.
        if req.stage == "generate" and not disp.submissions:
            cp_pct = _turn / req.max_turns
            cp_template = None
            if _turn == int(req.max_turns * 0.3):
                cp_template = _CHECKPOINT_EARLY
            elif _turn == int(req.max_turns * 0.5):
                cp_template = _CHECKPOINT_MID
            elif _turn == int(req.max_turns * 0.7):
                cp_template = _CHECKPOINT_LATE
            if cp_template is not None:
                backend.append_user_text(messages, cp_template.format(
                    pct=int(cp_pct * 100), turn=_turn, max_turns=req.max_turns))
                trace.write("generate_checkpoint", turn=_turn, submissions=0,
                            level=int(cp_pct * 100))

        # 80% forced auto-probe to obtain server feedback on the agent's behalf.
        if (req.stage == "generate" and not disp.submissions
                and _turn == int(req.max_turns * 0.8)):
            probe_output = await disp.auto_probe_submit()
            if probe_output is not None:
                backend.append_user_text(messages, _CHECKPOINT_FORCED.format(
                    pct=int((_turn / req.max_turns) * 100), turn=_turn,
                    max_turns=req.max_turns, probe_output=probe_output))
                trace.write("auto_probe", turn=_turn, probe_output=probe_output,
                            crash_found=disp.crash_found)

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

    # JSON-flush fallback: one no-tools turn to force the contract JSON when the stage exited
    # (max_turns / completed) without ever emitting it. Cheap; only fires when we'd return empty.
    if not structured and stop in ("max_turns", "completed") and error is None:
        try:
            backend.append_user_text(messages, _FLUSH_MSG)
            backend.before_call(messages)
            flush_params = backend.flush_params(params)
            trace.write("json_flush_request", tool_choice=backend.NONE,
                        message_count=len(messages))
            fturn = await backend.call(system, toolset, messages, flush_params, backend.NONE)
            usage = usage + fturn.usage
            cost = cost_of(usage, req.model)
            if fturn.text:
                last_text = fturn.text
                structured = extract_last_json(fturn.text) or structured
            trace.write("json_flush_response", stop=fturn.stop,
                        usage=fturn.usage.model_dump(), text=fturn.text,
                        structured_output=structured)
        except Exception as e:
            trace.write("json_flush_error", error=truncate(str(e), 1500, 500))

    artifacts = Artifacts(submissions=disp.submissions)
    if disp.tool_calls:
        artifacts.extra["tool_calls"] = dict(disp.tool_calls)
    if req.stage == "generate":
        artifacts.poc_path = disp.winning_poc or structured.get("winning_poc_path")
    trace.write("stage_end", stop_reason=stop, error=error, cost_usd=cost,
                usage=usage.model_dump(), structured_output=structured,
                artifacts=artifacts.model_dump(),
                transcript_tail=truncate(last_text, 3000, 1000))

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
