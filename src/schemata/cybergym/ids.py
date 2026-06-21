"""Map between real task ids, masked ids, and tasks_metadata.json rows."""
from __future__ import annotations

import json
from functools import lru_cache

from ..core.config import DATA_DIR
from ..core.models import TaskMeta

TASKS_METADATA = DATA_DIR / "tasks_metadata.json"
MASK_MAP = DATA_DIR / "mask_map.json"


@lru_cache(maxsize=1)
def _metadata() -> dict:
    if TASKS_METADATA.exists():
        with open(TASKS_METADATA) as f:
            return json.load(f)
    return {}


@lru_cache(maxsize=1)
def _mask_map() -> dict:
    if MASK_MAP.exists():
        with open(MASK_MAP) as f:
            return json.load(f)
    return {}


def masked_for(real_task_id: str) -> str | None:
    return _mask_map().get(real_task_id)


def lookup(real_task_id: str) -> TaskMeta:
    """Return TaskMeta for a real id (e.g. 'arvo:10400'), with safe defaults."""
    row = _metadata().get(real_task_id, {})
    # NOTE: 48 oss-fuzz rows have explicit null for project/crash_type/sanitizer
    # (see _missing=True). dict.get() returns the null, not the default — so coerce
    # falsy values to defaults instead of letting None reach TaskMeta.
    def _g(key: str, default):
        return row.get(key) or default
    data = {
        "task_id": real_task_id,
        "masked_id": _g("masked_id", masked_for(real_task_id)),
        "source": _g("source", real_task_id.split(":", 1)[0]),
        "project": _g("project", "unknown"),
        "crash_type": _g("crash_type", "unknown"),
        "crash_type_category": _g("crash_type_category", "unknown"),
        "sanitizer": row.get("sanitizer"),  # Optional[str] — None is fine here
        "input_format": _g("input_format", "unknown"),
        "project_complexity": _g("project_complexity", "unknown"),
        "difficulty_estimate": _g("difficulty_estimate", "medium"),
    }
    return TaskMeta(**data)
