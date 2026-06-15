"""Smoke tests for the SCHE-MA interactive runner."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from schemata.runner import commands, prompt_runner, ui
from schemata.runner.repl import Session, main


def _settings():
    s = MagicMock()
    s.backend = "claude_code"
    s.anthropic_api_key = None
    s.server_url = "http://127.0.0.1:8666"
    s.budget_total_usd = 100.0
    s.per_task_soft_usd = 5.0
    return s


def _session(backend="claude_code"):
    return Session(settings=_settings(), backend=backend, model_alias="sonnet", cwd="/tmp")


def _run(line: str, session=None) -> str:
    """Run a slash command, capturing the rich console output as plain text."""
    session = session or _session()
    with ui.console.capture() as cap:
        commands.dispatch_slash(session, line)
    return cap.get()


# ---------- slash commands ----------

def test_help_lists_core_commands():
    out = _run("/help")
    for tok in ("/task", "/subset", "/backend", "/model", "/cost", "/exit"):
        assert tok in out


def test_unknown_command_reports():
    out = _run("/nope")
    assert "unknown command" in out.lower() or "/nope" in out


def test_backend_switch():
    s = _session()
    out = _run("/backend claude_api", s)
    assert "claude_api" in out
    assert s.backend == "claude_api"


def test_backend_rejects_garbage():
    s = _session()
    out = _run("/backend nope", s)
    assert "unknown backend" in out.lower()
    assert s.backend == "claude_code"


def test_model_switch():
    s = _session()
    out = _run("/model opus", s)
    assert "opus" in out
    assert s.model_alias == "opus"


def test_config_reports_state():
    out = _run("/config")
    assert "claude_code" in out and "backend" in out.lower()


def test_exit_raises():
    with pytest.raises(commands.ExitREPL):
        commands.dispatch_slash(_session(), "/exit")


def test_task_requires_arg():
    out = _run("/task")
    assert "usage" in out.lower()


# ---------- prompt dispatch ----------

def test_ask_api_requires_key():
    s = _session(backend="claude_api")
    import os
    if "ANTHROPIC_API_KEY" in os.environ:
        os.environ.pop("ANTHROPIC_API_KEY")
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        prompt_runner.ask(s, "hello")


def test_ask_code_requires_claude_cli():
    s = _session(backend="claude_code")
    with patch("schemata.runner.prompt_runner.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="claude.*not found"):
            prompt_runner.ask(s, "hello")


def test_ask_dispatches_to_code_subprocess():
    s = _session(backend="claude_code")
    fake = MagicMock(returncode=0, stdout='{"result":"hi","usage":{"input_tokens":3}}', stderr="")
    with patch("schemata.runner.prompt_runner.shutil.which", return_value="/usr/bin/claude"), \
         patch("schemata.runner.prompt_runner.subprocess.run", return_value=fake) as run:
        res = prompt_runner.ask(s, "hello")
    assert res.text == "hi"
    assert res.usage == {"input_tokens": 3}
    assert res.backend == "claude_code"
    cmd = run.call_args[0][0]
    assert "/usr/bin/claude" in cmd and "hello" in cmd


# ---------- main entry + UI ----------

def test_main_help_smoketest(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    cap = capsys.readouterr()
    assert "schema" in cap.out.lower() and "--backend" in cap.out


def test_banner_renders():
    with ui.console.capture() as cap:
        ui.print_banner("9.9.9")
    out = cap.get()
    assert "SCHE-MA" in out or "Security" in out
    assert "9.9.9" in out
