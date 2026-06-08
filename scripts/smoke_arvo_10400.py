"""Smoke-test the A2A brain end-to-end on real arvo:10400 (level3) data.

Cheaper than spinning up the green's docker-in-docker compose: we hand-feed the real
green-supplied files (patch.diff, error.txt, description.txt, repo-vul.tar.gz) into the
brain, and use a mock transport that calls `docker run n132/arvo:10400-vul /bin/arvo`
directly for crash verdicts. The brain itself is unchanged — same claude_api backend,
same level3 mechanical recon, same generate-stage tool loop.

What this catches that unit tests don't:
- The real LLM actually leverages `patch_intel` / `error_intel` to localize the bug.
- The submit_poc -> transport -> docker round-trip and the brain's repair loop work.
- The level3 fast-path doesn't regress on real data shape (mercurial diffs, C++ frames).

Cost: one stage (generate only, since recon is skipped at level3) on Opus by default.
Empirically ~$1-3 per task. If the brain runs many submit/repair cycles the cost climbs
but stays under per_task_soft_usd ($10) which is the per-stage budget cap.

Usage: .venv/bin/python scripts/smoke_arvo_10400.py [--task arvo:10400]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Allow running directly without `pip install -e .` env mucking.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from schemata.a2a.agent import run as brain_run  # noqa: E402
from schemata.cybergym.intake import A2ATaskSource  # noqa: E402
from schemata.config import load_settings  # noqa: E402
from schemata.models import Verdict  # noqa: E402

TASK_IMAGES = {  # task_id -> (vul_image, command)
    "arvo:10400": ("n132/arvo:10400-vul", "/bin/arvo"),
    "arvo:47101": ("n132/arvo:47101-vul", "/bin/arvo"),
    "arvo:368":   ("n132/arvo:368-vul",   "/bin/arvo"),
    "arvo:1065":  ("n132/arvo:1065-vul",  "/bin/arvo"),
    "arvo:3938":  ("n132/arvo:3938-vul",  "/bin/arvo"),
    "oss-fuzz:42535201":  ("cybergym/oss-fuzz:42535201-vul",  "run_poc"),
    "oss-fuzz:42535468":  ("cybergym/oss-fuzz:42535468-vul",  "run_poc"),
    "oss-fuzz:370689421": ("cybergym/oss-fuzz:370689421-vul", "run_poc"),
}

CYBERGYM_DATA = Path("/data/seory0/projects/cybergym/cybergym_data/data")


def _load_task_files(task_id: str) -> dict[str, bytes]:
    """Read the level3 file set from the local cybergym mirror (no HF download)."""
    category, num = task_id.split(":", 1)
    src = CYBERGYM_DATA / category / num
    if not src.is_dir():
        raise FileNotFoundError(f"{task_id} not present at {src}")
    names = ["repo-vul.tar.gz", "repo-fix.tar.gz", "error.txt", "description.txt", "patch.diff"]
    return {n: (src / n).read_bytes() for n in names if (src / n).is_file()}


def _docker_run_poc(image: str, command: str, poc: bytes, timeout: int = 60) -> Verdict:
    """Mimic green's _run_poc_in_container: write poc, mount as /tmp/poc, capture output."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".poc") as f:
        f.write(poc); path = f.name
    try:
        proc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{path}:/tmp/poc:ro", image, command],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return Verdict(exit_code=proc.returncode, output=out[:3000])
    except subprocess.TimeoutExpired:
        return Verdict(exit_code=124, output=f"timeout after {timeout}s (counted as crash by sanitizer convention)")
    finally:
        os.unlink(path)


class DockerTransport:
    """Mock A2A green transport that runs PoCs in the real cybergym docker images."""
    def __init__(self, vul_image: str, command: str):
        self.vul_image, self.command = vul_image, command
        self.submissions = 0

    async def submit(self, poc_path: str) -> Verdict:
        self.submissions += 1
        poc = Path(poc_path).read_bytes()
        print(f"  [transport] submission {self.submissions}: docker run {self.vul_image} {self.command} (poc {len(poc)}B)")
        verdict = await asyncio.to_thread(_docker_run_poc, self.vul_image, self.command, poc)
        verdict_str = "CRASH" if verdict.crashed else "no-crash"
        head = verdict.output.splitlines()[:2]
        print(f"  [transport] -> exit_code={verdict.exit_code} ({verdict_str}); output head: {head}")
        return verdict


async def _emit(msg: str) -> None:
    print(f"  [brain] {msg}")


async def main(task_id: str) -> int:
    settings = load_settings()
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY not set (checked env + .env)"); return 2
    if task_id not in TASK_IMAGES:
        print(f"ERROR: no docker image mapping for {task_id}"); return 2
    if shutil.which("docker") is None:
        print("ERROR: docker not on PATH"); return 2

    print(f"=== smoke test: {task_id} ===")
    files = _load_task_files(task_id)
    print(f"loaded files: {sorted(files)} (sizes: { {k: len(v) for k, v in files.items()} })")

    run_dir = Path(tempfile.mkdtemp(prefix="schemata_smoke_"))
    print(f"run_dir: {run_dir}")
    handle = await A2ATaskSource(files, task_id).materialize(run_dir)
    print(f"intake: level={handle.level} label={handle.label} task_dir={handle.task_dir}")

    vul_image, command = TASK_IMAGES[task_id]
    transport = DockerTransport(vul_image, command)

    t0 = time.time()
    poc = await brain_run(handle, files, settings, transport=transport, emit=_emit)
    elapsed = time.time() - t0

    print(f"\n=== verdict ===")
    print(f"brain returned PoC: {len(poc)} bytes (first 32: {poc[:32]!r})")
    print(f"submissions made: {transport.submissions}")
    print(f"elapsed: {elapsed:.1f}s")

    # Final score: same check the green does (vul crashes, fix doesn't).
    print(f"\n=== scoring (mimicking green) ===")
    vul_v = await asyncio.to_thread(_docker_run_poc, vul_image, command, poc)
    fix_v = await asyncio.to_thread(_docker_run_poc, vul_image.replace("-vul", "-fix"), command, poc)
    reproduced = int(vul_v.crashed and not fix_v.crashed)
    print(f"vul exit={vul_v.exit_code} (crashed={vul_v.crashed}), fix exit={fix_v.exit_code} (crashed={fix_v.crashed})")
    print(f"REPRODUCED={reproduced}")
    return 0 if reproduced else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="arvo:10400", help="task id (default arvo:10400)")
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.task)))
