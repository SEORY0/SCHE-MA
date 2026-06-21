"""ARVO container lifecycle for Stage-2 instrumentation (depthfirst local loop).

`docker run -d n132/arvo:{id}-vul sleep infinity` gives a build env with the
vulnerable source + toolchain. Inside: edit source, `arvo compile` to rebuild,
`arvo` to run the PoC at /tmp/poc — local, no server round-trip, no rate limit.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


def _arvo_id(task_id: str) -> str | None:
    if task_id.startswith("arvo:"):
        return task_id.split(":", 1)[1]
    return None


@dataclass
class Container:
    name: str
    image: str
    task_id: str


class Instrumenter:
    def __init__(self, timeout_s: int = 600):
        self.timeout_s = timeout_s

    def start(self, task_id: str, run_id: str) -> Container | None:
        aid = _arvo_id(task_id)
        if aid is None:
            return None  # OSS-Fuzz instrumentation deferred (different run_poc interface)
        image = f"n132/arvo:{aid}-vul"
        name = f"schema_{run_id}_{aid}"
        subprocess.run(["docker", "rm", "-f", name],
                       capture_output=True, text=True)
        proc = subprocess.run(
            ["docker", "run", "-d", "--name", name, image, "sleep", "infinity"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return None
        return Container(name=name, image=image, task_id=task_id)

    def exec(self, c: Container, cmd: str) -> tuple[int, str]:
        proc = subprocess.run(
            ["docker", "exec", c.name, "bash", "-lc", cmd],
            capture_output=True, text=True, timeout=self.timeout_s,
        )
        return proc.returncode, (proc.stdout + proc.stderr)

    def compile(self, c: Container) -> tuple[int, str]:
        return self.exec(c, "arvo compile")

    def run_poc(self, c: Container, host_poc_path: str) -> tuple[int, str]:
        subprocess.run(["docker", "cp", host_poc_path, f"{c.name}:/tmp/poc"],
                       capture_output=True, text=True)
        return self.exec(c, "/bin/arvo")

    def cleanup(self, c: Container | None) -> None:
        if c is not None:
            subprocess.run(["docker", "rm", "-f", c.name],
                           capture_output=True, text=True)
