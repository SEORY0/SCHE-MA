"""Small helpers: extract the final JSON block from agent output; truncate output."""
from __future__ import annotations

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_last_json(text: str) -> dict:
    """Return the last ```json fenced object, else the last bare {...} blob, else {}."""
    if not text:
        return {}
    matches = _FENCE_RE.findall(text)
    for blob in reversed(matches):
        try:
            return json.loads(blob)
        except Exception:
            continue
    # fall back: last balanced-looking {...}
    start = text.rfind("{")
    while start != -1:
        for end in range(len(text), start, -1):
            chunk = text[start:end]
            try:
                return json.loads(chunk)
            except Exception:
                continue
        start = text.rfind("{", 0, start)
    return {}


def truncate(s: str, head: int = 4000, tail: int = 2000) -> str:
    if s is None:
        return ""
    if len(s) <= head + tail:
        return s
    return s[:head] + f"\n...[truncated {len(s) - head - tail} chars]...\n" + s[-tail:]
