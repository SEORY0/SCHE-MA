"""ARVO container lifecycle for Stage-2 instrumentation (depthfirst local loop).

`docker run -d n132/arvo:{id}-vul sleep infinity` gives a build env with the
vulnerable source + toolchain. Inside: edit source, `arvo compile` to rebuild,
`arvo` to run the PoC at /tmp/poc — local, no server round-trip, no rate limit.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass


def _sh_quote(s: str) -> str:
    """Shell-quote a string for use inside a docker exec command."""
    return shlex.quote(s)


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

    def _ensure_gdb(self, c: Container) -> None:
        rc, _ = self.exec(c, "which gdb")
        if rc != 0:
            self.exec(c, "apt-get update -qq && apt-get install -y -qq gdb 2>/dev/null")

    def _find_binary(self, c: Container) -> str:
        rc, out = self.exec(c, "grep -oP '(?<=exec )\\S+' /bin/arvo 2>/dev/null")
        if rc == 0 and out.strip():
            return out.strip().split()[0]
        rc, out = self.exec(c, "find /out -type f -executable 2>/dev/null | head -1")
        if rc == 0 and out.strip():
            return out.strip()
        return "/out/target"

    def run_gdb(self, c: Container, host_poc_path: str, commands: str) -> tuple[int, str]:
        self._ensure_gdb(c)
        subprocess.run(["docker", "cp", host_poc_path, f"{c.name}:/tmp/poc"],
                       capture_output=True, text=True)
        binary = self._find_binary(c)
        gdb_lines = ["set pagination off", "set confirm off"]
        for line in commands.splitlines():
            line = line.strip()
            if line:
                gdb_lines.append(line)
        gdb_script = "\n".join(gdb_lines)
        cmd = f'printf %s {_sh_quote(gdb_script)} > /tmp/_gdb.cmd && gdb -batch -x /tmp/_gdb.cmd --args {binary} /tmp/poc 2>&1'
        return self.exec(c, cmd)

    def check_coverage(self, c: Container, host_poc_path: str, functions: list[str]) -> tuple[int, str]:
        self._ensure_gdb(c)
        subprocess.run(["docker", "cp", host_poc_path, f"{c.name}:/tmp/poc"],
                       capture_output=True, text=True)
        binary = self._find_binary(c)
        bp_cmds = "\n".join(f"break {fn}" for fn in functions)
        script = f"set pagination off\nset confirm off\n{bp_cmds}\nrun\ninfo breakpoints\nquit"
        cmd = f'printf %s {_sh_quote(script)} > /tmp/_gdb.cmd && gdb -batch -x /tmp/_gdb.cmd --args {binary} /tmp/poc 2>&1'
        return self.exec(c, cmd)

    def cleanup(self, c: Container | None) -> None:
        if c is not None:
            subprocess.run(["docker", "rm", "-f", c.name],
                           capture_output=True, text=True)
