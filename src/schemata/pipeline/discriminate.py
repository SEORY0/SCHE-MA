"""Stage 4 — Discriminator: the independent referee (CrayFisher judgment-agent, adapted).

Decides whether the crash we achieved is the SPECIFIC bug in `description.txt` or a false
positive that would ALSO crash the fixed build (scoring 0 — the dominant CyberGym failure).

At level1 there is no patch/error ground truth and we can only observe the vulnerable
build (the green's `test_vulnerable`). So the referee reasons OFFLINE from description.txt +
the sanitizer output of the crashing submission + the source — never the web (CyberGym
no-cheating rule). A REJECT drives a bounded retarget loop in the brain/orchestrator.

This module is backend-agnostic: it builds a `discriminate` StageRequest via prompt_loader
and runs it on whatever backend is passed in. The retarget loop itself lives in the caller
(it knows how to rebuild a `generate` request for its environment).
"""
from __future__ import annotations

from pathlib import Path

from . import prompt_loader


def _disc_cfg(settings) -> dict:
    fn = getattr(settings, "stage_cfg", None)
    return fn("discriminate") if callable(fn) else {}


def discriminate_enabled(settings) -> bool:
    return bool(_disc_cfg(settings).get("enabled", True))


def max_retarget(settings) -> int:
    return int(_disc_cfg(settings).get("max_retarget", 1))


def parse_verdict(structured: dict | None) -> dict:
    """Normalize the discriminate stage's JSON into a small decision dict.

    Fail-safe: an unparseable / missing verdict is treated as ACCEPT (emit the crash we
    have — a crashing PoC can still score; a regenerate cannot make a no-verdict worse).
    """
    v = structured or {}
    verdict = str(v.get("verdict", "")).upper()
    decision = str(v.get("submit_decision", "")).upper()
    if not verdict and not decision:
        accept = True  # no usable judgment -> don't throw away a crash
    else:
        accept = verdict == "ACCEPT" or decision == "EMIT_AS_FINAL"
    return {
        "accept": accept,
        "verdict": verdict or "UNKNOWN",
        "failure_class": v.get("failure_class"),
        "retarget_instruction": v.get("retarget_instruction"),
        "confidence": v.get("confidence"),
    }


async def run_discriminator(
    backend, plan, meta, handle, prior, settings, backend_name, submissions,
    *, instrument_container=None,
):
    """Run one discriminate stage over the current submissions.

    Returns (verdict_dict, StageResult). The submissions (each carrying the sanitizer
    `output_excerpt`) are injected into the prior under `_submissions` so the referee sees
    every attempt's exit_code + crash trace via the prompt's {{prior_json}}.
    """
    disc_prior = dict(prior)
    # StageRequest.prior_results is typed dict[str, dict]; wrap the attempts list in a dict.
    disc_prior["_submissions"] = {"attempts": [s.model_dump() for s in submissions]}
    req = prompt_loader.build_request(
        "discriminate", plan, meta, handle, disc_prior, settings, backend_name,
        instrument_container=instrument_container,
    )
    res = await backend.run_stage(req)
    return parse_verdict(res.structured_output), res


def best_poc_bytes(handle, submissions) -> bytes | None:
    """Bytes of the most recent CRASHING submission's PoC (resolved under the task dir)."""
    for s in reversed(submissions):
        if getattr(s, "crashed", False) and s.poc_path:
            p = Path(s.poc_path)
            p = p if p.is_absolute() else (Path(handle.task_dir) / s.poc_path)
            try:
                if p.is_file():
                    return p.read_bytes()
            except OSError:
                continue
    return None
