"""Semgrep recon wrapper (used by the Claude API backend's semgrep_scan tool, M2+).

The Claude Code backend has the agent run semgrep/rg via Bash directly; this module
gives the API backend a programmatic-tool-calling path: run semgrep, then summarize
the JSON down to an attack-surface dict so only the summary reaches the model.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def semgrep_available() -> bool:
    return shutil.which("semgrep") is not None


def semgrep_summary(cwd: str | Path, config: str = "auto", max_findings: int = 40) -> dict:
    """Run semgrep and reduce to a compact attack-surface summary (or grep fallback)."""
    cwd = str(cwd)
    if semgrep_available():
        proc = subprocess.run(
            ["semgrep", "--config", config, "--json", "--quiet", cwd],
            capture_output=True, text=True, timeout=600,
        )
        try:
            data = json.loads(proc.stdout)
        except Exception:
            data = {"results": []}
        findings = []
        for r in data.get("results", [])[:max_findings]:
            findings.append({
                "check_id": r.get("check_id"),
                "path": r.get("path"),
                "line": r.get("start", {}).get("line"),
                "message": (r.get("extra", {}).get("message") or "")[:200],
            })
        return {"tool": "semgrep", "config": config, "findings": findings,
                "total": len(data.get("results", []))}
    return {"tool": "none", "findings": [],
            "note": "semgrep not installed; agent should fall back to rg/grep"}
