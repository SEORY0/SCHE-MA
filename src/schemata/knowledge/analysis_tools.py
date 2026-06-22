"""Thin loader for analysis tool skill files under ``skills/tools/``.

Uses the :class:`SkillRegistry` for progressive disclosure: metadata is
always indexed, bodies are loaded on demand.  The ``advice()`` function
is the public entry point called by ``prompt_loader.build_request()``.
"""
from __future__ import annotations

from .skill_registry import get_selector


def advice(
    has_instrument: bool = False,
    failure_classes: list[str] | None = None,
) -> str:
    """Load and assemble available tool skill files for the generate prompt.

    Uses the SkillRegistry to select skills based on availability and
    trigger conditions, replacing the old hardcoded file lists.
    """
    return get_selector().assemble_tool_advice(
        has_instrument=has_instrument,
        failure_classes=failure_classes,
    )
