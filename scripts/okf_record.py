#!/usr/bin/env python3
"""Record a MANUAL (agent-reasoned) solve into the okf_solves artifact store.

The automated harness (okf_solve.py) only catches seed/hint cases. The bulk of tasks need
real PoC construction by the agent. When the agent crafts a winning PoC by hand, this script
validates it in the vulnerable container, parses the ASan trace, and writes the SAME abstract
artifact the distiller mines — so manual solves flow into the OKF bundle uniformly.

Stores only abstract labels (vuln class, format family, strategy, tools). PoC bytes stay in
the gitignored workdir. Refuses to record an eval-split task (held-out integrity).

Usage:
  python scripts/okf_record.py --task arvo:10400 --poc notes/manual/arvo_10400/poc \
      --strategy construct --format chunked-image --tools construct,find_seeds
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import okf_solve as S  # noqa: E402  (reuse Box / parse_asan / _apply_asan / format_family)

SPLIT = ROOT / "data" / "okf_split.json"


def _split_of(task_id: str) -> str:
    s = json.loads(SPLIT.read_text())
    if task_id in set(s.get("eval", [])) | set(s.get("pilot", {}).get("eval", [])):
        return "eval"
    if task_id in set(s.get("train", [])) | set(s.get("pilot", {}).get("train", [])):
        return "train"
    return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--poc", required=True, help="path to the winning PoC file")
    ap.add_argument("--strategy", default="construct",
                    help="seed-mutate|construct|hint-literal|raw|...")
    ap.add_argument("--format", dest="fmt", default="unknown")
    ap.add_argument("--tools", default="", help="comma-separated tools used")
    ap.add_argument("--crash-type", dest="crash_type", default=None,
                    help="override crash type when the binary segfaults with no ASan report "
                         "(e.g. 'stack-overflow' for a deep-recursion crash)")
    args = ap.parse_args()

    split = _split_of(args.task)
    if split == "eval":
        print(f"REFUSED: {args.task} is in the EVAL split — never mine a held-out task.",
              file=sys.stderr)
        sys.exit(2)

    poc = Path(args.poc)
    if not poc.is_file():
        print(f"no such poc: {poc}", file=sys.stderr); sys.exit(1)

    meta = S.task_meta().get(args.task, {})
    box = S.Box(args.task)
    if not box.start():
        print("container start failed", file=sys.stderr); sys.exit(1)
    try:
        rc, out = box.run_poc(poc)
    finally:
        box.stop()

    if not S.is_crash(rc, out):
        print(f"PoC did NOT crash the vulnerable build (rc={rc}); not recorded.", file=sys.stderr)
        sys.exit(3)

    info = S.parse_asan(out) or {}
    if not info.get("crash_type") and args.crash_type:
        info["crash_type"] = args.crash_type        # no ASan report -> use the agent's label
    art = {
        "task_id": args.task, "source": S.source_of(args.task),
        "project": meta.get("project_name", "?"), "language": meta.get("project_language", "?"),
        "split": split, "solved": True, "attempted": True,
        "strategy": args.strategy, "crash_type": None, "vuln_class": [], "sink_shape": None,
        "format_family": args.fmt,
        "harness_convention": None,
        "tools_used": [t for t in args.tools.split(",") if t],
        "seed_used": args.strategy in ("seed-sweep", "seed-mutate"),
        "submissions_n": 1, "asan_dedup_token": None, "error": None, "manual": True,
    }
    S._apply_asan(art, info)
    out_path = S.OUT_DIR / f"{S.safe(args.task)}.json"
    S._save(art, out_path)
    print(f"recorded {args.task} [{split}] strategy={args.strategy} "
          f"vuln={art['vuln_class']} crash={art['crash_type']}")


if __name__ == "__main__":
    main()
