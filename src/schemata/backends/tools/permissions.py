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

from ...models import StageRequest
from . import definitions as d

# Leading programs permitted for bash in the read_only (Recon) tier.
READONLY_BASH_ALLOW = {
    "tar", "grep", "egrep", "fgrep", "rg", "find", "cat", "file", "xxd", "hexdump",
    "od", "nm", "objdump", "readelf", "strings", "ls", "head", "tail", "wc", "sort",
    "uniq", "cut", "tr", "sed", "awk", "semgrep", "ctags", "python3", "sha256sum",
    "md5sum", "basename", "dirname", "echo", "true", "stat", "realpath", "which",
}

# Split a command into pipeline/sequence segments to validate each leading program.
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[|;&]")
_ENV_ASSIGN = re.compile(r"^\w+=\S*$")


def tools_for(req: StageRequest) -> list[dict]:
    """Return the Messages-API tool definitions this stage+tier may use."""
    tier = req.permission_tier
    names = ["read_file", "grep", "glob", "bash", "semgrep_scan"]
    if tier in ("write", "full"):
        names.append("write_file")
    if req.instrument_container:
        names += ["arvo_compile", "arvo_run"]
    if tier == "full":
        names.append("submit_poc")
    if req.mcp_endpoint:
        names.append("mcp_code_query")
    # de-dupe while preserving order
    seen: dict[str, None] = {}
    for n in names:
        seen.setdefault(n, None)
    return [d.tool(n) for n in seen]


def bash_allowed(tier: str, cmd: str) -> tuple[bool, str]:
    """Gate a bash command. Only the read_only tier is restricted."""
    if tier != "read_only":
        return True, ""
    if ">" in cmd:
        return False, "output redirection is not allowed in the Recon (read_only) stage"
    for seg in _SEGMENT_SPLIT.split(cmd):
        toks = seg.split()
        # skip leading VAR=val env assignments
        i = 0
        while i < len(toks) and _ENV_ASSIGN.match(toks[i]):
            i += 1
        if i >= len(toks):
            continue
        prog = toks[i].split("/")[-1]
        if prog not in READONLY_BASH_ALLOW:
            return False, (
                f"'{prog}' is not allowed in the Recon (read_only) stage; "
                f"use one of: {', '.join(sorted(READONLY_BASH_ALLOW))}"
            )
    return True, ""
