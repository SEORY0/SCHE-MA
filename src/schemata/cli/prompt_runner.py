"""Free-form prompt dispatch.

claude_code backend -> headless `claude -p <prompt>` subprocess (uses the local
  Claude Code login; no ANTHROPIC_API_KEY needed).
claude_api  backend -> Anthropic Messages API (uses ANTHROPIC_API_KEY).

Both return (text, usage_dict). usage_dict is empty {} for claude_code because
the JSON envelope from `claude -p` does not expose per-call usage uniformly
across CLI versions; the engine's stage runs do parse it, but a quick chat turn
does not need to.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from ..backends.base import MODEL_IDS


@dataclass
class AskResult:
    text: str
    usage: dict
    backend: str
    model: str


def _resolve_model(alias: str) -> str:
    return MODEL_IDS.get(alias, alias)


def _ask_code(prompt: str, model_id: str, cwd: str) -> AskResult:
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise RuntimeError(
            "`claude` CLI not found in PATH. Install Claude Code "
            "(https://github.com/anthropics/claude-code) or switch backend "
            "with `/backend claude_api`."
        )
    cmd = [
        claude_bin, "-p", prompt,
        "--output-format", "json",
        "--model", model_id,
        "--permission-mode", "bypassPermissions",
    ]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr[:500]}")
    try:
        env = json.loads(proc.stdout)
        text = env.get("result") or env.get("text") or proc.stdout
        usage = env.get("usage") or {}
    except Exception:
        text, usage = proc.stdout, {}
    return AskResult(text=text.strip(), usage=usage, backend="claude_code", model=model_id)


def _ask_api(prompt: str, model_id: str, api_key: str | None) -> AskResult:
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Either `export ANTHROPIC_API_KEY=sk-...` "
            "or switch with `/backend claude_code` if you have Claude Code installed."
        )
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError(f"anthropic SDK not installed: {e}")
    client = Anthropic(api_key=key)
    resp = client.messages.create(
        model=model_id,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    u = resp.usage
    usage = {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    return AskResult(text=text.strip(), usage=usage, backend="claude_api", model=model_id)


def ask(session, prompt: str, *, model_alias: str | None = None) -> AskResult:
    """Dispatch a free-form prompt to the active backend."""
    alias = model_alias or session.model_alias
    model_id = _resolve_model(alias)
    if session.backend == "claude_code":
        return _ask_code(prompt, model_id, cwd=session.cwd)
    if session.backend == "claude_api":
        return _ask_api(prompt, model_id, api_key=session.settings.anthropic_api_key)
    raise RuntimeError(f"unknown backend: {session.backend!r}")
