from __future__ import annotations

from ..core.config import Settings
from .base import AgentBackend

# How a backend submits PoCs in the generate stage: the API backends expose a `submit_poc`
# tool; the claude_code CLI backend runs `bash submit.sh`. routed_api dispatches only between
# API sub-backends, so its submit surface is uniformly tool-based. This is the capability the
# generate prompt keys off of — NOT the backend name directly.
_SUBMIT_MODE = {
    "claude_code": "script",
    "claude_api": "tool",
    "openai_api": "tool",
    "routed_api": "tool",
}


def submit_mode(backend_name: str) -> str:
    return _SUBMIT_MODE.get(backend_name, "tool")


def make_backend(name: str, settings: Settings) -> AgentBackend:
    if name == "claude_code":
        from .claude_code import ClaudeCodeBackend
        return ClaudeCodeBackend(settings)
    if name == "claude_api":
        from .claude_api import ClaudeApiBackend
        return ClaudeApiBackend(settings)
    if name == "openai_api":
        from .openai_api import OpenAiApiBackend
        return OpenAiApiBackend(settings)
    if name == "routed_api":
        from .routing import RoutingBackend
        return RoutingBackend(settings)
    raise ValueError(
        f"unknown backend: {name!r} "
        "(expected claude_code | claude_api | openai_api | routed_api)"
    )
