from __future__ import annotations

from ..config import Settings
from .base import AgentBackend


def make_backend(name: str, settings: Settings) -> AgentBackend:
    if name == "claude_code":
        from .claude_code import ClaudeCodeBackend
        return ClaudeCodeBackend(settings)
    if name == "claude_api":
        from .claude_api import ClaudeApiBackend
        return ClaudeApiBackend(settings)
    raise ValueError(f"unknown backend: {name!r} (expected claude_code | claude_api)")
