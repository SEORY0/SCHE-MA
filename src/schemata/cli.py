"""schemata CLI: run-task / run-subset / report."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import typer

from . import orchestrator
from .config import DATA_DIR, RUNS_DIR, load_settings
from .cost_tracker import CostTracker

app = typer.Typer(add_completion=False, help="SCHE-MA — CyberGym multi-agent PoC generator.")


def _run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


@app.command("run-task")
def run_task_cmd(
    task_id: str = typer.Option(..., "--task-id", help="e.g. arvo:10400"),
    backend: str = typer.Option(None, "--backend", help="claude_code | claude_api"),
    config: str = typer.Option(None, "--config", help="path to schemata.toml"),
):
    settings = load_settings(config)
    backend = backend or settings.backend
    cost = CostTracker(settings.budget_total_usd, settings.per_task_soft_usd)
    run_id = _run_id()
    outcome = asyncio.run(orchestrator.run_task(task_id, backend, settings, cost, run_id))
    cost.write(RUNS_DIR / run_id / "cost.json")
    typer.echo(outcome.model_dump_json(indent=2))
    raise typer.Exit(code=0 if outcome.success else 1)


@app.command("run-subset")
def run_subset_cmd(
    backend: str = typer.Option(None, "--backend"),
    config: str = typer.Option(None, "--config"),
    subset: str = typer.Option(str(DATA_DIR / "subset_tasks.txt"), "--subset"),
    limit: int = typer.Option(0, "--limit", help="run only the first N subset tasks (0 = all); use --limit 1 for the M3-6 1-task smoke"),
):
    settings = load_settings(config)
    backend = backend or settings.backend
    cost = CostTracker(settings.budget_total_usd, settings.per_task_soft_usd)
    run_id = _run_id()

    tasks = [ln.strip() for ln in Path(subset).read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    if limit > 0:
        tasks = tasks[:limit]

    rows = []
    for tid in tasks:
        oc = asyncio.run(orchestrator.run_task(tid, backend, settings, cost, run_id))
        rows.append(oc)
        typer.echo(f"{tid:28} success={oc.success!s:5} exit={oc.final_exit_code} "
                   f"poc_id={oc.poc_id} cost=${oc.cost_usd:.3f} stages={','.join(oc.stages_run)}")

    cost.write(RUNS_DIR / run_id / "cost.json")
    n_ok = sum(1 for r in rows if r.success)
    summary = RUNS_DIR / run_id / "subset_summary.json"
    summary.write_text(
        __import__("json").dumps(
            {"passed": n_ok, "total": len(rows), "global_cost_usd": round(cost.global_spent, 4),
             "rows": [r.model_dump() for r in rows]}, indent=2, ensure_ascii=False))
    typer.echo(f"\n=== {n_ok}/{len(rows)} crashed (exit!=0)  global ${cost.global_spent:.3f}  -> {summary}")


if __name__ == "__main__":
    app()
