#!/usr/bin/env python3
"""Verify fuzzer crash finds against description.txt and record only target-matching ones.

A fuzzer may surface a DIFFERENT bug than the task describes. This conservatively checks each
find in data/okf_fuzz_finds/ against the task's description: the crash-type FAMILY and/or the
sink function name must align with the description. High-confidence matches are re-validated in
a container and recorded (strategy=fuzzer); the rest are reported for manual review or skipped.

Usage:  python scripts/okf_fuzz_verify.py [--record]
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import okf_solve as S  # noqa: E402

FINDS = ROOT / "data" / "okf_fuzz_finds"
DATA = Path("/data/cybergym_data/data")

# crash-type family -> description keywords that confirm it
_FAMILY_KW = {
    "heap-buffer-overflow": ["heap-buffer-overflow", "heap overflow", "buffer overflow", "out-of-bounds", "out of bounds", "oob"],
    "stack-buffer-overflow": ["stack-buffer-overflow", "stack overflow", "stack buffer"],
    "global-buffer-overflow": ["global-buffer-overflow", "global buffer"],
    "use-of-uninitialized-value": ["uninitialized", "uninitialised", "msan", "unpredictable", "not initialize", "uninit"],
    "heap-use-after-free": ["use-after-free", "use after free", "uaf", "dangling"],
    "double-free": ["double-free", "double free"],
    "stack-overflow": ["stack-overflow", "stack exhaustion", "recursion", "infinite"],
    "memory-leak": ["memory leak", "leak"],
    "negative-size-param": ["negative size", "negative"],
    "integer": ["integer overflow", "integer", "overflow"],
}


def family(crash_type: str | None) -> str:
    if not crash_type:
        return ""
    n = crash_type.lower().split()[0]
    return n


def desc_for(task_id: str) -> str:
    sub = "arvo" if task_id.startswith("arvo") else "oss-fuzz"
    p = DATA / sub / task_id.split(":")[1] / "description.txt"
    try:
        return p.read_text(errors="replace").lower()
    except OSError:
        return ""


def match_confidence(find: dict, desc: str) -> tuple[str, str]:
    """Return (verdict, reason): verdict in {match, mismatch, ambiguous}."""
    if not desc:
        return "ambiguous", "no description"
    fam = family(find.get("crash_type"))
    sink = (find.get("sink_fn") or "").lower()
    fam_kw = _FAMILY_KW.get(fam, [fam] if fam else [])
    fam_hit = any(k in desc for k in fam_kw)
    # crash families the description explicitly names
    desc_families = [f for f, kws in _FAMILY_KW.items() if any(k in desc for k in kws)]
    # CONTRADICTION FIRST: description names crash families, none of which is the fuzzer's
    # -> different bug than described, regardless of overlapping function names.
    if desc_families and not fam_hit:
        return "mismatch", f"description indicates {desc_families}, fuzzer found '{fam}'"
    sink_hit = bool(sink) and len(sink) >= 4 and sink in desc
    if fam_hit and sink_hit:
        return "match", f"family '{fam}' + sink '{sink}' both in description"
    if fam_hit:
        return "match", f"crash family '{fam}' matches description"
    if sink_hit:
        return "match", f"sink '{sink}' named in description (family not contradicted)"
    return "ambiguous", f"family '{fam}' not clearly in description"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="actually record matches (re-validates)")
    args = ap.parse_args()
    if not FINDS.exists():
        print("no finds"); return
    matches = mism = amb = recorded = 0
    for f in sorted(FINDS.glob("*.json")):
        d = json.loads(f.read_text())
        if not d.get("crash"):
            continue
        tid = d["task_id"]
        verdict, reason = match_confidence(d, desc_for(tid))
        print(f"[{verdict:9}] {tid:22} {d.get('crash_type')} @ {d.get('sink_loc')}  -- {reason}")
        if verdict == "match":
            matches += 1
            if args.record and d.get("poc_b64"):
                wd = ROOT / "data" / "okf_work" / S.safe(tid); wd.mkdir(parents=True, exist_ok=True)
                poc = wd / "winning_poc"; poc.write_bytes(base64.b64decode(d["poc_b64"]))
                fmt = "media-container" if "ffmpeg" in tid or "libav" in desc_for(tid) else "unknown"
                r = subprocess.run([sys.executable, str(ROOT / "scripts" / "okf_record.py"),
                                    "--task", tid, "--poc", str(poc), "--strategy", "fuzzer",
                                    "--format", fmt, "--tools", "libfuzzer",
                                    "--crash-type", d.get("crash_type") or "unknown"],
                                   capture_output=True, text=True)
                ok = "recorded" in r.stdout
                print(f"            -> {'RECORDED' if ok else 'record-failed: '+r.stdout.strip()[-80:]}")
                if ok:
                    recorded += 1
        elif verdict == "mismatch":
            mism += 1
        else:
            amb += 1
    print(f"\nmatch={matches} mismatch={mism} ambiguous={amb} recorded={recorded}")


if __name__ == "__main__":
    main()
