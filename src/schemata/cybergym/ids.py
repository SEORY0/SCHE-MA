"""Map between real task ids, masked ids, and tasks_metadata.json rows."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from ..config import DATA_DIR
from ..models import TaskMeta

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
    data = {
        "task_id": real_task_id,
        "masked_id": row.get("masked_id") or masked_for(real_task_id),
        "source": row.get("source", real_task_id.split(":", 1)[0]),
        "project": row.get("project", "unknown"),
        "crash_type": row.get("crash_type", "unknown"),
        "crash_type_category": row.get("crash_type_category", "unknown"),
        "sanitizer": row.get("sanitizer"),
        "input_format": row.get("input_format", "unknown"),
        "project_complexity": row.get("project_complexity", "unknown"),
        "difficulty_estimate": row.get("difficulty_estimate", "medium"),
    }
    return TaskMeta(**data)
