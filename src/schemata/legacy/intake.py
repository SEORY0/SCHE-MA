"""Seam 1 — Task intake. Materializes a task into a working directory.

Two implementations behind one `TaskSource`:
- LocalTaskSource: dev mode — wraps cybergym.task.gen_task (local data + submit.sh).
- A2ATaskSource: AgentBeats mode — writes the files the green agent sent over A2A.

Both yield a uniform TaskHandle the orchestrator consumes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

# CyberGym level -> required files (mirrors cybergym/cybergym-green).
LEVEL_FILES: dict[str, set[str]] = {
    "level0": {"repo-vul.tar.gz"},
    "level1": {"repo-vul.tar.gz", "description.txt"},
    "level2": {"repo-vul.tar.gz", "description.txt", "error.txt"},
    "level3": {"repo-vul.tar.gz", "repo-fix.tar.gz", "error.txt", "description.txt", "patch.diff"},
}

_LABEL_RE = re.compile(r"(?:arvo|oss-fuzz):\d+")


def infer_level(files) -> str:
    """Infer the CyberGym level from which attachments are present (green sends per-level)."""
    names = set(files)
    if "patch.diff" in names and "repo-fix.tar.gz" in names:
        return "level3"
    if "error.txt" in names and "description.txt" in names:
        return "level2"
    if "description.txt" in names:
        return "level1"
    return "level0"


def infer_label(text: str, files) -> str:
    m = _LABEL_RE.search(text or "")
    if m:
        return m.group(0)
    for name in files:
        m = _LABEL_RE.search(name)
        if m:
            return m.group(0)
    return "unknown"


@dataclass
class TaskHandle:
    task_dir: Path
    level: str = "level1"
    label: str = "unknown"
    # local (submit.sh) mode only — None in AgentBeats mode
    masked_id: Optional[str] = None
    agent_id: Optional[str] = None
    checksum: Optional[str] = None
    server_url: Optional[str] = None


class TaskSource(Protocol):
    async def materialize(self, run_dir: Path) -> TaskHandle: ...


class A2ATaskSource:
    """AgentBeats mode: write the green-supplied file bytes into the task dir."""

    def __init__(self, files: dict[str, bytes], text: str = ""):
        self.files = files
        self.text = text

    async def materialize(self, run_dir: Path) -> TaskHandle:
        task_dir = run_dir / "task"
        task_dir.mkdir(parents=True, exist_ok=True)
        for name, data in self.files.items():
            (task_dir / name).write_bytes(data)
        return TaskHandle(
            task_dir=task_dir,
            level=infer_level(self.files),
            label=infer_label(self.text, self.files),
        )


class LocalTaskSource:
    """Dev mode: generate the task locally via the cybergym submodule."""

    def __init__(self, settings, task_id: str, difficulty: str = "level1"):
        self.settings = settings
        self.task_id = task_id
        self.difficulty = difficulty

    async def materialize(self, run_dir: Path) -> TaskHandle:
        from .task_gen import gen_task  # local import to avoid hard dep in A2A mode
        h = gen_task(self.settings, self.task_id, run_dir / "task", self.difficulty)
        return TaskHandle(
            task_dir=h.task_dir, level=self.difficulty, label=self.task_id,
            masked_id=h.masked_id, agent_id=h.agent_id,
            checksum=h.checksum, server_url=h.server_url,
        )
