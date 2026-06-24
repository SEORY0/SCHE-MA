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
from schemata.knowledge import atomic_vulns  # noqa: E402

SOLVES = ROOT / "data" / "okf_solves"
SPLIT = ROOT / "data" / "okf_split.json"
BUNDLE = ROOT / "skills" / "knowledge" / "okf"
PROV = ROOT / "data" / "okf_provenance"
TS = "2026-06-24T00:00:00Z"   # fixed stamp (deterministic; real ts via git history)

# Curated, task-agnostic structure notes per format family (authored from the 8-step
# procedure — generalizable, no task specifics).
_FORMAT_NOTES = {
    "isobmff": "Nested box container: each box = size(4, BE) + type(4) + payload; boxes nest "
               "(ftyp, meta, mdat...). Seed-mutate a shipped sample — building valid nesting "
               "from scratch is costly. The bug usually hides in an auxiliary/alpha/derived plane.",
    "chunked-image": "PNG/MNG-style: 8-byte signature + repeated [len(4,BE)|type(4)|data|crc(4)]. "
                     "CRC is typically unchecked by the decoder. Build with `construct`; violate one "
                     "length/field while keeping the chunk stream otherwise valid.",
    "riff": "RIFF: 'RIFF'+size+'WAVE' then sub-chunks [id(4)|size(4,LE)|data]. Mismatch a sub-chunk "
            "size vs payload. Build with `construct`.",
    "font": "sfnt table directory: header + numTables*[tag|checksum|offset|length]; CFF/glyf/loca "
            "tables. Variation (CFF2/fvar) paths are bug-rich. Seed-mutate a real font when available.",
    "jpeg": "Marker stream: 0xFFxx segments with 2-byte big-endian lengths. Seed-mutate a real JPEG.",
    "tiff": "IFD: header + tag entries (tag,type,count,value/offset). Offsets point into the file; "
            "an out-of-range offset/count is a classic sink.",
    "elf": "ELF header + program/section headers with offsets/sizes into the file. Mutate a header "
           "size/offset field of a real binary.",
    "pdf": "Object/xref structure; many parsers are lenient. Seed-mutate a minimal PDF.",
    "xml": "Text tree; depth/entity expansion and attribute handling are common sinks.",
    "pcap": "Global header + per-packet [ts|caplen|len|bytes]; caplen vs len mismatch is a classic.",
}

# Curated strategy methodology (the 8-step procedure pieces), task-agnostic.
_STRATEGY_NOTES = {
    "seed-sweep": "Run EVERY in-repo corpus/seed file through the target first. For complex/container "
                  "formats a shipped seed frequently already reproduces the bug (or is one field away). "
                  "Decisive tool: find_seeds. Always the first move when seeds exist.",
    "seed-mutate": "Copy the closest seed as a bytearray and patch ONLY the single invariant field at "
                   "the sink (length/index/count); keep every other byte identical. Drastically cheaper "
                   "than from-scratch and avoids crashing the fix (which a structural change would).",
    "hint-literal": "When the description states an explicit input (a directive, magic, or boundary "
                    "integer), feed it verbatim. Text/source targets (assemblers, interpreters) often "
                    "describe the trigger literally.",
    "construct": "For nested binary containers, build the skeleton declaratively with `construct` "
                 "(Rebuild lengths from data), fill valid defaults, then violate one field. Avoids "
                 "hand-counting offsets.",
    "tiny-probe": "Generic tiny inputs (empty, 1 byte, short ASCII). Rarely hits a targeted bug; only "
                  "catches trivially-shallow crashes. Never a substitute for reaching the real sink.",
}


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

    # --- vuln-class concepts ---
    for vc, items in sorted(by_vuln.items()):
        if vc == "unclassified":            # no ASan label → not a retrievable key
            continue
        strat_hist = Counter(i.get("strategy") for i in items)
        fmt_hist = Counter(i.get("format_family") for i in items)
        entry = atomic.get(vc, {})
        recipe = entry.get("recipe", "(see atomic vuln library)")
        fp = entry.get("fp_guard", "Change only the single invariant field at the sink.")
        body = (
            f"# Schema\n"
            f"- Recipe (abstract): {recipe}\n"
            f"- Avoid (would crash the fix too → score 0): {fp}\n"
            f"# Examples\n"
            f"- Winning strategies observed: {dict(strat_hist)}\n"
            f"- Format families observed: {dict(fmt_hist)}\n"
            f"# Citations\n"
            f"- Distilled from {len(items)} train-set solves of this crash class.\n"
        )
        _write(BUNDLE / "vuln-classes" / f"{vc}.md", {
            "type": "vuln-class", "title": entry.get("label", vc),
            "description": f"Distilled PoC pattern for {entry.get('label', vc)}.",
            "resource": f"cybergym://vuln-class/{vc}", "tags": [vc],
            "timestamp": TS, "okf_support": len(items),
        }, body)
        (PROV / f"vuln-class__{vc}.json").write_text(json.dumps(
            {"concept": f"vuln-classes/{vc}", "support_task_ids": [i["task_id"] for i in items]}, indent=2))
        counts["vuln-classes"] += 1

    # --- format-family concepts ---
    for fam, items in sorted(by_fmt.items()):
        if fam == "unknown":
            continue
        strat_hist = Counter(i.get("strategy") for i in items)
        note = _FORMAT_NOTES.get(fam, "Structure not yet curated; prefer seed-mutate when seeds exist.")
        syn = {"isobmff": ["heic", "heif", "avif", "mp4"], "chunked-image": ["png", "mng"],
               "riff": ["wav"], "font": ["ttf", "otf", "woff"]}.get(fam, [])
        body = (
            f"# Schema\n- {note}\n"
            f"# Examples\n- Winning strategies for this format: {dict(strat_hist)}\n"
            f"# Citations\n- Distilled from {len(items)} train-set solves with this format.\n"
        )
        _write(BUNDLE / "formats" / f"{fam}.md", {
            "type": "format-family", "title": f"{fam} format",
            "description": f"Construction notes for the {fam} input format.",
            "resource": f"cybergym://format/{fam}", "tags": [fam] + syn,
            "timestamp": TS, "okf_support": len(items),
        }, body)
        counts["formats"] += 1

    # --- strategy concepts ---
    for strat, items in sorted(by_strat.items()):
        if strat in ("unknown", None):
            continue
        note = _STRATEGY_NOTES.get(strat, "")
        body = (
            f"# Schema\n- {note}\n"
            f"# Examples\n- Used in {len(items)} train-set solves.\n"
            f"# Citations\n- Empirical win count: {len(items)}.\n"
        )
        # map strategies to the task_properties / triggers that should surface them
        trig = {"seed-sweep": ["seed_mutation"], "seed-mutate": ["seed_mutation"],
                "construct": ["format_complex", "nested_structures", "binary_format"],
                "hint-literal": ["flat_text"]}.get(strat, [])
        _write(BUNDLE / "strategies" / f"{strat}.md", {
            "type": "strategy", "title": f"{strat} strategy",
            "description": _STRATEGY_NOTES.get(strat, strat)[:90],
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
