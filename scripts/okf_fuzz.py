#!/usr/bin/env python3
"""Scalable fuzzing solver — run each task's OWN libFuzzer harness to rediscover the crash.

CyberGym/OSS-Fuzz bugs are reproducible by construction (a fuzzer found them). For DEEP-STATEFUL
or FLAKY bugs that resist hand-construction, the reliable method is to run the harness binary as a
fuzzer with its seed corpus until it writes a crash-<sha1>. This collects such finds (with their
ASan/MSan trace) for target-match verification — it does NOT auto-record, because a fuzzer may
surface a DIFFERENT bug than description.txt describes (only the matching one counts).

Usage:
  python scripts/okf_fuzz.py --tasks arvo:111,oss-fuzz:222 --time 300 --jobs 3
  python scripts/okf_fuzz.py --from-escalate 8 --time 300 --jobs 3   # first N train escalated tasks
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import okf_solve as S  # noqa: E402  (reuse image_of/source_of/parse_asan/is_crash)

FINDS = ROOT / "data" / "okf_fuzz_finds"
SPLIT = ROOT / "data" / "okf_split.json"


def _sh(args, timeout):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "[timeout]"


def fuzz_task(task_id: str, fuzz_time: int) -> dict:
    safe = S.safe(task_id)
    out = {"task_id": task_id, "crash": False, "crash_type": None, "sink_fn": None,
           "sink_loc": None, "poc_b64": None, "error": None}
    name = f"fz_{safe}_{uuid.uuid4().hex[:6]}"
    _sh(["docker", "rm", "-f", name], 30)
    rc, _ = _sh(["docker", "run", "-d", "--name", name, "--security-opt", "seccomp=unconfined",
                 S.image_of(task_id), "sleep", "infinity"], 120)
    if rc != 0:
        out["error"] = "container_start_failed"; return out
    try:
        # locate harness binary + unzip any seed corpus, then fuzz
        script = (
            'B=$(grep -oP "(?<=exec )\\S+|/out/\\S*fuzz\\S*|/out/\\S*_fuzzer" /bin/arvo 2>/dev/null | head -1); '
            '[ -z "$B" ] && B=$(grep -oE "/out/\\S+" /usr/local/bin/run_poc 2>/dev/null | head -1); '
            '[ -z "$B" ] && B=$(ls /out/*_fuzzer /out/*fuzz* 2>/dev/null | head -1); '
            'mkdir -p /tmp/corp /tmp/out; '
            'for z in /out/*corpus*.zip /out/*seed*.zip; do [ -f "$z" ] && (cd /tmp/corp && unzip -oq "$z" 2>/dev/null); done; '
            'cd /tmp/out; ASAN_OPTIONS=abort_on_error=1 MSAN_OPTIONS=abort_on_error=1 '
            f'timeout {fuzz_time + 20} "$B" -max_total_time={fuzz_time} -rss_limit_mb=4096 -jobs=2 -workers=2 /tmp/corp >/tmp/fz.log 2>&1; '
            'CF=$(ls /tmp/out/crash-* 2>/dev/null | head -1); echo "CRASHFILE:$CF"'
        )
        rc, o = _sh(["docker", "exec", name, "bash", "-lc", script], fuzz_time + 120)
        cf = ""
        for line in o.splitlines():
            if line.startswith("CRASHFILE:"):
                cf = line[len("CRASHFILE:"):].strip()
        if not cf:
            return out
        # got a crash: run the crash file once to capture the sanitizer trace
        rc2, trace = _sh(["docker", "exec", name, "bash", "-lc",
                          f'B=$(grep -oP "(?<=exec )\\S+|/out/\\S*fuzz\\S*" /bin/arvo 2>/dev/null|head -1); '
                          f'[ -z "$B" ] && B=$(grep -oE "/out/\\S+" /usr/local/bin/run_poc 2>/dev/null|head -1); '
                          f'cp {cf} /tmp/poc; "$B" /tmp/poc 2>&1 || true'], 60)
        info = S.parse_asan(trace) or {}
        # copy crash file out (b64)
        rc3, b64 = _sh(["docker", "exec", name, "bash", "-lc", f'base64 -w0 {cf}'], 60)
        out.update(crash=True, crash_type=info.get("crash_type"), sink_fn=info.get("sink_fn"),
                   sink_loc=info.get("sink_loc"), poc_b64=b64.strip() if rc3 == 0 else None)
        return out
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"[:200]; return out
    finally:
        _sh(["docker", "rm", "-f", name], 30)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default="")
    ap.add_argument("--from-escalate", type=int, default=0)
    ap.add_argument("--time", type=int, default=300)
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args()
    FINDS.mkdir(parents=True, exist_ok=True)

    tasks = [t for t in args.tasks.split(",") if t]
    if args.from_escalate:
        esc = [l.strip() for l in (ROOT / "data" / "okf_escalate.txt").read_text().splitlines() if l.strip()]
        split = json.loads(SPLIT.read_text())
        train = set(split["train"]) | set(split["pilot"]["train"])
        solved = {json.loads(p.read_text()).get("task_id") for p in (ROOT / "data" / "okf_solves").glob("*.json")}
        tasks = [t for t in dict.fromkeys(esc) if t in train and t not in solved][:args.from_escalate]

    print(f"fuzzing {len(tasks)} tasks x {args.time}s, {args.jobs} parallel")
    finds = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(fuzz_task, t, args.time): t for t in tasks}
        for fut in as_completed(futs):
            r = fut.result()
            (FINDS / f"{S.safe(r['task_id'])}.json").write_text(json.dumps(r, indent=2))
            flag = "CRASH" if r["crash"] else ("ERR" if r["error"] else "none")
            print(f"[{flag:5}] {r['task_id']:24} {r.get('crash_type') or ''} "
                  f"{r.get('sink_loc') or ''}", flush=True)
            if r["crash"]:
                finds.append(r)
    print(f"\n=== {len(finds)}/{len(tasks)} crashed (VERIFY target-match before recording) ===")


if __name__ == "__main__":
    main()
