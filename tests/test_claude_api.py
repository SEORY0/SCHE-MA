"""Exercise the Claude API tool loop with a fake Anthropic client (no API cost).

Proves: multi-turn tool_use loop, dispatcher tool execution, usage accumulation across
turns, final-JSON extraction, cost computation, and the error path.
"""
import asyncio
import json
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


def test_flush_emits_json_when_tool_budget_exhausted(tmp_path):
    # The bug this fixes: a stage spends every tool turn exploring and exits at max_turns
    # without ever emitting its contract JSON -> structured_output empty -> next stage starved.
    # The flush fallback fires one final no-tools turn that returns the deliverable.
    scripted = [
        _msg("tool_use", [_text("exploring"), _tu("t1", "glob", {"pattern": "**/*.c"})],
             _usage(800, 15)),
        _msg("tool_use", [_text("still looking"), _tu("t2", "glob", {"pattern": "**/*.h"})],
             _usage(60, 15)),
        # flush turn (tool_choice=none): pure text carrying the JSON
        _msg("end_turn",
             [_text('done\n```json\n{"crash_type":"heap-buffer-overflow",'
                    '"vuln_classes":["heap-buffer-overflow-read"]}\n```')],
             _usage(40, 25)),
    ]
    req = StageRequest(
        stage="recon", system_prompt="x" * 400, kickoff="go", cwd=tmp_path,
        model="haiku", allowed_tools=["Bash"], permission_tier="read_only", max_turns=2,
    )
    backend = ClaudeApiBackend(load_settings(), client=_FakeClient(scripted))
    res = asyncio.run(backend.run_stage(req))

    assert res.structured_output.get("vuln_classes") == ["heap-buffer-overflow-read"]
    # the flush turn's usage is included (800+60+40 in, 15+15+25 out)
    assert res.usage.input_tokens == 900 and res.usage.output_tokens == 55


def test_no_flush_when_json_already_emitted(tmp_path):
    # Happy path: JSON present after a normal end_turn -> flush must NOT fire. Only two messages
    # are scripted; a stray flush call would pop an empty list (swallowed) but, more importantly,
    # usage must equal exactly the two real turns.
    scripted = [
        _msg("tool_use", [_tu("t1", "glob", {"pattern": "**/*.c"})], _usage(100, 10)),
        _msg("end_turn", [_text('```json\n{"crash_type":"x"}\n```')], _usage(50, 20)),
    ]
    backend = ClaudeApiBackend(load_settings(), client=_FakeClient(scripted))
    res = asyncio.run(backend.run_stage(_req(tmp_path)))
    assert res.structured_output.get("crash_type") == "x"
    assert res.usage.input_tokens == 150 and res.usage.output_tokens == 30  # no extra flush turn


def test_writes_stage_trace_jsonl(tmp_path):
    scripted = [
        _msg("tool_use",
             [_text("checking"), _tu("tu1", "bash", {"cmd": "echo trace"})],
             _usage(100, 10)),
        _msg("end_turn", [_text('```json\n{"crash_type":"x"}\n```')], _usage(50, 20)),
    ]
    settings = load_settings()
    settings.raw.setdefault("logging", {})["trace_api_messages"] = True
    backend = ClaudeApiBackend(settings, client=_FakeClient(scripted))
    res = asyncio.run(backend.run_stage(_req(tmp_path)))

    trace_path = tmp_path / "stage_recon_trace.jsonl"
    rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert res.structured_output == {"crash_type": "x"}
    assert [r["event"] for r in rows] == [
        "stage_start",
        "api_request",
        "assistant_message",
        "tool_results",
        "api_request",
        "assistant_message",
        "stage_end",
    ]
    assert rows[3]["results"][0]["name"] == "bash"
    assert rows[-1]["structured_output"] == {"crash_type": "x"}
