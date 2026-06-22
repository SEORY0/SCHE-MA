"""Execute Claude API tool_use blocks into tool_result content.

The Dispatcher is the harness side of the agent loop: it jails every path under the
task dir, truncates large outputs (programmatic-tool-calling: only a digest reaches the
model), and tracks side effects the orchestrator needs — PoC submissions, whether a
crash was found, and Stage-3 early-stop counters.
"""
from __future__ import annotations

import asyncio
import base64
import json
import subprocess
from pathlib import Path

from ...core.models import StageRequest, SubmissionRecord
from ...core.util import truncate
from ...cybergym.submit import SubmitClient
from ...pipeline.instrument import Container, Instrumenter
from ...pipeline.recon import semgrep_summary
from . import permissions

_HEAD, _TAIL = 4000, 1000  # tool-output truncation budget (chars)

_SEARCH_PROGS = {"grep", "egrep", "fgrep", "rg"}


def _bash_leading_programs(cmd: str) -> list[str]:
    try:
        return permissions.leading_programs(cmd)
    except ValueError:
        return []


def _has_unquoted_pipe(cmd: str) -> bool:
    quote: str | None = None
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        if ch == "\\" and quote != "'":
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "|":
            return True
        i += 1
    return False


def _bash_exit_guide(cmd: str, rc: int, output: str) -> str:
    progs = _bash_leading_programs(cmd)
    uses_search = any(p in _SEARCH_PROGS for p in progs)
    if rc == 0:
        if _has_unquoted_pipe(cmd) and not output.strip():
            return (
                "pipeline exit 0 can mask earlier no-match/path failures; "
                "rerun the search without the pipe if this is surprising"
            )
        return ""
    if rc == 1 and uses_search:
        return "no matches found by grep/rg; broaden the pattern or verify the searched path"
    if rc == 2 and uses_search:
        return "grep/rg syntax, quoting, or path error; inspect stderr and simplify the command"
    if rc == 126:
        return "command found but not executable; check permissions or choose another tool"
    if rc == 127:
        return "command not found; check the executable name or PATH"
    if rc > 128:
        return f"command terminated by signal {rc - 128}"
    return "non-zero exit; inspect stdout/stderr for the failing command, path, or argument"


class Dispatcher:
    def __init__(self, req: StageRequest, settings):
        self.req = req
        self.settings = settings
        self.cwd = Path(req.cwd).resolve()

        # side-effect state read by the agent loop / orchestrator
        self.submissions: list[SubmissionRecord] = []
        self.crash_found: bool = False
        self.winning_poc: str | None = None
        self.failures: int = 0          # non-crash official submissions
        self.consec_nocrash: int = 0    # consecutive non-crash submissions

        gen = settings.stage_cfg("generate")
        self.max_iters = int(gen.get("max_iters", 5))
        self.max_nocrash = int(gen.get("max_consecutive_nocrash", 3))

        self._instr: Instrumenter | None = None
        self._submit_client: SubmitClient | None = None

    # -- public -------------------------------------------------------------------

    async def execute(self, name: str, tool_input: dict) -> tuple[str, bool]:
        """Run one tool. Returns (tool_result_content, is_error)."""
        try:
            handler = getattr(self, f"_t_{name}", None)
            if handler is None:
                return f"unknown tool: {name!r}", True
            return await handler(tool_input or {})
        except ValueError as e:           # path jail / decode errors
            return str(e), True
        except subprocess.TimeoutExpired:
            return f"{name}: timed out", True
        except Exception as e:            # never let a tool crash the stage
            return truncate(f"{name} failed: {e}", 800, 200), True

    def should_early_stop(self) -> bool:
        if self.req.stage != "generate":
            return False
        return self.failures >= self.max_iters or self.consec_nocrash >= self.max_nocrash

    # -- tools --------------------------------------------------------------------

    async def _t_bash(self, a: dict) -> tuple[str, bool]:
        cmd = a.get("cmd", "")
        ok, reason = permissions.bash_allowed(self.req.permission_tier, cmd)
        if not ok:
            return reason, True
        timeout = int(a.get("timeout_s") or self.settings.instrument.get("timeout_s", 600))
        proc = await asyncio.to_thread(
            subprocess.run, ["bash", "-lc", cmd],
            cwd=str(self.cwd), capture_output=True, text=True, timeout=timeout,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        guide = _bash_exit_guide(cmd, proc.returncode, out)
        suffix = f"\n[exit {proc.returncode}]"
        if guide:
            suffix += f"\n<agent guide> {guide}"
        return truncate(out, _HEAD, _TAIL) + suffix, False

    async def _t_read_file(self, a: dict) -> tuple[str, bool]:
        p = self._resolve(a["path"])
        if not p.is_file():
            return f"no such file: {a['path']}", True
        data = await asyncio.to_thread(p.read_bytes)
        text = data.decode("utf-8", "replace")
        start, end = a.get("start_line"), a.get("end_line")
        if start or end:
            lines = text.splitlines()
            s = max(int(start or 1), 1)
            e = min(int(end or len(lines)), len(lines))
            text = "\n".join(f"{i}\t{lines[i - 1]}" for i in range(s, e + 1))  # numbered, like an editor
        mb = a.get("max_bytes")
        if mb:
            text = text[: int(mb)]
        return truncate(text, _HEAD, _TAIL), False

    async def _t_write_file(self, a: dict) -> tuple[str, bool]:
        p = self._resolve(a["path"])
        try:
            raw = base64.b64decode(a["content_b64"], validate=False)
        except Exception:
            return "content_b64 is not valid base64", True
        await asyncio.to_thread(p.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, raw)
        return f"wrote {len(raw)} bytes to {a['path']}", False

    async def _t_grep(self, a: dict) -> tuple[str, bool]:
        target = self._resolve(a["path"]) if a.get("path") else self.cwd
        proc = await asyncio.to_thread(
            subprocess.run, ["grep", "-rnI", "--", a["pattern"], str(target)],
            capture_output=True, text=True, timeout=120,
        )
        out = proc.stdout or ("no matches" if proc.returncode == 1 else proc.stderr)
        return truncate(out, _HEAD, _TAIL), False

    async def _t_glob(self, a: dict) -> tuple[str, bool]:
        def _scan() -> list[str]:
            return sorted(
                str(x.relative_to(self.cwd))
                for x in self.cwd.glob(a["pattern"]) if x.is_file()
            )
        matches = await asyncio.to_thread(_scan)
        return ("\n".join(matches[:500]) or "no matches"), False

    async def _t_semgrep_scan(self, a: dict) -> tuple[str, bool]:
        summary = await asyncio.to_thread(
            semgrep_summary, str(self.cwd), a.get("config", "auto"))
        return truncate(json.dumps(summary, ensure_ascii=False), 6000, 500), False

    async def _t_arvo_compile(self, a: dict) -> tuple[str, bool]:
        c = self._container()
        if c is None:
            return "no instrument container is attached to this task", True
        rc, out = await asyncio.to_thread(self._instrumenter().compile, c)
        return truncate(out, _HEAD, _TAIL) + f"\n[arvo compile exit {rc}]", False

    async def _t_arvo_run(self, a: dict) -> tuple[str, bool]:
        c = self._container()
        if c is None:
            return "no instrument container is attached to this task", True
        poc = self._resolve(a["poc_path"])
        if not poc.is_file():
            return f"no such poc file: {a['poc_path']}", True
        rc, out = await asyncio.to_thread(self._instrumenter().run_poc, c, str(poc))
        note = "  (local validation only — call submit_poc to make it official)"
        return truncate(out, _HEAD, _TAIL) + f"\n[arvo exit {rc}]{note}", False

    async def _t_submit_poc(self, a: dict) -> tuple[str, bool]:
        poc = self._resolve(a["poc_path"])
        if not poc.is_file():
            return f"no such poc file: {a['poc_path']}", True
        if self.req.submit_fn is not None:          # A2A mode: green test_vulnerable round-trip
            verdict = await self.req.submit_fn(str(poc))
            if verdict is None:
                return "the test transport returned no verdict (green did not reply in time)", True
        else:                                       # local mode: SubmitClient -> /submit-vul
            client = self._submit()
            if client is None:
                return "submission is not configured (missing masked id / agent id / checksum)", True
            verdict = await asyncio.to_thread(client.submit, str(poc))
        return self._record_verdict(poc, verdict)

    def _record_verdict(self, poc, verdict) -> tuple[str, bool]:
        rel = self._rel(poc)
        self.submissions.append(SubmissionRecord(
            poc_path=rel,
            poc_sha256=SubmitClient.sha256(poc),
            exit_code=verdict.exit_code,
            output_excerpt=truncate(verdict.output, 1500, 500),
            poc_id=verdict.poc_id,
        ))
        if verdict.crashed:
            self.crash_found = True
            self.winning_poc = rel
            self.consec_nocrash = 0
        else:
            self.failures += 1
            self.consec_nocrash += 1
        return json.dumps({
            "exit_code": verdict.exit_code,
            "crashed": verdict.crashed,
            "poc_id": verdict.poc_id,
            "output": truncate(verdict.output, 1500, 500),
        }, ensure_ascii=False), False

    async def _t_mcp_code_query(self, a: dict) -> tuple[str, bool]:
        return "MCP code index is not enabled for this task (M4).", False

    # -- helpers ------------------------------------------------------------------

    def _resolve(self, path: str) -> Path:
        p = (self.cwd / path).resolve()
        if p != self.cwd and not p.is_relative_to(self.cwd):
            raise ValueError(f"path escapes the task directory: {path}")
        return p

    def _rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.cwd))
        except ValueError:
            return str(p)

    def _instrumenter(self) -> Instrumenter:
        if self._instr is None:
            self._instr = Instrumenter(timeout_s=int(self.settings.instrument.get("timeout_s", 600)))
        return self._instr

    def _container(self) -> Container | None:
        if not self.req.instrument_container:
            return None
        return Container(name=self.req.instrument_container, image="", task_id=self.req.task_id_masked or "")

    def _submit(self) -> SubmitClient | None:
        if self._submit_client is not None:
            return self._submit_client
        r = self.req
        if not (r.server_url and r.task_id_masked and r.agent_id and r.checksum):
            return None
        self._submit_client = SubmitClient(
            server_url=r.server_url,
            masked_id=r.task_id_masked,
            agent_id=r.agent_id,
            checksum=r.checksum,
            require_flag=self.settings.require_flag,
            rate_limit_max=self.settings.rate_limit_max,
            rate_limit_window_s=self.settings.rate_limit_window_s,
        )
        return self._submit_client
