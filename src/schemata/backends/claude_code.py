"""Claude Code backend — runs each stage as a headless `claude -p` agentic session.

Uses the installed `claude` CLI (v2.1+) with --output-format json, which runs the
full agentic loop (Bash/Read/Grep/Glob/Write) in the task dir and returns a JSON
envelope with the final text, usage, and total_cost_usd. The agent runs
`bash submit.sh <poc>` itself for Stage 3; the orchestrator independently
re-confirms the winning PoC via SubmitClient.
"""
from __future__ import annotations

import asyncio
import json
import shutil

from ..core.models import Artifacts, StageRequest, StageResult, Usage
from ..core.util import extract_last_json, truncate
from .base import AgentBackend, alias_of, cost_of, model_id_of


class ClaudeCodeBackend(AgentBackend):
    name = "claude_code"

    def __init__(self, settings):
        super().__init__(settings)
        self.claude_bin = shutil.which("claude") or "claude"

    def _build_cmd(self, req: StageRequest) -> list[str]:
        cc = self.settings.claude_code
        model_id = model_id_of(alias_of(req.model))
        tools = ",".join(req.allowed_tools)
        cmd = [
            self.claude_bin,
            "-p", req.kickoff,
            "--output-format", "json",
            "--append-system-prompt", req.system_prompt,
            "--allowedTools", tools,
            "--model", model_id,
            "--permission-mode", cc.get("permission_mode", "acceptEdits"),
            "--add-dir", str(req.cwd),
            "--no-session-persistence",
        ]
        if req.max_budget_usd:
            cmd += ["--max-budget-usd", f"{req.max_budget_usd:.2f}"]
        if cc.get("exclude_dynamic_system_prompt", False):
            cmd += ["--exclude-dynamic-system-prompt-sections"]
        return cmd

    @staticmethod
    def _parse_usage(envelope: dict, model: str) -> Usage:
        u = envelope.get("usage", {}) or {}
        return Usage(
            model=model,
            input_tokens=int(u.get("input_tokens", 0)),
            output_tokens=int(u.get("output_tokens", 0)),
            cache_read_tokens=int(u.get("cache_read_input_tokens", 0)),
            cache_write_tokens=int(u.get("cache_creation_input_tokens", 0)),
        )

    async def run_stage(self, req: StageRequest) -> StageResult:
        cmd = self._build_cmd(req)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(req.cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")

        if proc.returncode != 0 and not stdout.strip():
            return StageResult(
                stage=req.stage,
                stop_reason="error",
                error=f"claude exited {proc.returncode}: {truncate(stderr, 1500, 500)}",
            )

        try:
            envelope = json.loads(stdout)
        except Exception:
            return StageResult(
                stage=req.stage,
                stop_reason="error",
                error=f"could not parse claude JSON envelope: {truncate(stdout, 1500, 500)}",
            )

        result_text = envelope.get("result", "") or ""
        usage = self._parse_usage(envelope, req.model)
        cost = float(envelope.get("total_cost_usd") or cost_of(usage, req.model))
        structured = extract_last_json(result_text)

        artifacts = Artifacts()
        if req.stage == "generate":
            wp = structured.get("winning_poc_path")
            if wp:
                artifacts.poc_path = wp

        stop = "completed"
        if envelope.get("is_error"):
            stop = "error"

        return StageResult(
            stage=req.stage,
            structured_output=structured,
            raw_transcript_tail=truncate(result_text, 3000, 1000),
            usage=usage,
            cost_usd=cost,
            artifacts=artifacts,
            stop_reason=stop,
        )
