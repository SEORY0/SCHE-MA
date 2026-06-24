#!/usr/bin/env python3
"""Distill solved-task artifacts into the OKF knowledge bundle (skills/knowledge/okf/).

Reads data/okf_solves/*.json, keeps only TRAIN-split solved tasks (membership in
data/okf_split.json — NEVER eval), aggregates them by abstract key (vuln-class /
format-family / strategy), and writes OKF concept docs containing ONLY generalized
patterns + support counts. No task ids, no concrete offsets reach the bundle.

A separate PRIVATE provenance ledger (data/okf_provenance/, gitignored, never injected)
maps each concept to its supporting train task ids for audit/dedup.

Leakage guard: after writing, the script scans the whole bundle for task-id-shaped
tokens and for any eval task id, and fails loudly if any are found.

Usage:  python scripts/okf_distill.py --split pilot
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from schemata.knowledge import atomic_vulns  # noqa: E402
import okf_knowledge as OK  # noqa: E402  (curated rich format/strategy specs)

SOLVES = ROOT / "data" / "okf_solves"
SPLIT = ROOT / "data" / "okf_split.json"
BUNDLE = ROOT / "skills" / "knowledge" / "okf"
PROV = ROOT / "data" / "okf_provenance"
TS = "2026-06-24T00:00:00Z"   # fixed stamp (deterministic; real ts via git history)

def _solved_train() -> list[dict]:
    split = json.loads(SPLIT.read_text())
    train = set(split.get("train", [])) | set(split.get("pilot", {}).get("train", []))
    eval_ = set(split.get("eval", [])) | set(split.get("pilot", {}).get("eval", []))
    arts = []
    for p in sorted(SOLVES.glob("*.json")):
        try:
            a = json.loads(p.read_text())
        except Exception:
            continue
        tid = a.get("task_id")
        if not a.get("solved") or tid not in train:
            continue
        if tid in eval_:                       # paranoia: never mine an eval task
            continue
        arts.append(a)
    return arts


def _fm(d: dict) -> str:
    lines = ["---"]
    for k, v in d.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(map(str, v))}]")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _write(path: Path, frontmatter: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_fm(frontmatter) + "\n" + body.strip() + "\n")


def _empirical(items: list[dict]) -> str:
    """Observed-in-training stats block (abstract; no task ids)."""
    strat = dict(Counter(i.get("strategy") for i in items))
    fmt = dict(Counter(i.get("format_family") for i in items if i.get("format_family") != "unknown"))
    sinks = sorted({i.get("sink_shape") for i in items if i.get("sink_shape")})
    lines = [f"- Support: {len(items)} train-set solves.",
             f"- Winning strategies (observed): {strat}"]
    if fmt:
        lines.append(f"- Format families (observed): {fmt}")
    if sinks:
        lines.append(f"- Abstract sink shapes (observed): {', '.join(sinks)}")
    return "\n".join(lines)


def _vuln_body(vc: str, entry: dict, items: list[dict]) -> str:
    """Full atomic recipe (sink/recipe/byte_example/fp_guard/strategies/families) + empirical."""
    parts = ["# Schema"]
    if entry:
        parts.append(f"- **Sink**: {entry.get('sink','(unspecified)')}")
        parts.append(f"- **Recipe (the single invariant to violate)**: {entry.get('recipe','')}")
        if entry.get("byte_example"):
            parts.append(f"- **Byte pattern (ILLUSTRATIVE — instantiate against the real format, do "
                         f"NOT copy literally)**: {entry['byte_example']}")
        parts.append(f"- **Avoid (would crash the FIXED build too → score 0)**: "
                     f"{entry.get('fp_guard','Change only the one invariant field at the sink.')}")
        cs = entry.get("construction_strategies") or []
        if cs:
            parts.append("\n## Construction strategies (try in order; pick the first whose precondition holds)")
            for s in cs:
                parts.append(f"- **{s.get('name')}** (when: {s.get('when')}):\n  {s.get('steps')}")
        cf = entry.get("candidate_families") or []
        if cf:
            parts.append("\n## Candidate families (generate ≥1 per applicable family)")
            for f in sorted(cf, key=lambda x: x.get("priority", 99)):
                parts.append(f"- [{f.get('priority')}] **{f.get('name')}**: {f.get('description')}")
    else:
        parts.append(f"- Crash class `{vc}`. Build the shortest structurally-valid input that reaches "
                     f"the described sink, then violate the one invariant the patch adds.")
    parts.append("\n# Examples\n" + _empirical(items))
    parts.append("\n# Citations\n- Distilled from train-set solves of this crash class + the atomic "
                 "vulnerability library (task-agnostic).")
    return "\n".join(parts)


def _format_body(fam: str, spec: str | None, items: list[dict]) -> str:
    head = spec if spec else (
        f"## Structure\nNot yet curated in detail. Identify the magic/header, keep the prefix valid to "
        f"reach the sink, and prefer seed-mutate when a corpus exists.")
    return f"# Schema\n{head}\n\n# Examples\n{_empirical(items)}\n\n# Citations\n" \
           f"- Distilled from train-set solves with this format + curated format knowledge."


def _strategy_body(strat: str, spec: str | None, items: list[dict]) -> str:
    head = spec if spec else f"## What\nStrategy `{strat}`."
    return f"{head}\n\n## Observed\n{_empirical(items)}"


def distill(split_name: str) -> dict:
    arts = _solved_train()
    if BUNDLE.exists():
        # rewrite cleanly each run (idempotent)
        for p in BUNDLE.rglob("*.md"):
            p.unlink()
    PROV.mkdir(parents=True, exist_ok=True)

    by_vuln: dict[str, list[dict]] = defaultdict(list)
    by_fmt: dict[str, list[dict]] = defaultdict(list)
    by_strat: dict[str, list[dict]] = defaultdict(list)
    for a in arts:
        for vc in (a.get("vuln_class") or ["unclassified"]):
            by_vuln[vc].append(a)
        by_fmt[a.get("format_family") or "unknown"].append(a)
        by_strat[a.get("strategy") or "unknown"].append(a)

    atomic = atomic_vulns.load()
    counts = {"vuln-classes": 0, "formats": 0, "strategies": 0}

    # --- vuln-class concepts (full atomic recipe + empirical) ---
    for vc, items in sorted(by_vuln.items()):
        if vc == "unclassified":            # no ASan label → not a retrievable key
            continue
        entry = atomic.get(vc, {})
        body = _vuln_body(vc, entry, items)
        _write(BUNDLE / "vuln-classes" / f"{vc}.md", {
            "type": "vuln-class", "title": entry.get("label", vc),
            "description": f"How to construct a PoC for {entry.get('label', vc)} (sink, invariant, "
                           f"strategies, FP guard).",
            "resource": f"cybergym://vuln-class/{vc}", "tags": [vc],
            "timestamp": TS, "okf_support": len(items),
        }, body)
        (PROV / f"vuln-class__{vc}.json").write_text(json.dumps(
            {"concept": f"vuln-classes/{vc}", "support_task_ids": [i["task_id"] for i in items]}, indent=2))
        counts["vuln-classes"] += 1

    # --- format-family concepts (full curated spec + empirical) ---
    for fam, items in sorted(by_fmt.items()):
        if fam == "unknown":
            continue
        spec = OK.FORMAT_SPECS.get(fam)
        syn = OK.FORMAT_SYNONYMS.get(fam, [])
        body = _format_body(fam, spec, items)
        _write(BUNDLE / "formats" / f"{fam}.md", {
            "type": "format-family", "title": f"{fam} format",
            "description": f"Structure, build skeleton, and bug-prone areas of the {fam} input format.",
            "resource": f"cybergym://format/{fam}", "tags": [fam] + [s for s in syn if s != fam],
            "timestamp": TS, "okf_support": len(items),
        }, body)
        counts["formats"] += 1

    # --- strategy concepts (full methodology + empirical) ---
    for strat, items in sorted(by_strat.items()):
        if strat in ("unknown", None):
            continue
        spec = OK.STRATEGY_SPECS.get(strat)
        trig = OK.STRATEGY_TRIGGERS.get(strat, [])
        body = _strategy_body(strat, spec, items)
        first_line = (spec or strat).strip().splitlines()[0] if spec else strat
        _write(BUNDLE / "strategies" / f"{strat}.md", {
            "type": "strategy", "title": f"{strat} strategy",
            "description": first_line.lstrip("# ")[:110],
            "resource": f"cybergym://strategy/{strat}", "tags": [strat] + trig,
            "timestamp": TS, "okf_support": len(items),
        }, body)
        counts["strategies"] += 1

    # --- index.md + log.md ---
    idx = ["---", "type: index", f"title: CyberGym OKF knowledge bundle", "okf_version: \"0.1\"",
           "---", "", "# CyberGym PoC Knowledge (OKF)", "",
           "Task-agnostic, distilled patterns. Abstract only — no task ids, no concrete offsets.", ""]
    for sub in ("vuln-classes", "formats", "strategies"):
        d = BUNDLE / sub
        if d.exists():
            idx.append(f"## {sub}")
            for p in sorted(d.glob("*.md")):
                idx.append(f"- [{p.stem}]({sub}/{p.name})")
            idx.append("")
    (BUNDLE / "index.md").write_text("\n".join(idx))
    (BUNDLE / "log.md").write_text(
        f"# Log\n\n## {TS[:10]}\n* Distilled bundle from {len(arts)} train solves "
        f"({split_name}): {counts}.\n")

    # --- leakage guard ---
    leaks = _audit_leakage()
    summary = {"train_solves": len(arts), "concepts": counts, "leaks": leaks}
    return summary


def _audit_leakage() -> dict:
    split = json.loads(SPLIT.read_text())
    eval_ids = set(split.get("eval", [])) | set(split.get("pilot", {}).get("eval", []))
    taskid_re = re.compile(r"\b(?:arvo|oss-fuzz):\d+\b")
    bad_taskid: list[str] = []
    bad_eval: list[str] = []
    for p in BUNDLE.rglob("*.md"):
        text = p.read_text()
        if taskid_re.search(text):
            bad_taskid.append(p.name)
        for ev in eval_ids:
            if ev in text:
                bad_eval.append(f"{p.name}:{ev}")
    return {"task_id_tokens": bad_taskid, "eval_ids": bad_eval}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="pilot")
    args = ap.parse_args()
    s = distill(args.split)
    print(json.dumps(s, indent=2))
    leaks = s["leaks"]
    if leaks["task_id_tokens"] or leaks["eval_ids"]:
        print("LEAKAGE DETECTED — bundle is invalid", file=sys.stderr)
        sys.exit(2)
    print("leakage audit: clean ✓")


if __name__ == "__main__":
    main()
