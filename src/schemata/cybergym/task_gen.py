"""Generate a CyberGym task dir by shelling out to the cybergym submodule.

We never reimplement gen_task; we call `python -m cybergym.task.gen_task` with the
interpreter that has `cybergym` installed (settings.cybergym_python).
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings


@dataclass
class TaskHandle:
    task_id: str
    task_dir: Path
    masked_id: str
    agent_id: str
    checksum: str
    server_url: str


_META_RE = re.compile(
    r'"task_id":\s*"(?P<task_id>[^"]+)".*?'
    r'"agent_id":\s*"(?P<agent_id>[^"]+)".*?'
    r'"checksum":\s*"(?P<checksum>[^"]+)"',
    re.DOTALL,
)
_SERVER_RE = re.compile(r"curl\s+-X\s+POST\s+(?P<url>\S+)/submit-vul")


def _parse_submit_sh(submit_sh: Path) -> dict:
    text = submit_sh.read_text()
    m = _META_RE.search(text)
    s = _SERVER_RE.search(text)
    if not m:
        raise RuntimeError(f"could not parse metadata from {submit_sh}")
    out = m.groupdict()
    out["server_url"] = s.group("url") if s else ""
    return out


def maybe_rewrite_submit_host(submit_sh: Path, enable: bool) -> None:
    """When the agent runs inside a container, the submit server is on the docker host."""
    if not enable:
        return
    text = submit_sh.read_text()
    new = text.replace("127.0.0.1", "host.docker.internal").replace("localhost", "host.docker.internal")
    if new != text:
        submit_sh.write_text(new)


def gen_task(
    settings: Settings,
    task_id: str,
    out_dir: Path,
    difficulty: str = "level1",
) -> TaskHandle:
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        settings.cybergym_python, "-m", "cybergym.task.gen_task",
        "--task-id", task_id,
        "--out-dir", str(out_dir),
        "--data-dir", settings.data_dir,
        "--server", settings.server_url,
        "--difficulty", difficulty,
    ]
    if settings.mask_map:
        cmd += ["--mask-map", settings.mask_map]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gen_task failed ({proc.returncode}):\nCMD: {' '.join(cmd)}\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )

    submit_sh = out_dir / "submit.sh"
    if not submit_sh.exists():
        raise RuntimeError(f"gen_task produced no submit.sh in {out_dir}")

    maybe_rewrite_submit_host(submit_sh, settings.rewrite_submit_host)
    info = _parse_submit_sh(submit_sh)
    return TaskHandle(
        task_id=task_id,
        task_dir=out_dir,
        masked_id=info["task_id"],
        agent_id=info["agent_id"],
        checksum=info["checksum"],
        server_url=info["server_url"] or settings.server_url,
    )
