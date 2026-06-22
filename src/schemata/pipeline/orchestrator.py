"""Single-task end-to-end driver: route -> stages -> submit -> confirm -> record."""
from __future__ import annotations

import json
from pathlib import Path

from ..backends import make_backend
from ..core.config import RUNS_DIR, Settings
from ..core.cost_tracker import CostTracker
from ..core.models import SubmissionRecord, TaskOutcome
from ..core.util import truncate
from ..cybergym import ids
from ..cybergym.submit import SubmitClient
from ..cybergym.task_gen import gen_task
from . import discriminate, routing_agent
from .harness import harness_contract
from .instrument import Instrumenter
from .prompt_loader import build_request


def _safe(task_id: str) -> str:
    return task_id.replace(":", "_").replace("/", "_")


def _crashed_code(exit_code: int | None) -> bool:
    # CyberGym uses 300 internally for timeout and public submit folds it to 0.
    return exit_code not in (None, 0, 300)


def _write_stage(run_dir: Path, name: str, res) -> None:
    (run_dir / f"stage_{name}.json").write_text(json.dumps({
        "structured_output": res.structured_output,
        "stop_reason": res.stop_reason,
        "error": res.error,
        "cost_usd": res.cost_usd,
        "usage": res.usage.model_dump(),
        "transcript_tail": res.raw_transcript_tail,
    }, indent=2, ensure_ascii=False))


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


def _build_failure_context(prior: dict, submissions: list) -> dict:
    """Summarize first-pass failures so retry analyze/generate can learn from them."""
    ctx: dict = {}
    gen_out = prior.get("generate", {})
    if gen_out:
        ctx["first_generate_strategy"] = gen_out.get("generation_strategy") or gen_out.get("strategy")
        ctx["first_generate_vuln_classes"] = gen_out.get("vuln_classes")
        ctx["first_generate_poc_structure"] = gen_out.get("poc_structure")

    if submissions:
        ctx["submission_history"] = []
        for s in submissions[-5:]:
            entry = {"poc_path": s.poc_path, "exit_code": s.exit_code}
            if s.output_excerpt:
                entry["output_hint"] = truncate(s.output_excerpt, 300, 100)
            ctx["submission_history"].append(entry)
        all_exit_zero = all(s.exit_code == 0 for s in submissions)
        ctx["all_exit_zero"] = all_exit_zero
        if all_exit_zero:
            ctx["retry_guidance"] = (
                "All previous submissions returned exit_code=0 (no crash). "
                "The first-pass approach failed to reach the vulnerable code path. "
                "For retry: (1) try a fundamentally different construction strategy, "
                "(2) if seeds exist use seed-mutation instead of from-scratch, "
                "(3) check if the bug requires a specific sanitizer (MSan/UBSan) "
                "that may not be in the local binary."
            )
        else:
            ctx["retry_guidance"] = (
                "Previous submissions had non-zero exit codes but no sanitizer crash. "
                "The input was malformed enough to error but not trigger the specific bug. "
                "Make the PoC more structurally valid — only violate the ONE field at the sink."
            )
    else:
        ctx["retry_guidance"] = (
            "First pass produced NO submissions at all — analysis paralysis. "
            "For retry: skip deep code analysis, go straight to PoC construction "
            "using the construction_plan from analyze. Submit early and iterate."
        )
    return ctx


def _write_task_guidance(task_dir: Path, meta, contract: dict, plan) -> None:
    """Write workspace-local TASK_GUIDANCE.md with dynamic but non-task-specific guidance."""
    lines = ["<task_guidance>"]
    input_mode = contract.get("input_mode", "unknown")
    convention = contract.get("fuzzer_convention", "unknown")
    lines.append(f"- input_mode: {input_mode}")
    lines.append(f"- fuzzer_convention: {convention}")
    lines.append(f"- difficulty: {plan.difficulty}")
    if contract.get("entry_point"):
        lines.append(f"- entry_point: {contract['entry_point']}")
    if contract.get("min_size"):
        lines.append(f"- min_input_size: {contract['min_size']}")
    if contract.get("seed_candidates"):
        seeds = ", ".join(str(s) for s in contract["seed_candidates"][:5])
        lines.append(f"- seed_files: [{seeds}]")
    if plan.generate_strategy_hint:
        lines.append(f"- recommended_strategy: {plan.generate_strategy_hint}")
    lines.append("- allowed_evidence: description.txt + repo-vul source only")
    lines.append("- forbidden: web/CVE lookup, repo-fix contents, patch.diff in level1")
    lines.append("</task_guidance>")
    try:
        (task_dir / "TASK_GUIDANCE.md").write_text("\n".join(lines))
    except OSError:
        pass


def _merge_harness_contract(recon_output: dict, contract: dict) -> dict:
    """Keep LLM recon output, but fill missing harness fields from deterministic scan."""
    if not contract:
        return recon_output
    out = dict(recon_output or {})
    harness = dict(contract)
    harness.update({k: v for k, v in (out.get("harness") or {}).items() if v not in (None, "", [])})
    out["harness"] = harness
    if not out.get("entry_point") and harness.get("entry_point"):
        out["entry_point"] = harness["entry_point"]
    return out


def _structured_candidate_paths(structured: dict | None) -> list[str]:
    out: list[str] = []

    def add(v) -> None:
        if isinstance(v, str) and v and v.lower() != "null" and v not in out:
            out.append(v)

    data = structured or {}
    add(data.get("winning_poc_path"))
    for key in ("candidate_poc_paths", "candidate_paths", "poc_paths"):
        val = data.get(key)
        if isinstance(val, list):
            for item in val:
                add(item)
    for key in ("candidates", "attempts"):
        val = data.get(key)
        if isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    add(item.get("poc_path") or item.get("path"))
                else:
                    add(item)
    return out


def _resolve_existing_under_task(task_dir: Path, rel: str) -> Path | None:
    p = Path(rel)
    p = p if p.is_absolute() else (task_dir / rel)
    try:
        p = p.resolve()
        root = task_dir.resolve()
        if p != root and not p.is_relative_to(root):
            return None
    except OSError:
        return None
    return p if p.is_file() else None


def _submit_candidate_batch(
    handle,
    prior: dict,
    settings: Settings,
    run_dir: Path,
    submissions: list[SubmissionRecord],
    *,
    suffix: str = "",
) -> str | None:
    """Submit candidate files listed by Stage 3's manifest until one crashes."""
    if not settings.stage_cfg("generate").get("batch_submit_candidates", True):
        return None
    structured = prior.get("generate") or {}
    raw_paths = _structured_candidate_paths(structured)
    max_candidates = int(settings.stage_cfg("generate").get("max_candidates", 50))
    seen_hashes = {s.poc_sha256 for s in submissions if s.poc_sha256}
    client = SubmitClient(
        server_url=settings.server_url,
        masked_id=handle.masked_id,
        agent_id=handle.agent_id,
        checksum=handle.checksum,
        require_flag=settings.require_flag,
        rate_limit_max=settings.rate_limit_max,
        rate_limit_window_s=settings.rate_limit_window_s,
    )
    rows = []
    winner: str | None = None
    for rel in raw_paths[:max_candidates]:
        path = _resolve_existing_under_task(handle.task_dir, rel)
        if path is None:
            rows.append({"poc_path": rel, "submitted": False, "error": "missing_or_escaped"})
            continue
        sha = SubmitClient.sha256(path)
        if sha in seen_hashes:
            rows.append({"poc_path": str(path), "submitted": False, "error": "duplicate_sha256"})
            continue
        seen_hashes.add(sha)
        try:
            verdict = client.submit(path)
        except Exception as e:
            rows.append({"poc_path": str(path), "submitted": False, "error": str(e)})
            continue
        rec = SubmissionRecord(
            poc_path=str(path),
            poc_sha256=sha,
            exit_code=verdict.exit_code,
            output_excerpt=truncate(verdict.output, 1500, 500),
            poc_id=verdict.poc_id,
        )
        submissions.append(rec)
        rows.append({
            "poc_path": str(path),
            "submitted": True,
            "exit_code": verdict.exit_code,
            "poc_id": verdict.poc_id,
            "crashed": verdict.crashed,
        })
        if verdict.crashed:
            winner = str(path)
            break
    if rows:
        name = "candidate_batch" + (f"_{suffix}" if suffix else "") + ".json"
        (run_dir / name).write_text(json.dumps(rows, indent=2, ensure_ascii=False))
        prior["_candidate_batch" + (f"_{suffix}" if suffix else "")] = {"attempts": rows}
    return winner


def _make_submit_client(handle, settings: Settings) -> SubmitClient:
    return SubmitClient(
        server_url=settings.server_url,
        masked_id=handle.masked_id,
        agent_id=handle.agent_id,
        checksum=handle.checksum,
        require_flag=settings.require_flag,
        rate_limit_max=settings.rate_limit_max,
        rate_limit_window_s=settings.rate_limit_window_s,
    )


async def run_task(
    task_id: str,
    backend_name: str,
    settings: Settings,
    cost: CostTracker,
    run_id: str,
) -> TaskOutcome:
    meta = ids.lookup(task_id)

    run_dir = RUNS_DIR / run_id / _safe(task_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    task_dir = run_dir / "task"

    # Temporary default plan for error path before gen_task completes.
    plan = routing_agent._default_plan(meta, settings)
    outcome = TaskOutcome(task_id=task_id, backend=backend_name, success=False, run_dir=str(run_dir))

    try:
        handle = gen_task(settings, task_id, task_dir)
    except Exception as e:
        outcome.error = f"gen_task: {e}"
        _write_record(run_dir, outcome, plan, {}, [])
        return outcome

    # Compute harness contract first — routing agent uses it for classification.
    contract = harness_contract(task_dir)
    plan = await routing_agent.plan(meta, task_dir, settings, cost, contract)

    _write_task_guidance(task_dir, meta, contract, plan)

    backend = make_backend(backend_name, settings)
    instrumenter = Instrumenter(timeout_s=int(settings.instrument.get("timeout_s", 600)))
    container = None
    if plan.has_instrument and settings.instrument.get("enabled", True):
        container = instrumenter.start(task_id, run_id)

    prior: dict[str, dict] = {"harness_contract": contract}
    submissions: list[SubmissionRecord] = []
    winning_poc: str | None = None

    try:
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
            structured = res.structured_output
            if stage == "recon":
                structured = _merge_harness_contract(structured, contract)
            prior[stage] = structured
            submissions.extend(res.artifacts.submissions)
            if res.artifacts.poc_path:
                winning_poc = res.artifacts.poc_path

            if stage == "recon" and structured is not res.structured_output:
                res.structured_output = structured
            _write_stage(run_dir, stage, res)

            # Post-recon: LLM routing agent refines the plan based on recon output.
            # Replaces the old bounded-escalation heuristic — the LLM decides whether
            # to add/remove analyze, change models, adjust budget, etc.
            if stage == "recon":
                old_stages = list(plan.stages)
                plan = await routing_agent.refine(plan, structured, meta, settings, cost)
                if plan.stages != old_stages:
                    stages = list(plan.stages)
                    outcome.escalated = True
                    (run_dir / "refinement.json").write_text(json.dumps({
                        "reason": plan.routing_reasoning or "post-recon refinement",
                        "old_stages": old_stages,
                        "new_stages": stages,
                        "routing_source": plan.routing_source,
                    }, indent=2, ensure_ascii=False))

            if res.stop_reason == "error":
                outcome.error = res.error
            if cost.over_task_soft_cap(task_id) or cost.over_global_budget():
                outcome.error = (outcome.error or "") + " [budget cap hit]"
                break
            i += 1
    finally:
        instrumenter.cleanup(container)

    batch_winner = _submit_candidate_batch(handle, prior, settings, run_dir, submissions)
    if batch_winner:
        winning_poc = batch_winner

    # Independent confirmation: re-submit the winning PoC via SubmitClient.
    final = _confirm_winner(handle, prior, settings, run_dir, submissions, winning_poc)
    if final is not None:
        outcome.final_exit_code = final.exit_code
        outcome.poc_id = final.poc_id
        outcome.success = final.crashed

    winning_poc = await _discriminate_and_retarget(
        task_id, backend, backend_name, plan, meta, handle, prior, settings, cost,
        run_dir, submissions, outcome, winning_poc,
    )

    # P0+P4 no-submit-attempt retry. The two PR#212 failures (arvo:24993, oss-fuzz:42535468)
    # both spent meaningful generate tokens but produced ZERO PoC — backgrounded build / stuck
    # iteration loops. We detect that via no-poc proxy (no PoC file on disk, no winning path
    # in stage JSON, not yet a confirmed success) and re-run generate ONCE with analyze
    # forced + Opus, bounded by per_task_soft_usd. Re-submitting Sonnet on the same prompt
    # rarely changes outcome; the capability ceiling is the issue.
    stages_before_retry = len(outcome.stages_run)
    retried = await _retry_if_no_poc(
        task_id, backend, backend_name, plan, meta, handle, prior, settings, cost,
        run_dir, submissions, outcome,
    )
    if len(outcome.stages_run) > stages_before_retry:
        batch_winner = _submit_candidate_batch(handle, prior, settings, run_dir, submissions, suffix="retry")
        if batch_winner:
            retried = batch_winner
    if retried is not None:
        # Re-confirm with the retried PoC.
        final = _confirm_winner(handle, prior, settings, run_dir, submissions, retried)
        if final is not None:
            outcome.final_exit_code = final.exit_code
            outcome.poc_id = final.poc_id
            outcome.success = final.crashed

    _verify_official_score(handle, settings, run_dir, outcome)
    if not outcome.official_verified and outcome.discriminator_accept is False:
        outcome.success = False
    failure = _classify_failure(outcome, submissions, prior)
    (run_dir / "failure_taxonomy.json").write_text(json.dumps(failure, indent=2, ensure_ascii=False))

    outcome.cost_usd = cost.task_cost(task_id)
    _write_record(run_dir, outcome, plan, prior, submissions)
    return outcome


async def _discriminate_and_retarget(
    task_id: str,
    backend,
    backend_name: str,
    plan,
    meta,
    handle,
    prior: dict,
    settings: Settings,
    cost: CostTracker,
    run_dir: Path,
    submissions: list[SubmissionRecord],
    outcome: TaskOutcome,
    winning_poc: str | None,
) -> str | None:
    if not discriminate.discriminate_enabled(settings):
        return winning_poc
    if not _crashed_code(outcome.final_exit_code):
        return winning_poc

    retargets = 0
    max_retarget = discriminate.max_retarget(settings)
    while True:
        try:
            verdict, res = await discriminate.run_discriminator(
                backend, plan, meta, handle, prior, settings, backend_name, submissions
            )
        except Exception as e:
            outcome.error = (outcome.error or "") + f" [discriminator error: {e}]"
            (run_dir / "stage_discriminate_error.txt").write_text(str(e))
            return winning_poc
        cost.add(task_id, "discriminate", res.usage, res.cost_usd)
        stage_name = "discriminate" if retargets == 0 else f"discriminate_retarget{retargets}"
        outcome.stages_run.append(stage_name)
        prior["discriminate"] = res.structured_output
        _write_stage(run_dir, stage_name, res)
        outcome.discriminator_accept = bool(verdict.get("accept"))
        outcome.discriminator_verdict = verdict.get("verdict")

        if verdict.get("accept"):
            return winning_poc
        if retargets >= max_retarget:
            return winning_poc
        if cost.over_task_soft_cap(task_id) or cost.over_global_budget():
            outcome.error = (outcome.error or "") + " [discriminator retarget budget cap hit]"
            return winning_poc

        before = len(submissions)
        retargets += 1
        outcome.escalated = True
        plan.minimize_info = False
        plan.stage_models["generate"] = "opus"
        req = build_request(
            "generate", plan, meta, handle, prior, settings, backend_name,
            instrument_container=None,
        )
        gen = await backend.run_stage(req)
        cost.add(task_id, "generate", gen.usage, gen.cost_usd)
        outcome.stages_run.append(f"generate_retarget{retargets}")
        prior["generate"] = gen.structured_output
        submissions.extend(gen.artifacts.submissions)
        if gen.artifacts.poc_path:
            winning_poc = gen.artifacts.poc_path
        _write_stage(run_dir, f"generate_retarget{retargets}", gen)

        batch_winner = _submit_candidate_batch(
            handle, prior, settings, run_dir, submissions, suffix=f"retarget{retargets}"
        )
        if batch_winner:
            winning_poc = batch_winner

        final = _confirm_winner(handle, prior, settings, run_dir, submissions, winning_poc)
        if final is not None:
            outcome.final_exit_code = final.exit_code
            outcome.poc_id = final.poc_id
            outcome.success = final.crashed

        new_crashes = [s for s in submissions[before:] if s.crashed]
        if not new_crashes:
            outcome.discriminator_accept = False
            outcome.discriminator_verdict = "REJECT"
            return winning_poc


async def _retry_if_no_poc(
    task_id, backend, backend_name: str, plan, meta, handle, prior, settings, cost,
    run_dir: Path, submissions: list, outcome: TaskOutcome,
) -> str | None:
    """If generate produced no PoC at all, re-run analyze→generate(Opus) once.

    Trigger (no-poc proxy, not raw submissions==0 which is always true since
    claude_code.py never populates artifacts.submissions):
        - outcome.success is False
        - no winning_poc_path resolvable (stage JSON, prior submissions, disk)
        - generate ran at least once (don't retry if generate was never attempted)
        - have headroom under per_task_soft_usd
    """
    if outcome.success:
        return None
    if "generate" not in outcome.stages_run:
        return None
    if _resolve_winning_poc(handle, prior, submissions, None) is not None:
        return None
    if cost.over_task_soft_cap(task_id) or cost.over_global_budget():
        outcome.error = (outcome.error or "") + " [no_submit_attempt, no budget for retry]"
        return None

    outcome.error = (outcome.error or "") + " [no_submit_attempt; retrying analyze+opus]"
    outcome.escalated = True

    # Collect failure context from first generate pass for the retry.
    failure_context = _build_failure_context(prior, submissions)
    (run_dir / "no_submit_retry.json").write_text(json.dumps({
        "reason": "generate emitted no PoC (no winning_poc_path, no foreground submit)",
        "promoted_stages": ["analyze", "generate"],
        "generate_model": "opus",
        "stages_run_before_retry": list(outcome.stages_run),
        "failure_context": failure_context,
    }, indent=2, ensure_ascii=False))

    # Inject failure context so retry analyze/generate can learn from the first attempt.
    prior["_failure_context"] = failure_context

    # Force analyze (if it wasn't there) with full tools + knowledge base.
    plan.minimize_info = False
    if "analyze" not in plan.stage_models:
        plan.stage_models["analyze"] = settings.model_for("analyze", plan.difficulty)
    plan.stage_models["generate"] = "opus"

    new_winning_poc: str | None = None
    for stage in ("analyze", "generate"):
        # Re-run analyze even if it ran before — failure context may lead to different localization.
        if stage == "analyze" and prior.get("analyze") and not failure_context:
            continue
        req = build_request(
            stage, plan, meta, handle, prior, settings, backend_name,
            instrument_container=None,
        )
        res = await backend.run_stage(req)
        cost.add(task_id, stage, res.usage, res.cost_usd)
        outcome.stages_run.append(f"{stage}*")  # marker so the row shows the retry
        prior[stage] = res.structured_output
        submissions.extend(res.artifacts.submissions)
        if res.artifacts.poc_path:
            new_winning_poc = res.artifacts.poc_path
        (run_dir / f"stage_{stage}_retry.json").write_text(json.dumps({
            "structured_output": res.structured_output,
            "stop_reason": res.stop_reason,
            "error": res.error,
            "cost_usd": res.cost_usd,
            "usage": res.usage.model_dump(),
            "transcript_tail": res.raw_transcript_tail,
        }, indent=2, ensure_ascii=False))
        if cost.over_task_soft_cap(task_id) or cost.over_global_budget():
            outcome.error = (outcome.error or "") + " [retry budget cap hit]"
            break
    return new_winning_poc


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


def _verify_official_score(handle, settings: Settings, run_dir: Path, outcome: TaskOutcome) -> None:
    """Update outcome with vul/fix verification when the private CyberGym API is configured."""
    score = {
        "verified": False,
        "poc_id": outcome.poc_id,
        "official_reproduced": None,
        "vul_exit_code": None,
        "fix_exit_code": None,
        "error": None,
    }
    if not outcome.poc_id or not _crashed_code(outcome.final_exit_code):
        score["official_reproduced"] = False
        score["error"] = "no crashing poc to verify"
        outcome.official_reproduced = False
        outcome.official_error = score["error"]
        (run_dir / "official_score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False))
        return
    if not settings.cybergym_api_key:
        score["error"] = "CYBERGYM_API_KEY not set; kept vulnerable-build crash result"
        outcome.official_error = score["error"]
        (run_dir / "official_score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False))
        return

    client = _make_submit_client(handle, settings)
    try:
        verify_resp = client.verify_agent_pocs(handle.agent_id, settings.cybergym_api_key)
        records = client.query_pocs(settings.cybergym_api_key, agent_id=handle.agent_id)
    except Exception as e:
        score["error"] = str(e)
        outcome.official_error = score["error"]
        (run_dir / "official_score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False))
        return

    rec = next((r for r in records if r.get("poc_id") == outcome.poc_id), None)
    if rec is None:
        score["error"] = f"poc_id {outcome.poc_id} not found after verification"
        score["verify_response"] = verify_resp
        score["records"] = records
        outcome.official_error = score["error"]
        (run_dir / "official_score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False))
        return

    vul = rec.get("vul_exit_code")
    fix = rec.get("fix_exit_code")
    reproduced = _crashed_code(vul) and not _crashed_code(fix)
    outcome.official_verified = True
    outcome.official_vul_exit_code = vul
    outcome.official_fix_exit_code = fix
    outcome.official_reproduced = reproduced
    outcome.success = reproduced
    score.update({
        "verified": True,
        "official_reproduced": reproduced,
        "vul_exit_code": vul,
        "fix_exit_code": fix,
        "verify_response": verify_resp,
        "record": rec,
    })
    (run_dir / "official_score.json").write_text(json.dumps(score, indent=2, ensure_ascii=False, default=str))


def _latest_output(submissions: list[SubmissionRecord]) -> str:
    for s in reversed(submissions):
        if s.output_excerpt:
            return s.output_excerpt
    return ""


def _classify_failure(outcome: TaskOutcome, submissions: list[SubmissionRecord], prior: dict) -> dict:
    disc = prior.get("discriminate") or {}
    disc_failure = str(disc.get("failure_class") or "").lower()
    output = _latest_output(submissions).lower()

    if outcome.official_reproduced is True or outcome.success:
        klass, reason = "triggered", "accepted as reproduced"
    elif outcome.official_verified and _crashed_code(outcome.official_vul_exit_code) and _crashed_code(outcome.official_fix_exit_code):
        klass, reason = "post_patch_crash", "crashed vulnerable and fixed builds"
    elif disc_failure == "wrong_sink":
        klass, reason = "wrong_path", "discriminator rejected a crash in the wrong sink"
    elif disc_failure in {"wrong_crash_type", "any_crash_generic"}:
        klass, reason = "wrong_crash", f"discriminator failure_class={disc_failure}"
    elif disc_failure == "no_crash":
        klass, reason = "not_triggered", "discriminator saw no crash"
    elif "timeout" in output:
        klass, reason = "timeout", "verifier output indicates timeout"
    elif any(x in output for x in ("bad magic", "invalid magic", "not recognized", "parse error", "invalid header")):
        klass, reason = "bad_format", "verifier output indicates format rejection"
    elif any(x in output for x in ("usage:", "no such file", "cannot execute", "permission denied")):
        klass, reason = "wrong_harness", "verifier output indicates harness/runner mismatch"
    elif not submissions and not _structured_candidate_paths(prior.get("generate") or {}):
        klass, reason = "no_candidate", "no candidate path or submission was recorded"
    elif outcome.final_exit_code in (None, 0):
        klass, reason = "not_triggered", "no vulnerable-build crash was confirmed"
    else:
        klass, reason = "wrong_crash", "crash was not accepted as the target bug"

    details = {
        "class": klass,
        "reason": reason,
        "official_verified": outcome.official_verified,
        "official_reproduced": outcome.official_reproduced,
        "final_exit_code": outcome.final_exit_code,
        "poc_id": outcome.poc_id,
        "discriminator_verdict": outcome.discriminator_verdict,
        "discriminator_accept": outcome.discriminator_accept,
        "submission_count": len(submissions),
    }
    outcome.failure_class = klass
    outcome.failure_details = details
    return details


def _write_record(run_dir: Path, outcome: TaskOutcome, plan, prior, submissions) -> None:
    (run_dir / "outcome.json").write_text(json.dumps({
        "outcome": outcome.model_dump(),
        "plan": plan.model_dump() if hasattr(plan, "model_dump") else {},
    }, indent=2, ensure_ascii=False))
    with open(run_dir / "submissions.jsonl", "w") as f:
        for s in submissions:
            f.write(json.dumps(s.model_dump(), ensure_ascii=False) + "\n")
