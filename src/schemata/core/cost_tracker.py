"""Token -> USD accounting with per-task and global budget guards."""
from __future__ import annotations

import json
from pathlib import Path

from .models import Usage


class CostTracker:
    def __init__(self, total_budget_usd: float, per_task_soft_usd: float):
        self.total_budget_usd = total_budget_usd
        self.per_task_soft_usd = per_task_soft_usd
        self.global_spent = 0.0
        self.per_task: dict[str, float] = {}
        self.entries: list[dict] = []

    def add(self, task_id: str, stage: str, usage: Usage, cost_usd: float) -> None:
        self.global_spent += cost_usd
        self.per_task[task_id] = self.per_task.get(task_id, 0.0) + cost_usd
        self.entries.append({
            "task_id": task_id,
            "stage": stage,
            "model": usage.model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_tokens": usage.cache_read_tokens,
            "cache_write_tokens": usage.cache_write_tokens,
            "cost_usd": round(cost_usd, 6),
        })

    def over_global_budget(self) -> bool:
        return self.global_spent >= self.total_budget_usd

    def over_task_soft_cap(self, task_id: str) -> bool:
        return self.per_task.get(task_id, 0.0) >= self.per_task_soft_usd

    def task_cost(self, task_id: str) -> float:
        return self.per_task.get(task_id, 0.0)

    def write(self, path: Path) -> None:
        path.write_text(json.dumps({
            "global_spent_usd": round(self.global_spent, 6),
            "per_task": {k: round(v, 6) for k, v in self.per_task.items()},
            "entries": self.entries,
        }, indent=2))
