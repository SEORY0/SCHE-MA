#!/usr/bin/env python3
"""Automated CyberGym solve harness — real tools only, NO Anthropic API / no SCHE-MA pipeline.

Drives the mechanical part of the manual 8-step PoC flow at scale so we can mine a knowledge
catalog. Fully local: reads description.txt + repo-vul.tar.gz from the data dir (Level-1 only —
never error.txt / patch.diff), runs the vulnerable container, and tries deterministic strategies:

  1. seed-sweep      — run every in-repo corpus/seed file through the target; a crashing seed wins.
  2. description-hint — extract a literal input from description.txt (e.g. an assembler directive).
  3. tiny-probes     — a few cheap generic inputs (rarely solves targeted bugs; catches trivials).

A crash = the vulnerable build exits non-zero with a sanitizer report (Level-1 pass criterion).
Tasks no strategy solves are written to the escalation queue for supervised manual reasoning.

Artifacts (data/okf_solves/<safe>.json) store ABSTRACT labels only (vuln class, format family,
strategy, tools) — never task-specific offsets. PoC bytes stay in the gitignored workdir.

Usage:
  python scripts/okf_solve.py --split pilot --set train --jobs 6
  python scripts/okf_solve.py --task arvo:10400        # single task (debug)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tarfile
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from schemata.knowledge import atomic_vulns  # noqa: E402  (vuln-class labeling reuse)

DATA_ROOT = Path("/data/cybergym_data/data")
OUT_DIR = ROOT / "data" / "okf_solves"
WORK_DIR = ROOT / "data" / "okf_work"
SPLIT_FILE = ROOT / "data" / "okf_split.json"
ESCALATE = ROOT / "data" / "okf_escalate.txt"

SEED_DIR_NAMES = ("corpus", "corpora", "seed", "seeds", "testdata", "testcase",
                  "testcases", "ossfuzz", "regression")
# never treat source/build/text files as fuzzer seeds, even inside a seed dir
_SKIP_EXTS = {".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh", ".go", ".py", ".rs",
              ".java", ".js", ".ts", ".md", ".rst", ".am", ".in", ".m4", ".sh",
              ".cmake", ".mk", ".yml", ".yaml", ".toml", ".cfg", ".html", ".css",
              ".swift", ".pl", ".rb", ".txt"}
SEED_EXTS = (".bin", ".seed", ".corpus", ".dat", ".raw", ".input",
             ".heic", ".heif", ".avif", ".png", ".mng", ".jpg", ".jpeg", ".gif",
             ".tif", ".tiff", ".wav", ".mp3", ".mp4", ".ttf", ".otf", ".woff",
             ".pdf", ".xml", ".json", ".zip", ".gz", ".elf", ".so", ".pcap")
RUN_TIMEOUT = 30          # seconds per single target run
SWEEP_CAP = 400           # max seeds to sweep per task


# ----------------------------------------------------------------------------- helpers
def safe(task_id: str) -> str:
    return task_id.replace(":", "_").replace("/", "_")


def source_of(task_id: str) -> str:
    return task_id.split(":")[0]


def data_dir(task_id: str) -> Path:
    src, tid = task_id.split(":")
    sub = "arvo" if src == "arvo" else "oss-fuzz"
    return DATA_ROOT / sub / tid


def image_of(task_id: str) -> str:
    src, tid = task_id.split(":")
    return f"n132/arvo:{tid}-vul" if src == "arvo" else f"cybergym/oss-fuzz:{tid}-vul"


def run_cmd(task_id: str) -> str:
    return "/bin/arvo" if source_of(task_id) == "arvo" else "/usr/local/bin/run_poc"


def _sh(args: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                           errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "[timeout]"


# ----------------------------------------------------------------------------- ASan parse
_ASAN_TYPE = re.compile(r"(?:ERROR|SUMMARY): \w*Sanitizer: ([a-z0-9\-]+)")
_ASAN_RW = re.compile(r"\b(READ|WRITE) of size (\d+)")
_FRAME0 = re.compile(r"#0[^\n]*? in (\S+)\s+([^\s:]+:\d+)")
_DEDUP = re.compile(r"DEDUP_TOKEN: (\S+)")


def parse_asan(out: str) -> dict | None:
    m = _ASAN_TYPE.search(out)
    if not m:
        return None
    crash_type = m.group(1)
    rw = _ASAN_RW.search(out)
    f0 = _FRAME0.search(out)
    dd = _DEDUP.search(out)
    if rw:
        crash_type = f"{crash_type} {rw.group(1)}"
    return {
        "crash_type": crash_type,
        "access": rw.group(1) if rw else None,
        "sink_fn": f0.group(1) if f0 else None,
        "sink_loc": f0.group(2) if f0 else None,
        "dedup_token": dd.group(1) if dd else None,
    }


# Fatal signals that count as a crash (CyberGym scores exit_code != 0). EXCLUDES timeout
# (137=SIGKILL from our `timeout -s KILL`, 124=GNU timeout) which folds to "not crashed".
_CRASH_SIGNALS = {134, 135, 136, 138, 139}  # SIGABRT/?/SIGFPE/SIGBUS/SIGSEGV


def is_crash(rc: int, out: str) -> bool:
    # a sanitizer report (libFuzzer prints "ERROR: ... Sanitizer"), OR a bare fatal signal
    # (e.g. a deep-recursion stack overflow that segfaults before ASan can report it).
    if rc != 0 and ("Sanitizer:" in out or "ABORTING" in out or "runtime error:" in out):
        return True
    return rc in _CRASH_SIGNALS


# ----------------------------------------------------------------------------- container
class Box:
    """A short-lived vulnerable container for one task."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.name = f"okf_{safe(task_id)}_{uuid.uuid4().hex[:6]}"
        self.cmd = run_cmd(task_id)

    def start(self) -> bool:
        _sh(["docker", "rm", "-f", self.name], timeout=30)
        rc, _ = _sh(["docker", "run", "-d", "--name", self.name,
                     image_of(self.task_id), "sleep", "infinity"], timeout=120)
        return rc == 0

    def stop(self) -> None:
        _sh(["docker", "rm", "-f", self.name], timeout=30)

    def run_poc(self, host_path: Path) -> tuple[int, str]:
        # NOTE: the wrapper MUST be called with no argument — /bin/arvo treats any arg
        # that isn't "compile"/"run" as an unknown command and skips the target. Both
        # /bin/arvo and /usr/local/bin/run_poc default to reading /tmp/poc.
        _sh(["docker", "cp", str(host_path), f"{self.name}:/tmp/poc"], timeout=60)
        return _sh(["docker", "exec", self.name, "bash", "-lc",
                    f"timeout -s KILL {RUN_TIMEOUT} {self.cmd}"], timeout=RUN_TIMEOUT + 15)

    def sweep_dir(self, container_dir: str) -> tuple[str | None, int, str]:
        """Run every file in a container dir through the target; return (winning_basename, rc, out)."""
        script = (
            f'for f in {container_dir}/*; do '
            f'[ -f "$f" ] || continue; cp "$f" /tmp/poc; '
            f'out=$(timeout -s KILL {RUN_TIMEOUT} {self.cmd} 2>&1); rc=$?; '
            f'if [ $rc -ne 0 ] && echo "$out" | grep -q "Sanitizer:"; then '
            f'echo "OKF_WIN:$(basename $f):$rc"; echo "$out"; break; fi; done'
        )
        rc, out = _sh(["docker", "exec", self.name, "bash", "-lc", script],
                      timeout=RUN_TIMEOUT * 40 + 60)
        m = re.search(r"OKF_WIN:([^:]+):(\d+)", out)
        if m:
            return m.group(1), int(m.group(2)), out
        return None, rc, out


# ----------------------------------------------------------------------------- task prep
def prepare(task_id: str) -> tuple[Path, str, list[Path]]:
    """Extract repo + read description (Level-1 only). Returns (workdir, description, seed_paths)."""
    wd = WORK_DIR / safe(task_id)
    wd.mkdir(parents=True, exist_ok=True)
    dd = data_dir(task_id)
    desc = ""
    dfile = dd / "description.txt"
    if dfile.is_file():
        desc = dfile.read_text(errors="replace")
    src_dir = wd / "src"
    if not src_dir.exists():
        tarball = dd / "repo-vul.tar.gz"
        if tarball.is_file():
            src_dir.mkdir(exist_ok=True)
            try:
                with tarfile.open(tarball) as tf:
                    tf.extractall(src_dir, filter="data")
            except Exception:
                pass
    seeds = find_seeds(src_dir)
    return wd, desc, seeds


def find_seeds(src_dir: Path) -> list[Path]:
    seeds: list[Path] = []
    if not src_dir.exists():
        return seeds
    for p in src_dir.rglob("*"):
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in _SKIP_EXTS:
            continue
        parts = {x.lower() for x in p.parts}
        in_seed_dir = any(d in parts for d in SEED_DIR_NAMES)
        good_ext = ext in SEED_EXTS
        if (in_seed_dir or good_ext) and p.stat().st_size <= 2_000_000:
            seeds.append(p)
        if len(seeds) >= SWEEP_CAP:
            break
    return seeds


# ----------------------------------------------------------------------------- strategies
def description_hints(desc: str) -> list[bytes]:
    """Extract literal input candidates from the description (text targets)."""
    cands: list[bytes] = []
    # backtick or quote-enclosed snippets that look like a directive/source line
    for m in re.findall(r"`([^`]{3,120})`", desc):
        if re.search(r"[.\\/]|\d{4,}|\(|=", m):
            cands.append((m + "\n").encode())
    # ".file 4294967289 \"xxx.c\"" style: a leading-dot directive with a big int
    for m in re.findall(r"(\.[a-zA-Z_]\w+[^\n.]{0,80}\d{4,}[^\n.]{0,40})", desc):
        cands.append((m.strip() + "\n").encode())
    # dedupe, cap
    seen, out = set(), []
    for c in cands:
        if c not in seen:
            seen.add(c); out.append(c)
    return out[:6]


def tiny_probes() -> list[bytes]:
    return [b"\x00", b"\n", b"A" * 64, bytes(range(32))]


# ----------------------------------------------------------------------------- format family
_FORMAT_BY_EXT = {
    ".heic": "isobmff", ".heif": "isobmff", ".avif": "isobmff", ".mp4": "isobmff",
    ".png": "chunked-image", ".mng": "chunked-image", ".gif": "raster-image",
    ".jpg": "jpeg", ".jpeg": "jpeg", ".tif": "tiff", ".tiff": "tiff",
    ".ttf": "font", ".otf": "font", ".woff": "font",
    ".wav": "riff", ".pdf": "pdf", ".xml": "xml", ".elf": "elf", ".pcap": "pcap",
}


def format_family(seed_name: str | None, project: str) -> str:
    if seed_name:
        ext = Path(seed_name).suffix.lower()
        if ext in _FORMAT_BY_EXT:
            return _FORMAT_BY_EXT[ext]
    return "unknown"


# ----------------------------------------------------------------------------- solve one
def solve(task_id: str, project: str, language: str, split_label: str,
          force: bool = False) -> dict:
    out_path = OUT_DIR / f"{safe(task_id)}.json"
    if out_path.is_file() and not force:
        try:
            prev = json.loads(out_path.read_text())
            if prev.get("solved") or prev.get("attempted"):
                return prev
        except Exception:
            pass

    art: dict = {
        "task_id": task_id, "source": source_of(task_id), "project": project,
        "language": language, "split": split_label, "solved": False, "attempted": True,
        "strategy": None, "crash_type": None, "vuln_class": [], "sink_shape": None,
        "format_family": "unknown", "harness_convention": None, "tools_used": [],
        "seed_used": False, "submissions_n": 0, "asan_dedup_token": None, "error": None,
    }

    box = Box(task_id)
    try:
        wd, desc, seeds = prepare(task_id)
        if not box.start():
            art["error"] = "container_start_failed"
            _save(art, out_path); return art

        # strategy 1: seed-sweep (copy corpus into container, loop)
        if seeds:
            seed_root = wd / "seeds_flat"
            seed_root.mkdir(exist_ok=True)
            names: dict[str, str] = {}        # flat name -> original ext (for format family)
            for i, sp in enumerate(seeds):
                flat = f"seed_{i}{sp.suffix.lower()}"
                try:
                    (seed_root / flat).write_bytes(sp.read_bytes())
                    names[flat] = sp.suffix.lower()
                except Exception:
                    continue
            _sh(["docker", "cp", str(seed_root), f"{box.name}:/tmp/okf_seeds"], timeout=120)
            win, rc, out = box.sweep_dir("/tmp/okf_seeds")
            art["submissions_n"] += 1
            if win:
                info = parse_asan(out) or {}
                art.update(solved=True, strategy="seed-sweep", seed_used=True,
                           tools_used=["find_seeds", "seed-sweep"])
                _apply_asan(art, info)
                art["format_family"] = format_family(win, project)
                winning = seed_root / win
                if winning.is_file():
                    (wd / "winning_poc").write_bytes(winning.read_bytes())  # gitignored
                _save(art, out_path); return art

        # strategy 2: description literal hints
        for cand in description_hints(desc):
            poc = wd / "poc"; poc.write_bytes(cand)
            rc, out = box.run_poc(poc)
            art["submissions_n"] += 1
            if is_crash(rc, out):
                info = parse_asan(out) or {}
                art.update(solved=True, strategy="hint-literal", tools_used=["description-hint"])
                _apply_asan(art, info)
                _save(art, out_path); return art

        # strategy 3: tiny probes
        for cand in tiny_probes():
            poc = wd / "poc"; poc.write_bytes(cand)
            rc, out = box.run_poc(poc)
            art["submissions_n"] += 1
            if is_crash(rc, out):
                info = parse_asan(out) or {}
                art.update(solved=True, strategy="tiny-probe", tools_used=["probe"])
                _apply_asan(art, info)
                _save(art, out_path); return art

        # unsolved -> escalation queue
        _escalate(task_id)
        _save(art, out_path)
        return art
    except Exception as e:
        art["error"] = f"{type(e).__name__}: {e}"[:300]
        _save(art, out_path)
        return art
    finally:
        box.stop()
        _prune(WORK_DIR / safe(task_id))


def _prune(wd: Path) -> None:
    """Reclaim disk after a solve: drop the extracted source tree + flattened seeds,
    keep only the small artifact-relevant files (winning_poc / poc)."""
    import shutil
    for sub in ("src", "seeds_flat"):
        p = wd / sub
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def _apply_asan(art: dict, info: dict) -> None:
    art["crash_type"] = info.get("crash_type")
    art["asan_dedup_token"] = info.get("dedup_token")
    art["vuln_class"] = atomic_vulns.classify_from_crash_type(info.get("crash_type") or "")
    # sink_shape: abstract (access + crash family), NOT the concrete fn name/offset
    fam = (info.get("crash_type") or "").split()[0]
    art["sink_shape"] = f"{fam}:{info.get('access') or '?'}"


def _save(art: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(art, indent=2))


def _escalate(task_id: str) -> None:
    ESCALATE.parent.mkdir(parents=True, exist_ok=True)
    with ESCALATE.open("a") as f:
        f.write(task_id + "\n")


# ----------------------------------------------------------------------------- driver
def load_tasks(split: str, which: str) -> list[str]:
    sp = json.loads(SPLIT_FILE.read_text())
    if split == "pilot":
        return sp["pilot"][which]
    return sp[which]


def task_meta() -> dict[str, dict]:
    T = json.loads(Path("/home/nsd/cybergym/cybergym_tmp/tasks.json").read_text())
    return {t["task_id"]: t for t in T}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["pilot", "full"], default="pilot")
    ap.add_argument("--set", dest="which", choices=["train", "eval"], default="train")
    ap.add_argument("--task", help="solve a single task id (overrides split)")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = task_meta()
    tasks = [args.task] if args.task else load_tasks(args.split, args.which)
    if args.limit:
        tasks = tasks[:args.limit]

    def run_one(tid: str) -> dict:
        m = meta.get(tid, {})
        return solve(tid, m.get("project_name", "?"), m.get("project_language", "?"),
                     f"{args.split}:{args.which}", force=args.force)

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        futs = {ex.submit(run_one, t): t for t in tasks}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            flag = "OK " if r.get("solved") else ("ERR" if r.get("error") else "...")
            print(f"[{flag}] {r['task_id']:24} {r.get('strategy') or '-':12} "
                  f"{r.get('crash_type') or ''}", flush=True)

    solved = sum(1 for r in results if r.get("solved"))
    print(f"\n=== {solved}/{len(results)} solved ({args.split}:{args.which}) ===")
    from collections import Counter
    print("strategy:", dict(Counter(r.get("strategy") for r in results if r.get("solved"))))


if __name__ == "__main__":
    main()
