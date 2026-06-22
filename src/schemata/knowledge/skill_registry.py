"""Skill registry: discovers, indexes, and selects .md skill files.

Implements progressive disclosure — metadata is always available (cheap),
skill body is loaded on demand (expensive).  Tool-skills carry trigger
conditions and tool requirements so the selector can activate them based
on failure state and container availability.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Literal

from ..core.config import SKILLS_DIR

SkillType = Literal["tool", "agent", "stage", "shared", "knowledge"]

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    result: dict = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            result[key] = items
        elif value.lower() in ("true", "false"):
            result[key] = value.lower() == "true"
        elif value.lower() == "null" or value == "~":
            result[key] = None
        else:
            result[key] = value.strip("'\"")
    return result if result else None


def _to_tuple(val) -> tuple[str, ...]:
    if isinstance(val, list):
        return tuple(str(v) for v in val)
    if isinstance(val, str):
        return (val,)
    return ()


@dataclass(frozen=True)
class SkillMeta:
    """Metadata parsed from a skill file's YAML frontmatter."""
    name: str
    description: str
    type: SkillType
    path: Path
    availability: str = "always"
    required_package: str | None = None
    requires_tools: tuple[str, ...] = ()
    triggers: tuple[str, ...] = ()
    stage: str | None = None
    model: str | None = None
    tools: tuple[str, ...] = ()
    permission_tier: str | None = None
    outputs: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    token_cost: str = "medium"


def _build_meta(fm: dict, path: Path) -> SkillMeta:
    return SkillMeta(
        name=fm.get("name", path.stem),
        description=fm.get("description", ""),
        type=fm.get("type", "tool"),
        path=path,
        availability=fm.get("availability", "always"),
        required_package=fm.get("required_package"),
        requires_tools=_to_tuple(fm.get("requires_tools")),
        triggers=_to_tuple(fm.get("triggers")),
        stage=fm.get("stage"),
        model=fm.get("model"),
        tools=_to_tuple(fm.get("tools")),
        permission_tier=fm.get("permission_tier"),
        outputs=_to_tuple(fm.get("outputs")),
        skills=_to_tuple(fm.get("skills")),
        token_cost=fm.get("token_cost", "medium"),
    )


class SkillRegistry:
    """Discovers and indexes all .md skill files under SKILLS_DIR."""

    def __init__(self, skills_dir: Path | None = None):
        self._dir = skills_dir or SKILLS_DIR
        self._skills: dict[str, SkillMeta] = {}
        self._scan()

    def _scan(self) -> None:
        for md in sorted(self._dir.rglob("*.md")):
            fm = _parse_frontmatter(md)
            if fm and "name" in fm:
                meta = _build_meta(fm, md)
                self._skills[meta.name] = meta

    def get(self, name: str) -> SkillMeta | None:
        return self._skills.get(name)

    def all(self) -> list[SkillMeta]:
        return list(self._skills.values())

    def by_type(self, skill_type: SkillType) -> list[SkillMeta]:
        return [s for s in self._skills.values() if s.type == skill_type]

    def tools(self) -> list[SkillMeta]:
        return self.by_type("tool")

    def agents(self) -> list[SkillMeta]:
        return self.by_type("agent")

    def menu(self) -> str:
        """One-line-per-skill summary for LLM context (cheap metadata only)."""
        lines: list[str] = []
        for s in self._skills.values():
            avail = f" [requires: {s.availability}]" if s.availability != "always" else ""
            lines.append(f"- **{s.name}**: {s.description}{avail}")
        return "\n".join(lines)

    def body(self, name: str) -> str:
        """Load the full skill body (strips frontmatter). On-demand only."""
        meta = self._skills.get(name)
        if not meta:
            return ""
        try:
            text = meta.path.read_text(encoding="utf-8")
        except OSError:
            return ""
        m = _FRONTMATTER_RE.match(text)
        return text[m.end():] if m else text


class SkillSelector:
    """Select tool-skills based on stage, failure state, and container availability."""

    def __init__(self, registry: SkillRegistry):
        self._reg = registry

    def select_tool_skills(
        self,
        *,
        has_instrument: bool = False,
        failure_classes: list[str] | None = None,
        stage: str = "generate",
    ) -> list[SkillMeta]:
        if stage != "generate":
            return []
        selected: list[SkillMeta] = []
        for skill in self._reg.tools():
            if not self._available(skill):
                continue
            if skill.availability == "instrument_container" and not has_instrument:
                continue
            # Skill passes availability checks — include it.
            # Triggers are metadata for future progressive disclosure;
            # for now all available skills are loaded.
            selected.append(skill)
        return selected

    def select_agent(self, stage: str) -> SkillMeta | None:
        for agent in self._reg.agents():
            if agent.stage == stage:
                return agent
        return None

    def _available(self, skill: SkillMeta) -> bool:
        if not skill.required_package:
            return True
        try:
            __import__(skill.required_package)
            return True
        except ImportError:
            return False

    def assemble_tool_advice(
        self,
        *,
        has_instrument: bool = False,
        failure_classes: list[str] | None = None,
    ) -> str:
        skills = self.select_tool_skills(
            has_instrument=has_instrument,
            failure_classes=failure_classes,
        )
        parts: list[str] = []
        for s in skills:
            body = self._reg.body(s.name)
            if body.strip():
                parts.append(body)
        return "\n\n".join(parts)


@lru_cache(maxsize=1)
def _default_registry() -> SkillRegistry:
    return SkillRegistry()


def get_registry() -> SkillRegistry:
    return _default_registry()


def get_selector() -> SkillSelector:
    return SkillSelector(get_registry())
