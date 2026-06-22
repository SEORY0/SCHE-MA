"""Permission tiers for the Claude API backend (claw-code mirror).

The tool surface a stage gets is decided by (permission_tier, instrument_container,
mcp_endpoint) — NOT by the Claude-Code tool names in req.allowed_tools. Tiers:

  read_only (Stage 1 Recon)   : read/inspect + bash(allow-list) + semgrep_scan
  write     (Stage 2 Analyze)  : + write_file + arvo_compile/arvo_run (if container)
  full      (Stage 3 Generate) : + submit_poc

read_only bash is restricted to an allow-list of inspection programs; write/full bash
is unrestricted (the harness runs on a trusted machine inside the task dir).
"""
from __future__ import annotations

import re
import shlex

from ...core.models import StageRequest
from . import definitions as d

# Leading programs permitted for bash in the read_only (Recon) tier.
READONLY_BASH_ALLOW = {
    "tar", "grep", "egrep", "fgrep", "rg", "find", "cat", "file", "xxd", "hexdump",
    "od", "nm", "objdump", "readelf", "strings", "ls", "head", "tail", "wc", "sort",
    "uniq", "cut", "tr", "sed", "awk", "semgrep", "ctags", "python3", "sha256sum",
    "md5sum", "basename", "dirname", "echo", "true", "stat", "realpath", "which",
}

_ENV_ASSIGN = re.compile(r"^\w+=\S*$")


def tools_for(req: StageRequest) -> list[dict]:
    """Return the Messages-API tool definitions this stage+tier may use."""
    tier = req.permission_tier
    names = ["read_file", "grep", "glob", "bash", "semgrep_scan"]
    if tier in ("write", "full"):
        names.append("write_file")
    if req.instrument_container:
        names += ["arvo_compile", "arvo_run", "gdb_script", "coverage_check"]
    if tier == "full":
        names.append("submit_poc")
    if req.mcp_endpoint:
        names.append("mcp_code_query")
    # de-dupe while preserving order
    seen: dict[str, None] = {}
    for n in names:
        seen.setdefault(n, None)
    return [d.tool(n) for n in seen]


def _split_shell_segments(cmd: str) -> list[str]:
    """Split on shell control operators while respecting quotes and backslash escapes."""
    segments: list[str] = []
    start = 0
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
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            seg = cmd[start:i].strip()
            if seg:
                segments.append(seg)
            i += 2
            start = i
            continue
        if ch in "|;&":
            seg = cmd[start:i].strip()
            if seg:
                segments.append(seg)
            i += 1
            start = i
            continue
        i += 1
    tail = cmd[start:].strip()
    if tail:
        segments.append(tail)
    return segments


def _has_unquoted_redirection(cmd: str) -> bool:
    """Return true for shell redirection outside quotes/escapes."""
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
        if ch in "<>":
            return True
        i += 1
    return False


def leading_programs(cmd: str) -> list[str]:
    """Return leading executables for each shell segment."""
    progs: list[str] = []
    for seg in _split_shell_segments(cmd):
        toks = shlex.split(seg, posix=True)
        i = 0
        while i < len(toks) and _ENV_ASSIGN.match(toks[i]):
            i += 1
        if i < len(toks):
            progs.append(toks[i].split("/")[-1])
    return progs


def bash_allowed(tier: str, cmd: str) -> tuple[bool, str]:
    """Gate a bash command. Only the read_only tier is restricted."""
    if tier != "read_only":
        return True, ""
    if _has_unquoted_redirection(cmd):
        return False, "input/output redirection is not allowed in the Recon (read_only) stage"
    try:
        progs = leading_programs(cmd)
    except ValueError as e:
        return False, f"could not parse bash command in the Recon (read_only) stage: {e}"
    for prog in progs:
        if prog not in READONLY_BASH_ALLOW:
            return False, (
                f"'{prog}' is not allowed in the Recon (read_only) stage; "
                f"use one of: {', '.join(sorted(READONLY_BASH_ALLOW))}"
            )
    return True, ""
