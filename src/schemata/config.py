"""Load config/schemata.toml + .env into a typed Settings object."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    # A pre-existing EMPTY env var (common shell-profile pollution, e.g. `export
    # ANTHROPIC_API_KEY=`) blocks load_dotenv from filling it, since load_dotenv does
    # not override already-set vars. Drop empty ANTHROPIC_/CYBERGYM_ vars first so a
    # populated .env value wins, while real (non-empty) env vars still override .env.
    for _k in [k for k in os.environ
               if os.environ[k] == "" and (k.startswith("ANTHROPIC_") or k.startswith("CYBERGYM_"))]:
        del os.environ[_k]
    load_dotenv()
except Exception:  # dotenv optional
    pass

# Repo root: where config/, prompts/, data/ live. Overridable via SCHEMATA_ROOT for
# installed/containerized deployments where the package is not run from the source tree
# (e.g. the AgentBeats Docker image: `pip install .` puts the package in site-packages).
PKG_ROOT = Path(os.environ.get("SCHEMATA_ROOT") or Path(__file__).resolve().parents[2])
DEFAULT_CONFIG = PKG_ROOT / "config" / "schemata.toml"
TEMPLATE_CONFIG = PKG_ROOT / "config" / "templates" / "schemata.toml"
PROMPTS_DIR = PKG_ROOT / "prompts"
DATA_DIR = PKG_ROOT / "data"
RUNS_DIR = PKG_ROOT / "runs"


@dataclass
class Settings:
    raw: dict[str, Any]
    backend: str
    server_url: str
    data_dir: str
    mask_map: str
    cybergym_python: str
    require_flag: bool
    rewrite_submit_host: bool
    rate_limit_max: int
    rate_limit_window_s: int
    budget_total_usd: float
    per_task_soft_usd: float
    anthropic_api_key: str | None = field(default=None)

    def stage_cfg(self, stage: str) -> dict[str, Any]:
        return self.raw.get("stages", {}).get(stage, {})

    def model_for(self, stage: str, difficulty: str) -> str:
        """Pick the model alias per stage, cost-optimized.

        Recon is always the cheapest model (haiku) — it's narrowing, not reasoning.
        Analyze is a mid-tier reasoner (sonnet by default) — explores the narrowed surface
        and produces the byte-level PoC plan that generate executes.
        Generate is difficulty-dependent: easy/medium fall to sonnet (3x cheaper than opus),
        hard escalates to opus for the final crafting + iteration loop.
        """
        models = self.raw.get("models", {})
        if stage == "recon":
            return models.get("recon", "haiku")
        if stage == "analyze":
            return models.get("analyze", "sonnet")
        if stage == "discriminate":
            # Independent referee — judgment quality matters more than cost; sonnet default.
            return models.get("discriminate", "sonnet")
        return models.get("by_difficulty", {}).get(difficulty, "opus")

    @property
    def claude_code(self) -> dict[str, Any]:
        return self.raw.get("claude_code", {})

    @property
    def tokens(self) -> dict[str, Any]:
        return self.raw.get("tokens", {})

    @property
    def instrument(self) -> dict[str, Any]:
        return self.raw.get("instrument", {})

    @property
    def thinking_budget(self) -> int:
        return int(self.raw.get("thinking", {}).get("hard_budget_tokens", 16000))


def load_settings(config_path: str | Path | None = None) -> Settings:
    path = Path(config_path) if config_path else DEFAULT_CONFIG
    if config_path is None and not path.exists() and TEMPLATE_CONFIG.exists():
        path = TEMPLATE_CONFIG
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    server = raw.get("server", {})
    budget = raw.get("budget", {})

    def env(key: str, default: Any) -> Any:
        return os.environ.get(key, default)

    def repo_path(value: Any) -> str:
        if not value:
            return ""
        p = Path(str(value)).expanduser()
        return str(p if p.is_absolute() else PKG_ROOT / p)

    def command_or_path(value: Any) -> str:
        if not value:
            return ""
        s = str(value)
        if "/" not in s and "\\" not in s:
            return s
        return repo_path(s)

    return Settings(
        raw=raw,
        backend=raw.get("backend", {}).get("default", "claude_code"),
        server_url=env("CYBERGYM_SERVER_URL", server.get("url", "http://127.0.0.1:8666")),
        data_dir=repo_path(env("CYBERGYM_DATA_DIR", server.get("data_dir", ""))),
        mask_map=repo_path(env("CYBERGYM_MASK_MAP", server.get("mask_map", ""))),
        cybergym_python=command_or_path(env("CYBERGYM_PYTHON", server.get("cybergym_python", "python3"))),
        require_flag=bool(server.get("require_flag", False)),
        rewrite_submit_host=bool(server.get("rewrite_submit_host", False)),
        rate_limit_max=int(server.get("rate_limit_max", 20)),
        rate_limit_window_s=int(server.get("rate_limit_window_s", 60)),
        budget_total_usd=float(budget.get("total_usd", 2000.0)),
        per_task_soft_usd=float(budget.get("per_task_soft_usd", 10.0)),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
