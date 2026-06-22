"""Thin loader for analysis tool skill files under ``skills/tools/``.

Reads ``.md`` skill files and renders them into the stage prompt via the
``{{analysis_tools_advice}}`` token.  All usage guidance lives in the
markdown files — this module only handles availability gating and assembly.
"""
from __future__ import annotations

from functools import lru_cache

from ..core.config import SKILLS_DIR

_TOOLS_DIR = SKILLS_DIR / "tools"

_ALWAYS = [
    "construct_format_builder.md",
    "pwntools_binary.md",
]
_INSTRUMENT = [
    "gdb_dynamic_analysis.md",
]
_STATIC = [
    "angr_reachability.md",
]

_PKG_FOR_SKILL = {
    "construct_format_builder.md": "construct",
    "pwntools_binary.md": "pwn",
    "angr_reachability.md": "angr",
}


def _available(filename: str) -> bool:
    pkg = _PKG_FOR_SKILL.get(filename)
    if pkg is None:
        return True
    try:
        __import__(pkg)
        return True
    except ImportError:
        return False


@lru_cache(maxsize=8)
def _load(filename: str) -> str:
    p = _TOOLS_DIR / filename
    return p.read_text(encoding="utf-8") if p.is_file() else ""


def advice(has_instrument: bool = False) -> str:
    """Load and assemble available tool skill files for the generate prompt."""
    parts: list[str] = []
    for f in _ALWAYS:
        if _available(f):
            parts.append(_load(f))
    if has_instrument:
        for f in _INSTRUMENT:
            parts.append(_load(f))
    for f in _STATIC:
        if _available(f):
            parts.append(_load(f))
    return "\n\n".join(p for p in parts if p)
