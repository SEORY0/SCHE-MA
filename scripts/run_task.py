#!/usr/bin/env python
"""Thin wrapper: python scripts/run_task.py --task-id arvo:10400 --backend claude_code"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from schemata.cli import app  # noqa: E402

if __name__ == "__main__":
    # default subcommand = run-task
    if not any(a in ("run-task", "run-subset") for a in sys.argv[1:]):
        sys.argv.insert(1, "run-task")
    app()
