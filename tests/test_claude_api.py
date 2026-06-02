"""Exercise the Claude API tool loop with a fake Anthropic client (no API cost).

Proves: multi-turn tool_use loop, dispatcher tool execution, usage accumulation across
turns, final-JSON extraction, cost computation, and the error path.
"""
import asyncio
import types

from schemata.backends.claude_api import ClaudeApiBackend
from schemata.config import load_settings
from schemata.models import StageRequest


def _tu(id, name, inp):
    return types.SimpleNamespace(type="tool_use", id=id, name=name, input=inp)


def _text(t):
    return types.SimpleNamespace(type="text", text=t)


def _usage(i, o):
    return types.SimpleNamespace(input_tokens=i, output_tokens=o,
                                 cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _msg(stop, content, usage):
    return types.SimpleNamespace(stop_reason=stop, content=content, usage=usage)


class _FakeStream:
    def __init__(self, msg): self._m = msg
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get_final_message(self): return self._m


class _FakeMessages:
    def __init__(self, scripted): self._s = list(scripted)
    def stream(self, **kwargs): return _FakeStream(self._s.pop(0))


class _FakeClient:
    def __init__(self, scripted): self.messages = _FakeMessages(scripted)


class _RaisingMessages:
    def stream(self, **kwargs):
        raise RuntimeError("boom: 529 overloaded")


class _RaisingClient:
    messages = _RaisingMessages()


def _req(tmp_path, stage="recon", tier="read_only"):
    return StageRequest(
        stage=stage, system_prompt="x" * 400, kickoff="go", cwd=tmp_path,
        model="haiku", allowed_tools=["Bash"], permission_tier=tier, max_turns=5,
    )


def test_tool_loop_runs_executes_tool_and_extracts_json(tmp_path):
    scripted = [
        _msg("tool_use",
             [_text("let me look around"), _tu("tu1", "bash", {"cmd": "echo hi"})],
             _usage(100, 10)),
        _msg("end_turn",
             [_text('found it\n```json\n{"crash_type":"heap-buffer-overflow","suspected_files":["a.c"]}\n```')],
             _usage(50, 20)),
    ]
    backend = ClaudeApiBackend(load_settings(), client=_FakeClient(scripted))
    res = asyncio.run(backend.run_stage(_req(tmp_path)))

    assert res.stop_reason == "completed"
    assert res.structured_output.get("crash_type") == "heap-buffer-overflow"
    assert res.usage.input_tokens == 150 and res.usage.output_tokens == 30  # accumulated
    assert res.cost_usd > 0  # haiku: 150 in + 30 out priced


def test_api_error_maps_to_error_stop(tmp_path):
    backend = ClaudeApiBackend(load_settings(), client=_RaisingClient())
    res = asyncio.run(backend.run_stage(_req(tmp_path)))
    assert res.stop_reason == "error" and "boom" in (res.error or "")
