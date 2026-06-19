#!/usr/bin/env python
"""Merge SCHE-MA run traces into one readable Markdown file.

Default output keeps the useful transcript path:
stage metadata, assistant text/tool calls, tool results, JSON flushes, and the
final stage summary. API request events are skipped by default because they
duplicate the full conversation on every turn; pass --include-api-requests when
you need the exact raw request payloads too.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


STAGE_ORDER = {"recon": 0, "analyze": 1, "generate": 2, "discriminate": 3}
DEFAULT_OUTPUT = Path("runs") / "combined_traces.md"
DEFAULT_SPLIT_DIR = Path("runs") / "combined_reasoning"


@dataclass(frozen=True)
class TraceFile:
    run_id: str
    task_id: str
    stage: str
    path: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Combine runs/*/*/stage_*_trace.jsonl into one Markdown file.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=Path("runs"),
        help="Directory containing run folders. Default: runs",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output Markdown path. Default: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--include-api-requests",
        action="store_true",
        help="Include raw api_request/json_flush_request message payloads.",
    )
    parser.add_argument(
        "--include-prompts",
        action="store_true",
        help="Include full stage_start system and prior_results payloads.",
    )
    parser.add_argument(
        "--split-by-experiment",
        action="store_true",
        help="Also write one integrated Markdown reasoning file per run/task experiment.",
    )
    parser.add_argument(
        "--split-dir",
        type=Path,
        default=DEFAULT_SPLIT_DIR,
        help=f"Directory for --split-by-experiment output. Default: {DEFAULT_SPLIT_DIR}",
    )
    parser.add_argument(
        "--max-block-chars",
        type=int,
        default=12000,
        help="Max characters per rendered text/JSON block. Use 0 for no limit.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                rows.append({
                    "event": "parse_error",
                    "line_no": line_no,
                    "error": str(exc),
                    "raw": line,
                })
    return rows


def discover_traces(runs_dir: Path) -> list[TraceFile]:
    traces: list[TraceFile] = []
    for path in runs_dir.glob("*/*/stage_*_trace.jsonl"):
        rel = path.relative_to(runs_dir)
        run_id = rel.parts[0]
        task_id = rel.parts[1]
        stage = path.name.removeprefix("stage_").removesuffix("_trace.jsonl")
        traces.append(TraceFile(run_id=run_id, task_id=task_id, stage=stage, path=path))

    return sorted(
        traces,
        key=lambda t: (
            t.run_id,
            t.task_id,
            STAGE_ORDER.get(t.stage, 99),
            t.stage,
            str(t.path),
        ),
    )


def clip(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    head = max(0, limit // 2)
    tail = max(0, limit - head)
    omitted = len(text) - head - tail
    return (
        text[:head].rstrip()
        + f"\n\n...[truncated {omitted} chars]...\n\n"
        + text[-tail:].lstrip()
    )


def code_fence(text: str, lang: str = "", *, max_chars: int = 12000) -> str:
    text = "" if text is None else str(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = clip(text, max_chars).rstrip("\n")
    fence_len = 3
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            fence_len = max(fence_len, len(stripped) + 1)
    fence = "`" * fence_len
    return f"{fence}{lang}\n{text}\n{fence}\n"


def json_block(value: Any, *, max_chars: int = 12000) -> str:
    return code_fence(
        json.dumps(value, ensure_ascii=False, indent=2),
        "json",
        max_chars=max_chars,
    )


def plain_block(text: str, *, max_chars: int = 12000) -> str:
    return code_fence(text, "text", max_chars=max_chars)


def section(title: str, level: int = 2) -> str:
    return f"{'#' * level} {title}\n\n"


def bullet(label: str, value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"- {label}: `{value}`\n"


def short_usage(usage: dict[str, Any] | None) -> str:
    if not usage:
        return ""
    keys = ("model", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")
    parts = [f"{k}={usage[k]}" for k in keys if k in usage]
    return ", ".join(parts)


def render_doc_header(traces: list[TraceFile], runs_dir: Path) -> str:
    run_ids = sorted({t.run_id for t in traces})
    task_ids = sorted({t.task_id for t in traces})
    lines = [
        "# SCHE-MA Combined Trace Report",
        "",
        f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`",
        f"- Runs dir: `{runs_dir}`",
        f"- Runs: `{len(run_ids)}`",
        f"- Tasks: `{len(task_ids)}`",
        f"- Trace files: `{len(traces)}`",
        "",
        "## Index",
        "",
        "| Run | Task | Stage | Trace file |",
        "| --- | --- | --- | --- |",
    ]
    for trace in traces:
        lines.append(f"| `{trace.run_id}` | `{trace.task_id}` | `{trace.stage}` | `{trace.path}` |")
    lines.append("")
    return "\n".join(lines)


def render_outcome(task_dir: Path, *, max_chars: int) -> str:
    path = task_dir / "outcome.json"
    if not path.exists():
        return ""
    outcome_doc = load_json(path)
    outcome = outcome_doc.get("outcome", {})
    plan = outcome_doc.get("plan", {})
    lines = [section("Outcome", 4)]
    lines.append(bullet("success", outcome.get("success")))
    lines.append(bullet("final_exit_code", outcome.get("final_exit_code")))
    lines.append(bullet("poc_id", outcome.get("poc_id")))
    lines.append(bullet("cost_usd", outcome.get("cost_usd")))
    lines.append(bullet("stages_run", ", ".join(outcome.get("stages_run", []) or [])))
    lines.append(bullet("error", outcome.get("error")))
    lines.append(bullet("difficulty", plan.get("difficulty")))
    lines.append(bullet("stage_models", json.dumps(plan.get("stage_models", {}), ensure_ascii=False)))
    lines.append("\n")
    return "".join(lines)


def render_stage_start(row: dict[str, Any], *, include_prompts: bool, max_chars: int) -> str:
    lines = [section("Stage Start", 4)]
    for key in ("ts", "stage", "model", "cwd", "max_turns", "max_budget_usd", "permission_tier"):
        lines.append(bullet(key, row.get(key)))
    allowed_tools = row.get("allowed_tools") or []
    if allowed_tools:
        lines.append(bullet("allowed_tools", ", ".join(allowed_tools)))
    params = row.get("params")
    if params:
        lines.append(bullet("params", json.dumps(params, ensure_ascii=False)))
    kickoff = row.get("kickoff")
    if kickoff:
        lines.append("\nKickoff:\n\n")
        lines.append(plain_block(kickoff, max_chars=max_chars))
    if include_prompts:
        if row.get("system") is not None:
            lines.append("\nSystem:\n\n")
            lines.append(json_block(row.get("system"), max_chars=max_chars))
        if row.get("prior_results") is not None:
            lines.append("\nPrior Results:\n\n")
            lines.append(json_block(row.get("prior_results"), max_chars=max_chars))
    return "".join(lines)


def render_tool_use(item: dict[str, Any], *, max_chars: int) -> str:
    name = item.get("name", "<tool>")
    tool_id = item.get("id", "")
    lines = [f"Tool Call: `{name}`"]
    if tool_id:
        lines.append(f"ID: `{tool_id}`")
    input_value = item.get("input", {})
    return "\n".join(lines) + "\n\nInput:\n\n" + json_block(input_value, max_chars=max_chars)


def render_assistant(row: dict[str, Any], *, max_chars: int) -> str:
    turn = row.get("turn")
    title = "Assistant"
    if turn is not None:
        title += f" Turn {turn}"
    lines = [section(title, 4)]
    lines.append(bullet("ts", row.get("ts")))
    lines.append(bullet("stop_reason", row.get("stop_reason")))
    usage = short_usage(row.get("usage"))
    if usage:
        lines.append(bullet("usage", usage))
    if lines[-1] != "\n":
        lines.append("\n")

    content = row.get("content") or []
    if not isinstance(content, list):
        content = []

    text_rendered = False
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text" and item.get("text"):
            lines.append("Text:\n\n")
            lines.append(plain_block(item.get("text", ""), max_chars=max_chars))
            text_rendered = True
        elif kind == "tool_use":
            lines.append(render_tool_use(item, max_chars=max_chars))
            lines.append("\n")

    if not text_rendered and row.get("text"):
        lines.append("Text:\n\n")
        lines.append(plain_block(row.get("text", ""), max_chars=max_chars))

    return "".join(lines)


def render_tool_results(row: dict[str, Any], *, max_chars: int) -> str:
    turn = row.get("turn")
    title = "Tool Results"
    if turn is not None:
        title += f" Turn {turn}"
    lines = [section(title, 4)]
    lines.append(bullet("ts", row.get("ts")))
    lines.append(bullet("crash_found", row.get("crash_found")))
    lines.append(bullet("winning_poc", row.get("winning_poc")))
    lines.append(bullet("failures", row.get("failures")))
    lines.append(bullet("consecutive_nocrash", row.get("consecutive_nocrash")))
    lines.append("\n")

    for result in row.get("results") or []:
        if not isinstance(result, dict):
            continue
        lines.append(f"Result: `{result.get('name', '<tool>')}`")
        if result.get("tool_use_id"):
            lines.append(f" (`{result['tool_use_id']}`)")
        if result.get("is_error"):
            lines.append(" [error]")
        lines.append("\n\nInput:\n\n")
        lines.append(json_block(result.get("input", {}), max_chars=max_chars))
        lines.append("Output:\n\n")
        lines.append(plain_block(result.get("result", ""), max_chars=max_chars))
        lines.append("\n")
    return "".join(lines)


def render_stage_end(row: dict[str, Any], *, max_chars: int) -> str:
    lines = [section("Stage End", 4)]
    lines.append(bullet("ts", row.get("ts")))
    lines.append(bullet("stop_reason", row.get("stop_reason")))
    lines.append(bullet("cost_usd", row.get("cost_usd")))
    lines.append(bullet("error", row.get("error")))
    usage = short_usage(row.get("usage"))
    if usage:
        lines.append(bullet("usage", usage))
    artifacts = row.get("artifacts")
    if artifacts:
        lines.append("\nArtifacts:\n\n")
        lines.append(json_block(artifacts, max_chars=max_chars))
    structured = row.get("structured_output")
    if structured:
        lines.append("\nStructured Output:\n\n")
        lines.append(json_block(structured, max_chars=max_chars))
    transcript_tail = row.get("transcript_tail")
    if transcript_tail:
        lines.append("\nTranscript Tail:\n\n")
        lines.append(plain_block(transcript_tail, max_chars=max_chars))
    return "".join(lines)


def render_simple_json_event(row: dict[str, Any], *, max_chars: int) -> str:
    event = row.get("event", "<event>")
    title = event.replace("_", " ").title()
    return section(title, 4) + json_block(row, max_chars=max_chars)


def render_trace_rows(
    rows: Iterable[dict[str, Any]],
    *,
    include_api_requests: bool,
    include_prompts: bool,
    max_chars: int,
) -> str:
    lines: list[str] = []
    skipped_api = 0
    for row in rows:
        event = row.get("event")
        if event in {"api_request", "json_flush_request"} and not include_api_requests:
            skipped_api += 1
            continue

        if event == "stage_start":
            lines.append(render_stage_start(row, include_prompts=include_prompts, max_chars=max_chars))
        elif event == "assistant_message":
            lines.append(render_assistant(row, max_chars=max_chars))
        elif event == "tool_results":
            lines.append(render_tool_results(row, max_chars=max_chars))
        elif event == "stage_end":
            lines.append(render_stage_end(row, max_chars=max_chars))
        else:
            lines.append(render_simple_json_event(row, max_chars=max_chars))
        lines.append("\n")

    if skipped_api:
        lines.insert(0, f"_Skipped `{skipped_api}` duplicated API request event(s); use `--include-api-requests` to render them._\n\n")
    return "".join(lines)


def render_trace_file(
    trace: TraceFile,
    runs_dir: Path,
    *,
    include_api_requests: bool,
    include_prompts: bool,
    max_chars: int,
) -> str:
    rows = read_jsonl(trace.path)
    task_dir = runs_dir / trace.run_id / trace.task_id
    lines = [
        section(f"Run {trace.run_id} / {trace.task_id}", 2),
        render_outcome(task_dir, max_chars=max_chars),
        section(f"Stage {trace.stage}", 3),
        f"- Trace file: `{trace.path}`\n",
        f"- Events: `{len(rows)}`\n\n",
        render_trace_rows(
            rows,
            include_api_requests=include_api_requests,
            include_prompts=include_prompts,
            max_chars=max_chars,
        ),
    ]
    return "".join(lines)


def render_stage_section(
    trace: TraceFile,
    *,
    include_api_requests: bool,
    include_prompts: bool,
    max_chars: int,
) -> str:
    rows = read_jsonl(trace.path)
    return "".join([
        section(f"Stage {trace.stage}", 2),
        f"- Trace file: `{trace.path}`\n",
        f"- Events: `{len(rows)}`\n\n",
        render_trace_rows(
            rows,
            include_api_requests=include_api_requests,
            include_prompts=include_prompts,
            max_chars=max_chars,
        ),
    ])


def render_experiment_file(
    run_id: str,
    task_id: str,
    traces: list[TraceFile],
    runs_dir: Path,
    *,
    include_api_requests: bool,
    include_prompts: bool,
    max_chars: int,
) -> str:
    task_dir = runs_dir / run_id / task_id
    lines = [
        f"# Reasoning Trace: {run_id} / {task_id}\n\n",
        f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`\n",
        f"- Run: `{run_id}`\n",
        f"- Task: `{task_id}`\n",
        f"- Stages: `{', '.join(t.stage for t in traces)}`\n",
        f"- Source dir: `{task_dir}`\n\n",
        render_outcome(task_dir, max_chars=max_chars),
        section("Stage Index", 2),
        "| Stage | Trace file |\n",
        "| --- | --- |\n",
    ]
    for trace in traces:
        lines.append(f"| `{trace.stage}` | `{trace.path}` |\n")
    lines.append("\n")
    for trace in traces:
        lines.append(render_stage_section(
            trace,
            include_api_requests=include_api_requests,
            include_prompts=include_prompts,
            max_chars=max_chars,
        ))
        lines.append("\n")
    return "".join(lines).rstrip() + "\n"


def group_by_experiment(traces: list[TraceFile]) -> dict[tuple[str, str], list[TraceFile]]:
    grouped: dict[tuple[str, str], list[TraceFile]] = {}
    for trace in traces:
        grouped.setdefault((trace.run_id, trace.task_id), []).append(trace)
    return grouped


def write_split_files(
    traces: list[TraceFile],
    runs_dir: Path,
    split_dir: Path,
    *,
    include_api_requests: bool,
    include_prompts: bool,
    max_chars: int,
) -> list[Path]:
    split_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    grouped = group_by_experiment(traces)
    index_lines = [
        "# SCHE-MA Per-Experiment Reasoning Files\n\n",
        f"- Generated: `{datetime.now().isoformat(timespec='seconds')}`\n",
        f"- Experiments: `{len(grouped)}`\n\n",
        "| Run | Task | File | Stages |\n",
        "| --- | --- | --- | --- |\n",
    ]

    for (run_id, task_id), group in grouped.items():
        file_name = f"{run_id}__{task_id}__reasoning.md"
        out_path = split_dir / file_name
        out_path.write_text(
            render_experiment_file(
                run_id,
                task_id,
                group,
                runs_dir,
                include_api_requests=include_api_requests,
                include_prompts=include_prompts,
                max_chars=max_chars,
            ),
            encoding="utf-8",
            newline="\n",
        )
        written.append(out_path)
        stages = ", ".join(trace.stage for trace in group)
        index_lines.append(f"| `{run_id}` | `{task_id}` | [{file_name}]({file_name}) | `{stages}` |\n")

    index_path = split_dir / "index.md"
    index_path.write_text("".join(index_lines), encoding="utf-8", newline="\n")
    return [index_path, *written]


def main() -> int:
    args = parse_args()
    traces = discover_traces(args.runs_dir)
    if not traces:
        raise SystemExit(f"No trace files found under {args.runs_dir}")

    parts = [render_doc_header(traces, args.runs_dir)]
    current_key: tuple[str, str] | None = None
    for trace in traces:
        key = (trace.run_id, trace.task_id)
        if key == current_key:
            rows = read_jsonl(trace.path)
            parts.extend([
                section(f"Stage {trace.stage}", 3),
                f"- Trace file: `{trace.path}`\n",
                f"- Events: `{len(rows)}`\n\n",
                render_trace_rows(
                    rows,
                    include_api_requests=args.include_api_requests,
                    include_prompts=args.include_prompts,
                    max_chars=args.max_block_chars,
                ),
            ])
        else:
            parts.append(render_trace_file(
                trace,
                args.runs_dir,
                include_api_requests=args.include_api_requests,
                include_prompts=args.include_prompts,
                max_chars=args.max_block_chars,
            ))
            current_key = key

    text = "\n\n".join(part.strip("\n") for part in parts if part).rstrip() + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8", newline="\n")

    split_written: list[Path] = []
    if args.split_by_experiment:
        split_written = write_split_files(
            traces,
            args.runs_dir,
            args.split_dir,
            include_api_requests=args.include_api_requests,
            include_prompts=args.include_prompts,
            max_chars=args.max_block_chars,
        )

    print(f"Wrote {args.output} ({len(text):,} chars, {len(traces)} trace files)")
    if split_written:
        print(f"Wrote {len(split_written) - 1} per-experiment reasoning files under {args.split_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
