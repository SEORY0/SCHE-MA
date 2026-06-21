"""Slash command registry for the SCHE-MA REPL.

Each command is `fn(session, args: list[str]) -> str | None`. Return a string to
print, or None for silence. Raise `ExitREPL` to terminate the loop.
"""
from __future__ import annotations

import asyncio

from ..backends.base import MODEL_IDS
from ..core.config import DATA_DIR, RUNS_DIR
from ..core.cost_tracker import CostTracker
from ..pipeline import orchestrator
from . import ui


class ExitREPL(Exception):
    """Raised to terminate the REPL."""


def cmd_help(session, args):
    ui.print_help_table()
    return None


def cmd_backend(session, args):
    if not args:
        ui.print_kv([("backend", session.backend)])
        return None
    name = args[0].strip()
    if name not in ("claude_code", "claude_api"):
        ui.print_err(f"unknown backend {name!r}; pick claude_code or claude_api")
        return None
    session.backend = name
    ui.print_ok(f"backend → [brand]{name}[/brand]")
    return None


def cmd_model(session, args):
    if not args:
        ui.print_kv([("model", f"{session.model_alias} ({MODEL_IDS.get(session.model_alias, '?')})")])
        return None
    alias = args[0].strip()
    if alias not in MODEL_IDS:
        ui.print_err(f"unknown model {alias!r}; pick one of {list(MODEL_IDS)}")
        return None
    session.model_alias = alias
    ui.print_ok(f"model → [brand]{alias}[/brand] [muted]({MODEL_IDS[alias]})[/muted]")
    return None


def cmd_config(session, args):
    s = session.settings
    ui.print_kv([
        ("backend",         session.backend),
        ("model",           session.model_alias),
        ("server_url",      s.server_url),
        ("budget_total",    f"${s.budget_total_usd:.2f}"),
        ("per_task_soft",   f"${s.per_task_soft_usd:.2f}"),
        ("api_key",         "set" if s.anthropic_api_key else "[warn]unset[/warn]"),
    ], title="config")
    return None


def cmd_cost(session, args):
    ui.print_kv([
        ("session_cost", f"${session.cost_usd:.4f}"),
        ("tasks_run",    str(session.tasks_run)),
    ], title="cost")
    return None


def cmd_clear(session, args):
    ui.console.clear()
    return None


def cmd_exit(session, args):
    raise ExitREPL


def cmd_task(session, args):
    if not args:
        ui.print_warn("usage: /task <task_id>   e.g. /task arvo:10400")
        return None
    task_id = args[0].strip()
    cost = CostTracker(session.settings.budget_total_usd, session.settings.per_task_soft_usd)
    run_id = session._next_run_id()
    with ui.status(f"running {task_id}…"):
        outcome = asyncio.run(
            orchestrator.run_task(task_id, session.backend, session.settings, cost, run_id)
        )
    cost.write(RUNS_DIR / run_id / "cost.json")
    session.cost_usd += cost.global_spent
    session.tasks_run += 1
    ui.print_task_result(
        task_id=task_id,
        success=outcome.success,
        exit_code=outcome.final_exit_code,
        poc_id=outcome.poc_id or "—",
        cost=outcome.cost_usd,
        stages=outcome.stages_run,
    )
    return None


def cmd_subset(session, args):
    subset_path = DATA_DIR / "subset_tasks.txt"
    if not subset_path.exists():
        ui.print_err(f"no subset file at {subset_path}")
        return None
    limit = int(args[0]) if args and args[0].isdigit() else 0
    tasks = [ln.strip() for ln in subset_path.read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    if limit > 0:
        tasks = tasks[:limit]
    cost = CostTracker(session.settings.budget_total_usd, session.settings.per_task_soft_usd)
    run_id = session._next_run_id()
    ok = 0
    for tid in tasks:
        with ui.status(f"running {tid}…"):
            oc = asyncio.run(orchestrator.run_task(tid, session.backend, session.settings, cost, run_id))
        ok += int(oc.success)
        icon = "[ok]✓[/ok]" if oc.success else "[err]✗[/err]"
        ui.console.print(f"  {icon} [brand]{tid:28}[/brand] [muted]${oc.cost_usd:.3f}[/muted]")
    cost.write(RUNS_DIR / run_id / "cost.json")
    session.cost_usd += cost.global_spent
    session.tasks_run += len(tasks)
    ui.print_ok(f"{ok}/{len(tasks)} crashed   total ${cost.global_spent:.3f}")
    return None


COMMANDS = {
    "help": cmd_help,
    "?": cmd_help,
    "task": cmd_task,
    "subset": cmd_subset,
    "backend": cmd_backend,
    "model": cmd_model,
    "config": cmd_config,
    "cost": cmd_cost,
    "clear": cmd_clear,
    "exit": cmd_exit,
    "quit": cmd_exit,
}


def dispatch_slash(session, line: str):
    parts = line.lstrip("/").strip().split()
    if not parts:
        return None
    name, args = parts[0].lower(), parts[1:]
    fn = COMMANDS.get(name)
    if not fn:
        ui.print_err(f"unknown command: /{name}   (try [key]/help[/key])")
        return None
    return fn(session, args)
