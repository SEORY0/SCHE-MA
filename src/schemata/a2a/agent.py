"""The agent 'brain' invoked by the A2A executor.

M6-b: `run()` drives the real claude_api recon->generate pipeline on the green-supplied
task. In the arena the purple container has no target binary/docker, so the only crash
feedback is the green's `test_vulnerable` round-trip — the generate stage's submit_poc
tool is wired to it (via StageRequest.submit_fn = transport.submit), giving the agent a
real submit/repair loop. Returns the winning PoC bytes (falls back to a placeholder so the
A2A task still completes if generation yields nothing).

Level3 fast-path (M6-c): when the green sends patch.diff + error.txt (level3), the bug
location and sanitizer are ground truth. We extract them mechanically with
`extract_level3_recon` and skip the LLM recon call entirely, going straight to generate
with the parsed intel pre-loaded as `prior["recon"]`. Largest token-saving lever in the
arena (recon is ~25-40% of per-task spend even on Haiku).

`run_skeleton` / SKELETON_POC remain as the deterministic fallback.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from ..models import PipelinePlan, TaskMeta
from .level3_intel import extract_level3_recon

logger = logging.getLogger(__name__)

# Deterministic placeholder PoC (fallback only).
SKELETON_POC = bytes(range(8))


def _log(msg: str) -> None:
    """stderr breadcrumb — captured by amber-otelcol so we can post-mortem skeleton submissions."""
    print(f"[brain {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


async def run_skeleton(handle, files: dict[str, bytes]) -> bytes:
    """Return placeholder PoC bytes (M6-a fallback)."""
    return SKELETON_POC


def _a2a_plan(settings, difficulty: str = "medium", *, skip_recon: bool = False) -> PipelinePlan:
    """Arena route, cost-routed across models. No local instrument/MCP — the purple
    container has no target image; crash feedback is the green's.

    Default level1 path: 3-stage Haiku → Sonnet → (Sonnet|Opus by difficulty).
      - recon (haiku):    triage description.txt + repo source, narrow to suspects.
      - analyze (sonnet): build the byte-level PoC plan from recon's surface.
      - generate (sonnet|opus): craft + iterate via submit_poc against the green.

    Level3 fast-path: when patch.diff + error.txt were parsed mechanically, recon AND
    analyze are skipped (`skip_recon=True`) — the parsed intel IS the localized plan.
    Generate alone runs with whatever model `by_difficulty` selects.
    """
    stages = ["generate"] if skip_recon else ["recon", "analyze", "generate"]
    return PipelinePlan(
        difficulty=difficulty,
        stages=stages,
        stage_models={s: settings.model_for(s, difficulty) for s in stages},
        has_instrument=False, has_mcp_index=False, thinking=False, minimize_info=False,
    )


def _read_poc(handle, res) -> bytes | None:
    """Best PoC bytes from a generate StageResult: the crashing winner, else the last submission."""
    cand = res.artifacts.poc_path
    if not cand and res.artifacts.submissions:
        cand = res.artifacts.submissions[-1].poc_path
    if not cand:
        return None
    p = Path(cand)
    p = p if p.is_absolute() else (Path(handle.task_dir) / cand)
    return p.read_bytes() if p.is_file() else None


async def run(handle, files, settings, transport=None, emit=None) -> bytes:
    """Run [recon]->generate on the claude_api backend; return the winning PoC bytes.

    transport: the green submit transport (A2AGreenSubmit). When set, the generate stage's
    submit_poc tests/repairs against the green. emit: optional async status reporter.

    When `handle.level == "level3"` and patch.diff/error.txt are parseable, the recon LLM
    stage is skipped and replaced by mechanical extraction (`extract_level3_recon`).
    """
    from .. import discriminate as disc
    from .. import prompt_loader
    from ..backends.claude_api import ClaudeApiBackend

    # One-line task-start breadcrumb (no per-stage spam — at 49 tasks × 10 workers each
    # emitting recon/analyze/generate start+end, the cumulative log volume filled the
    # GitHub Actions runner disk on PR #202 shard-1 and killed the run mid-flight.)
    api_key_set = bool(getattr(settings, "anthropic_api_key", None))
    lvl = getattr(handle, "level", "?")
    label = getattr(handle, "label", "?")

    backend = ClaudeApiBackend(settings)
    meta = TaskMeta(task_id=handle.label, difficulty_estimate="medium")
    prior: dict[str, dict] = {}

    # ---- level3 fast-path: mechanical recon, skip the LLM recon call ------------
    level3_recon = None
    if lvl == "level3":
        level3_recon = extract_level3_recon(Path(handle.task_dir))
    if level3_recon is not None:
        prior["recon"] = level3_recon
        ct = level3_recon.get("crash_type") or "unknown"
        n_files = len(level3_recon.get("suspected_files", []))
        _log(f"{label} level=level3 fast-path: {ct} ({n_files} files); skipping LLM recon")
        if emit:
            await emit(f"level3 mechanical recon: {ct}, {n_files} suspected file(s); skipping LLM recon")
    else:
        _log(f"{label} level={lvl} api_key={api_key_set}")

    plan = _a2a_plan(settings, skip_recon=level3_recon is not None)
    poc: bytes | None = None
    gen_res = None

    for stage in plan.stages:
        req = prompt_loader.build_request(stage, plan, meta, handle, prior, settings, "claude_api")
        if stage == "generate" and transport is not None:
            req.submit_fn = transport.submit          # submit_poc -> green test_vulnerable
        if emit:
            await emit(f"stage {stage} ({req.model})…")
        res = await backend.run_stage(req)
        prior[stage] = res.structured_output
        if res.error or res.stop_reason == "error":
            _log(f"{label} stage {stage} ERROR: stop={res.stop_reason} err={res.error!r}")
        if stage == "generate":
            gen_res = res
            poc = _read_poc(handle, res)
            if emit:
                await emit(f"generate {res.stop_reason}: {len(res.artifacts.submissions)} test(s)")

    # ---- Stage 4: independent discriminator + bounded retarget -------------------
    # The generator self-judges its own crash (rubber-stamp risk) and the arena green only
    # tests the vul build — so an achieved crash may be a false positive that also crashes
    # the fix (scoring 0). An INDEPENDENT referee compares the crash to description.txt and,
    # on REJECT, drives one more generate round with a different theory.
    all_submissions = list(gen_res.artifacts.submissions) if gen_res else []
    if (gen_res is not None and transport is not None and all_submissions
            and disc.discriminate_enabled(settings)):
        # Hard invariant: this optional stage must NEVER downgrade the outcome. Any failure
        # falls through to `best_poc_bytes` below, which returns the best crash already found
        # (or keeps the pre-loop `poc`) — never worse than not having the referee at all.
        try:
            budget = disc.max_retarget(settings)
            for attempt in range(budget + 1):
                verdict, disc_res = await disc.run_discriminator(
                    backend, plan, meta, handle, prior, settings, "claude_api", all_submissions)
                prior["discriminate"] = disc_res.structured_output
                _log(f"{label} discriminate[{attempt}]: {verdict['verdict']} "
                     f"fc={verdict['failure_class']} accept={verdict['accept']}")
                if emit:
                    await emit(f"referee: {verdict['verdict']} ({verdict['failure_class'] or '-'})")
                if verdict["accept"] or attempt >= budget:
                    break
                if emit:
                    await emit("referee rejected → regenerating with a different theory…")
                req = prompt_loader.build_request("generate", plan, meta, handle, prior, settings, "claude_api")
                req.submit_fn = transport.submit
                gen_res = await backend.run_stage(req)
                prior["generate"] = gen_res.structured_output
                all_submissions.extend(gen_res.artifacts.submissions)
        except Exception as e:
            _log(f"{label} discriminate ERROR (kept best crash so far): {e!r}")
        poc = disc.best_poc_bytes(handle, all_submissions) or poc

    # ---- P0+P4 no-PoC retry safety net ------------------------------------------
    # If generate produced no PoC at all (no submit_poc call, or every call returned
    # without a crash), one Sonnet retry on the same prompt rarely helps — capability
    # ceiling. Promote to Opus + force analyze if level3 fast-path skipped it. Mirrors
    # orchestrator._retry_if_no_poc; one shot only.
    if poc is None and transport is not None and gen_res is not None:
        try:
            _log(f"{label} no-PoC retry: forcing analyze+opus (had {n_sub if (n_sub := len(all_submissions)) else 0} submits)")
            if emit:
                await emit("no PoC produced → retry with analyze + opus")
            plan.minimize_info = False
            plan.stage_models["generate"] = "opus"
            retry_stages = []
            if "analyze" not in plan.stages:
                plan.stage_models["analyze"] = settings.model_for("analyze", plan.difficulty)
                retry_stages.append("analyze")
            retry_stages.append("generate")
            for stage in retry_stages:
                req = prompt_loader.build_request(stage, plan, meta, handle, prior, settings, "claude_api")
                if stage == "generate":
                    req.submit_fn = transport.submit
                res = await backend.run_stage(req)
                prior[stage] = res.structured_output
                if stage == "generate":
                    gen_res = res
                    all_submissions.extend(res.artifacts.submissions)
                    poc = _read_poc(handle, res) or poc
                if res.error or res.stop_reason == "error":
                    _log(f"{label} retry stage {stage} ERROR: stop={res.stop_reason} err={res.error!r}")
        except Exception as e:
            _log(f"{label} no-PoC retry ERROR (kept original outcome): {e!r}")

    # One-line task-end breadcrumb. This is the line we need for post-mortem: did
    # generate succeed? how many submit_poc round-trips? did we return a real PoC?
    n_sub = len(all_submissions)
    stop = gen_res.stop_reason if gen_res else "no-generate"
    _log(f"{label} done: stop={stop} subs={n_sub} poc={len(poc) if poc else 0}B"
         f"{' SKELETON_FALLBACK' if poc is None else ''}")
    return poc or SKELETON_POC
