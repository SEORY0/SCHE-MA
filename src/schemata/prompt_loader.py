"""Render stage system prompts and assemble StageRequest objects."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .config import PROMPTS_DIR, Settings
from .cybergym.task_gen import TaskHandle
from .models import (PipelinePlan, StageRequest, TaskMeta, ThinkingConfig)

_STAGE_PROMPT = {
    "recon": "stage1_recon.md",
    "analyze": "stage2_analyze.md",
    "generate": "stage3_generate.md",
    "discriminate": "stage4_discriminate.md",
}

_KICKOFF = {
    "recon": "Do the Recon stage now. Follow your instructions and end with the JSON block.",
    "analyze": "Do the Analyze & Reason stage now. Follow your instructions and end with the JSON block.",
    "discriminate": ("Judge whether the crash we achieved is the SPECIFIC bug in description.txt "
                     "or a false positive that would also crash the fixed build. Read description.txt "
                     "and the submit attempts (with sanitizer output) in the prior results; read the "
                     "source if needed. End with the JSON block per the discriminate schema."),
}


def _kickoff_for(stage: str, backend_name: str) -> str:
    """Generate's submit mechanism is backend-specific: the claude_api backend exposes a
    `submit_poc` tool; the claude_code backend submits via `bash submit.sh`."""
    if stage == "generate":
        submit_hint = "the `submit_poc` tool" if backend_name == "claude_api" else "`bash submit.sh <poc>`"
        return (f"Generate the PoC and test it with {submit_hint}; iterate until you trigger the "
                "described bug (exit_code != 0). If the prior results carry a `discriminate` "
                "retarget_instruction, a previous attempt was rejected as a likely false positive — "
                "pursue a DIFFERENT theory, not a tweak. End with the JSON block.")
    return _KICKOFF[stage]


@lru_cache(maxsize=32)
def _read(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


def _render(template: str, tokens: dict[str, str | None]) -> str:
    """Substitute {{tokens}}; None values render as empty string (A2A mode has no masked_id)."""
    out = template
    for k, v in tokens.items():
        out = out.replace("{{" + k + "}}", "" if v is None else str(v))
    return out


def build_request(
    stage: str,
    plan: PipelinePlan,
    meta: TaskMeta,
    handle: TaskHandle,
    prior_results: dict[str, dict],
    settings: Settings,
    backend_name: str,
    instrument_container: str | None = None,
    mcp_endpoint: str | None = None,
) -> StageRequest:
    scfg = settings.stage_cfg(stage)
    desc_file = Path(handle.task_dir) / "description.txt"
    try:
        description_txt = desc_file.read_text(errors="replace")[:4000] if desc_file.is_file() else "(no description.txt)"
    except OSError:
        description_txt = "(no description.txt)"
    # Atomic-vuln classification: recon/analyze pick `vuln_classes` from the type menu; generate
    # gets ONLY the matching Example(V_i) recipes (targeted + token-cheap vs shipping all 28).
    from . import atomic_vulns
    vuln_classes = (prior_results.get("analyze", {}).get("vuln_classes")
                    or prior_results.get("recon", {}).get("vuln_classes") or [])
    tokens = {
        "project": meta.project,
        "crash_type": meta.crash_type,
        "input_format": meta.input_format,
        "difficulty": plan.difficulty,
        "masked_id": handle.masked_id,
        "instrument_container": instrument_container or "(none)",
        "description_txt": description_txt,
        "recon_json": json.dumps(prior_results.get("recon", {}), ensure_ascii=False, indent=2),
        "prior_json": json.dumps(prior_results, ensure_ascii=False, indent=2),
        "vuln_type_menu": atomic_vulns.menu(),            # used by recon/analyze (classification vocab)
        "vuln_examples": atomic_vulns.retrieve(vuln_classes),  # used by generate (matched recipes)
    }

    parts = [_render(_read("shared/situational_context.md"), tokens)]
    if not plan.minimize_info:
        # Global, task-agnostic knowledge base (disclosed at submission; placed early in the
        # static prefix for prompt-cache reuse). Dropped on the minimize_info (lean) route.
        parts.append(_render(_read("shared/knowledge.md"), tokens))
    parts.append(_render(_read(_STAGE_PROMPT[stage]), tokens))
    if not plan.minimize_info:
        parts.append(_render(_read("shared/tool_profile.md"), tokens))
    parts.append(_render(_read("shared/output_contracts.md"), tokens))
    system_prompt = "\n\n".join(parts)

    thinking = None
    if plan.thinking and stage in ("analyze", "generate"):
        thinking = ThinkingConfig(budget_tokens=settings.thinking_budget)

    return StageRequest(
        stage=stage,
        system_prompt=system_prompt,
        kickoff=_kickoff_for(stage, backend_name),
        cwd=handle.task_dir,
        model=plan.stage_models.get(stage) or settings.model_for(stage, plan.difficulty),
        allowed_tools=list(scfg.get("tools", ["Bash", "Read", "Grep", "Glob"])),
        permission_tier=scfg.get("tier", "read_only"),
        max_turns=int(scfg.get("max_turns", 20)),
        max_budget_usd=settings.per_task_soft_usd,
        thinking=thinking,
        prior_results=prior_results,
        instrument_container=instrument_container,
        mcp_endpoint=mcp_endpoint,
        recon_summary=prior_results.get("recon"),
        submit_sh=str(handle.task_dir / "submit.sh"),
        task_id_masked=handle.masked_id,
        agent_id=handle.agent_id,
        checksum=handle.checksum,
        server_url=handle.server_url,
    )
