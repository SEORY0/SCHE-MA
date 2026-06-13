"""Single-task end-to-end driver: route -> stages -> submit -> confirm -> record."""
from __future__ import annotations

import json
from pathlib import Path

from . import router
from .backends import make_backend
from .config import RUNS_DIR, Settings
from .cost_tracker import CostTracker
from .cybergym import ids
from .cybergym.submit import SubmitClient
from .cybergym.task_gen import gen_task
from .instrument import Instrumenter
from .models import SubmissionRecord, TaskOutcome
from .prompt_loader import build_request
from .util import truncate


def _safe(task_id: str) -> str:
    return task_id.replace(":", "_").replace("/", "_")


def _recon_localized(recon_output: dict | None) -> bool:
    """Did cheap (Haiku) recon actually narrow the bug LOCATION? Drives bounded escalation.

    Localized = it named a suspect file/function OR pinned the harness entry point. Note:
    `vuln_classes` alone does NOT count — that is description.txt classification (no code
    reading), which the JSON-flush fallback recovers even when recon never localized. The
    escalation we gate here is specifically about *localization*, which needs a stronger model.
    """
    so = recon_output or {}
    if so.get("suspected_files") or so.get("suspected_functions") or so.get("entry_point"):
        return True
    harness = so.get("harness")
    return bool(isinstance(harness, dict) and harness.get("entry_point"))


async def run_task(
    task_id: str,
    backend_name: str,
    settings: Settings,
    cost: CostTracker,
    run_id: str,
) -> TaskOutcome:
    meta = ids.lookup(task_id)
    plan = router.plan(meta, settings)

    run_dir = RUNS_DIR / run_id / _safe(task_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    task_dir = run_dir / "task"

    outcome = TaskOutcome(task_id=task_id, backend=backend_name, success=False, run_dir=str(run_dir))

    try:
        handle = gen_task(settings, task_id, task_dir)
    except Exception as e:
        outcome.error = f"gen_task: {e}"
        _write_record(run_dir, outcome, plan, {}, [])
        return outcome

    backend = make_backend(backend_name, settings)
    instrumenter = Instrumenter(timeout_s=int(settings.instrument.get("timeout_s", 600)))
    container = None
    if plan.has_instrument and settings.instrument.get("enabled", True):
        container = instrumenter.start(task_id, run_id)

    prior: dict[str, dict] = {}
    submissions: list[SubmissionRecord] = []
    winning_poc: str | None = None

    try:
        # `stages` is a working copy so we can PROMOTE analyze mid-run (bounded escalation):
        # recon is cheap, fast triage on Haiku, not the definitive localizer. When it comes
        # up empty (the "easy" route skips analyze entirely), the right move is one capable
        # localization stage — NOT looping more Haiku turns (capability ceiling, and "found
        # it" isn't verifiable until a crash). We escalate at most once, before generate.
        stages = list(plan.stages)
        i = 0
        while i < len(stages):
            stage = stages[i]
            req = build_request(
                stage, plan, meta, handle, prior, settings, backend_name,
                instrument_container=(container.name if container else None),
            )
            res = await backend.run_stage(req)
            cost.add(task_id, stage, res.usage, res.cost_usd)
            outcome.stages_run.append(stage)
            prior[stage] = res.structured_output
            submissions.extend(res.artifacts.submissions)
            if res.artifacts.poc_path:
                winning_poc = res.artifacts.poc_path

            (run_dir / f"stage_{stage}.json").write_text(json.dumps({
                "structured_output": res.structured_output,
                "stop_reason": res.stop_reason,
                "error": res.error,
                "cost_usd": res.cost_usd,
                "usage": res.usage.model_dump(),
                "transcript_tail": res.raw_transcript_tail,
            }, indent=2, ensure_ascii=False))

            # Bounded escalation: cheap recon failed to localize and the plan has no analyze
            # stage -> insert analyze (stronger Sonnet localizer with full tools) before
            # generate, and lift the lean-context flag so it gets the knowledge base. Fires
            # once (guarded by "analyze" not in stages). generate then builds on a real plan
            # instead of re-localizing from scratch on its own.
            if (stage == "recon" and "analyze" not in stages
                    and not _recon_localized(res.structured_output)):
                insert_at = stages.index("generate") if "generate" in stages else len(stages)
                stages.insert(insert_at, "analyze")
                plan.minimize_info = False
                plan.stage_models["analyze"] = settings.model_for("analyze", plan.difficulty)
                outcome.escalated = True
                (run_dir / "escalation.json").write_text(json.dumps({
                    "reason": "recon did not localize (no suspected file/function/harness entry)",
                    "recon_stop_reason": res.stop_reason,
                    "promoted_stage": "analyze",
                    "analyze_model": plan.stage_models["analyze"],
                }, indent=2, ensure_ascii=False))

            if res.stop_reason == "error":
                outcome.error = res.error
            if cost.over_task_soft_cap(task_id) or cost.over_global_budget():
                outcome.error = (outcome.error or "") + " [budget cap hit]"
                break
            i += 1
    finally:
        instrumenter.cleanup(container)

    # Independent confirmation: re-submit the winning PoC via SubmitClient.
    final = _confirm_winner(handle, prior, settings, run_dir, submissions, winning_poc)
    if final is not None:
        outcome.final_exit_code = final.exit_code
        outcome.poc_id = final.poc_id
        outcome.success = final.crashed

    outcome.cost_usd = cost.task_cost(task_id)
    _write_record(run_dir, outcome, plan, prior, submissions)
    return outcome


def _resolve_winning_poc(handle, prior, submissions, winning_poc):
    """Locate the PoC to re-confirm, most-reliable source first."""
    def _under_task(rel: str):
        p = Path(rel)
        p = p if p.is_absolute() else (handle.task_dir / rel)
        return p if p.exists() else None

    # 1) backend's recorded winning PoC (artifacts.poc_path), then the stage JSON
    for cand in (winning_poc, prior.get("generate", {}).get("winning_poc_path")):
        if cand and (p := _under_task(cand)):
            return p
    # 2) most-recent crashing submission — covers early-stop before the model wrote its
    #    closing JSON, and PoCs named anything other than 'poc' (e.g. poc_mng.mng)
    for s in reversed(submissions):
        if getattr(s, "crashed", False) and (p := _under_task(s.poc_path)):
            return p
    # 3) a file literally named 'poc' in the task dir
    cand = handle.task_dir / "poc"
    return cand if cand.exists() else None


def _confirm_winner(handle, prior, settings, run_dir: Path, submissions: list, winning_poc=None):
    poc_path = _resolve_winning_poc(handle, prior, submissions, winning_poc)
    if poc_path is None:
        return None

    client = SubmitClient(
        server_url=settings.server_url,
        masked_id=handle.masked_id,
        agent_id=handle.agent_id,
        checksum=handle.checksum,
        require_flag=settings.require_flag,
        rate_limit_max=settings.rate_limit_max,
        rate_limit_window_s=settings.rate_limit_window_s,
    )
    try:
        verdict = client.submit(poc_path)
    except Exception as e:
        (run_dir / "confirm_error.txt").write_text(str(e))
        return None

    submissions.append(SubmissionRecord(
        poc_path=str(poc_path),
        poc_sha256=SubmitClient.sha256(poc_path),
        exit_code=verdict.exit_code,
        output_excerpt=truncate(verdict.output, 1500, 500),
        poc_id=verdict.poc_id,
    ))
    return verdict


def _write_record(run_dir: Path, outcome: TaskOutcome, plan, prior, submissions) -> None:
    (run_dir / "outcome.json").write_text(json.dumps({
        "outcome": outcome.model_dump(),
        "plan": plan.model_dump() if hasattr(plan, "model_dump") else {},
    }, indent=2, ensure_ascii=False))
    with open(run_dir / "submissions.jsonl", "w") as f:
        for s in submissions:
            f.write(json.dumps(s.model_dump(), ensure_ascii=False) + "\n")
