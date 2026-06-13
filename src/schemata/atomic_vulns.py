"""Atomic Vulnerability library — the 28 CyberGym crash types as Example(V_i) recipes.

`PoC = F(C, Example(V)) ≃ F(C, ∪ Example(V_i))`: the agent classifies a task into all
applicable atomic types (recon/analyze emit `vuln_classes`), and ONLY those recipes are
injected into the Stage-3 generate prompt (`retrieve`). This is the "Bug Type memory" —
targeted (better PoCs) and token-cheap (we don't ship all 28 every task).

`classify_from_crash_type` maps a sanitizer-reported crash string (e.g. "Heap-buffer-overflow
READ 1") to type ids, used by the level3 mechanical fast-path where the LLM recon is skipped.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from .config import PROMPTS_DIR

_LIB_PATH = PROMPTS_DIR / "knowledge" / "atomic_vulns.json"


@lru_cache(maxsize=1)
def load() -> dict[str, dict]:
    with open(_LIB_PATH, encoding="utf-8") as f:
        return json.load(f)["types"]


def _norm(s: str) -> str:
    """lowercase, unify separators to '-', drop a trailing ' N' (ASan appends an id), collapse."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"-\d+$", "", s)        # "...-read-1" -> "...-read"
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s


def all_type_ids() -> list[str]:
    return list(load().keys())


def menu() -> str:
    """Compact id:label vocabulary for the classification prompt (static, cache-friendly)."""
    return "\n".join(f"- {tid}: {e['label']} ({e['sanitizer']})" for tid, e in load().items())


def _candidates(tid: str, entry: dict) -> list[str]:
    return [tid] + [_norm(a) for a in entry.get("aliases", [])] + [_norm(entry.get("label", ""))]


def classify_from_crash_type(crash_type: str) -> list[str]:
    """Sanitizer crash string -> matching atomic type ids (all applicable).

    A bare family ("heap-buffer-overflow") with no READ/WRITE matches BOTH variants.
    """
    n = _norm(crash_type)
    if not n:
        return []
    out: list[str] = []
    for tid, entry in load().items():
        cands = _candidates(tid, entry)
        match = (
            n in cands                       # exact id / alias / label
            or tid.startswith(n + "-")       # bare family ("heap-buffer-overflow") -> all variants
            # verbose sanitizer strings (e.g. "segv on unknown address 0x..."): a specific alias
            # is contained in the crash string. Exclude family-root aliases (a prefix of this tid,
            # e.g. "heap-buffer-overflow" under -read) so a WRITE crash never pulls in the READ entry.
            or any(c in n for c in cands if len(c) >= 10 and not tid.startswith(c))
        )
        if match and tid not in out:
            out.append(tid)
    return out


def retrieve(classes) -> str:
    """Render the Example(V_i) blocks for the given type ids (unknown ids ignored)."""
    lib = load()
    seen: list[str] = []
    for c in classes or []:
        cid = _norm(str(c))
        if cid in lib and cid not in seen:
            seen.append(cid)
    if not seen:
        return ""
    blocks = ["<atomic_vuln_examples>",
              "Example(V_i) for the classified atomic vulnerability type(s). Build the PoC from these "
              "(minimum-margin, single-invariant); avoid each FP guard."]
    for cid in seen:
        e = lib[cid]
        blocks.append(
            f"\n### {e['label']} ({e['sanitizer']})\n"
            f"- sink: {e['sink']}\n"
            f"- Example(V_i): {e['recipe']}\n"
            f"- avoid (would crash the fix too → score 0): {e['fp_guard']}"
        )
    blocks.append("</atomic_vuln_examples>")
    return "\n".join(blocks)
