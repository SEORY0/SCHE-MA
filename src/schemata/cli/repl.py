"""SCHE-MA interactive REPL — Claude Code-style, pretty UI.

Run `schema` (or `python -m schemata.cli`) to enter the loop. Slash commands
drive the engine (`/task`, `/subset`); free-form prompts go to the active
backend (`claude_code` or `claude_api`).
"""
from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass, field

try:  # readline gives history + line-edit on Unix; ok if missing
    import readline  # noqa: F401
except Exception:
    pass

from ..core.config import Settings, load_settings
from . import ui
from .commands import ExitREPL, dispatch_slash
from .prompt_runner import ask


@dataclass
class Session:
    settings: Settings
    backend: str
    model_alias: str = "sonnet"
    cwd: str = field(default_factory=lambda: os.getcwd())
    cost_usd: float = 0.0
    tasks_run: int = 0
    last_usage: dict = field(default_factory=dict)
    _run_counter: int = 0

    def _next_run_id(self) -> str:
        self._run_counter += 1
        return time.strftime("%Y%m%d_%H%M%S") + (f"_{self._run_counter}" if self._run_counter > 1 else "")


def _read_line(session: Session) -> str:
    """Pretty colored prompt; falls back to plain `input()` if terminal lacks color."""
    prompt = ui.prompt_text(session.backend, session.model_alias)
    try:
        return input(prompt).rstrip()
    except EOFError:
        raise
    except KeyboardInterrupt:
        ui.console.print()  # newline
        return ""


def start_repl(session: Session) -> None:
    ui.print_banner()
    ui.print_status(
        backend=session.backend,
        model=session.model_alias,
        api_key_set=bool(session.settings.anthropic_api_key),
        cwd=session.cwd,
    )
    while True:
        try:
            line = _read_line(session)
        except EOFError:
            ui.console.print()
            ui.print_info("bye.")
            return
        if not line:
            continue
        if line.startswith("/"):
            try:
                out = dispatch_slash(session, line)
            except ExitREPL:
                ui.print_info("bye.")
                return
            except Exception as e:
                ui.print_err(str(e))
                continue
            if isinstance(out, str) and out:
                ui.console.print(out)
            continue
        # free-form prompt
        try:
            with ui.status(f"asking {session.backend}/{session.model_alias}…"):
                res = ask(session, line)
        except Exception as e:
            ui.print_err(str(e))
            continue
        session.last_usage = res.usage
        ui.print_reply(res.text)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="schema",
        description="SCHE-MA interactive runner (Claude Code-style REPL).",
    )
    p.add_argument("--backend", choices=("claude_code", "claude_api"),
                   help="override backend (default: settings.backend)")
    p.add_argument("--model", choices=("haiku", "sonnet", "opus"), default="sonnet",
                   help="default chat model (default: sonnet)")
    p.add_argument("--config", help="path to schemata.toml")
    p.add_argument("--cwd", default=os.getcwd(),
                   help="working dir for claude_code subprocess (default: cwd)")
    p.add_argument("--no-color", action="store_true", help="disable colored output")
    args = p.parse_args(argv)

    if args.no_color:
        ui.console.no_color = True

    try:
        settings = load_settings(args.config)
    except Exception as e:
        ui.print_err(f"failed to load settings: {e}")
        return 2

    session = Session(
        settings=settings,
        backend=args.backend or settings.backend,
        model_alias=args.model,
        cwd=args.cwd,
    )
    start_repl(session)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
