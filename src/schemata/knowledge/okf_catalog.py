"""OKF knowledge catalog — distilled, task-agnostic PoC knowledge as an OKF bundle.

The bundle lives at ``skills/knowledge/okf/`` and follows Google's Open Knowledge Format
(SPEC v0.1): a directory of markdown files with YAML frontmatter, a required ``type`` field,
``index.md`` for progressive disclosure, ``log.md`` for history, and ``# Schema / # Examples
/ # Citations`` body sections. Each concept carries only ABSTRACT, generalizable patterns
distilled from solving many CyberGym tasks — never per-task answers (no task ids, no concrete
offsets). This module mirrors ``atomic_vulns.retrieve`` so it plugs into the same generate-stage
injection point in ``prompt_loader.build_request``.

Retrieval is key-based (no embeddings): a concept matches when its id (file stem), ``tags``, or
``match_keys`` intersect the signal set built from the task's vuln classes, input format,
harness convention, and task properties — exactly the vocabularies the pipeline already computes.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from ..core.config import SKILLS_DIR
from . import atomic_vulns
from .skill_registry import _FRONTMATTER_RE, _parse_frontmatter

_BUNDLE = SKILLS_DIR / "knowledge" / "okf"
_RESERVED = {"index", "log"}


class Concept:
    __slots__ = ("cid", "type", "title", "keys", "body")

    def __init__(self, cid: str, fm: dict, body: str):
        self.cid = cid
        self.type = fm.get("type", "")
        self.title = fm.get("title", cid.rsplit("/", 1)[-1])
        stem = cid.rsplit("/", 1)[-1]
        keys = {stem.lower()}
        for field in ("tags", "match_keys"):
            v = fm.get(field)
            if isinstance(v, list):
                keys |= {str(x).lower() for x in v}
            elif isinstance(v, str):
                keys.add(v.lower())
        self.keys = keys
        self.body = body


def _read_body(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    m = _FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


@lru_cache(maxsize=1)
def _load() -> list[Concept]:
    concepts: list[Concept] = []
    if not _BUNDLE.exists():
        return concepts
    for md in sorted(_BUNDLE.rglob("*.md")):
        cid = md.relative_to(_BUNDLE).with_suffix("").as_posix()
        if md.stem in _RESERVED:
            continue
        fm = _parse_frontmatter(md)
        if not fm or not fm.get("type"):     # OKF conformance: non-empty type required
            continue
        body = _read_body(md)
        if body.strip():
            concepts.append(Concept(cid, fm, body))
    return concepts


def _format_tokens(input_format: str | None) -> set[str]:
    if not input_format:
        return set()
    s = input_format.lower()
    return {s} | set(re.split(r"[^a-z0-9]+", s)) - {""}


def signal_set(vuln_classes, input_format, harness_convention, task_properties) -> set[str]:
    sig: set[str] = set()
    for c in vuln_classes or []:
        sig.add(atomic_vulns._norm(str(c)))
    sig |= _format_tokens(input_format)
    if harness_convention:
        sig.add(str(harness_convention).lower())
    for p in task_properties or []:
        sig.add(str(p).lower())
    return {s for s in sig if s}


def retrieve(vuln_classes=None, input_format=None, harness_convention=None,
             task_properties=None) -> str:
    """Render `<okf_examples>` from concepts whose keys intersect the task signal set.

    Progressive disclosure: only matched concept bodies are emitted (not the whole bundle).
    Returns "" when nothing matches, so the prompt token renders empty.
    """
    sig = signal_set(vuln_classes, input_format, harness_convention, task_properties)
    if not sig:
        return ""
    matched = [c for c in _load() if c.keys & sig]
    if not matched:
        return ""
    # stable order: vuln-class first, then format-family, harness, strategy, then by id
    order = {"vuln-class": 0, "format-family": 1, "harness-convention": 2, "strategy": 3}
    matched.sort(key=lambda c: (order.get(c.type, 9), c.cid))
    out = ["<okf_examples>",
           "Distilled, task-agnostic PoC knowledge matched to this task (vuln class / format / "
           "harness / properties). These are GENERALIZED patterns from prior solves — instantiate "
           "them against THIS target's real bytes; never copy a literal value."]
    for c in matched:
        out.append(f"\n## {c.title}  ·  _{c.type}_\n{c.body.strip()}")
    out.append("</okf_examples>")
    return "\n".join(out)


def reload() -> None:
    """Drop the cache (used by tests / after the distiller rewrites the bundle)."""
    _load.cache_clear()
