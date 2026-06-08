"""M3-4: ClaudeApiBackend produces a valid StageResult with NO API key (mock LLM).

This is the M3 'build complete' gate — it exercises the full agent loop (tool dispatch,
usage accumulation incl. cache fields, rolling cache breakpoint, early-stop on crash,
final-JSON parse, StageResult contract) entirely offline. The real-API parity run is M3-5.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))
import mock_anthropic as mock  # noqa: E402

from schemata.config import load_settings  # noqa: E402
from schemata.models import StageRequest, Usage, Verdict  # noqa: E402
from schemata.backends.claude_api import ClaudeApiBackend  # noqa: E402


def _req(cwd, *, stage, tier, model, **kw):
    base = dict(
        stage=stage, system_prompt="S" * 40000, kickoff="go", cwd=Path(cwd),
        model=model, allowed_tools=["Bash"], permission_tier=tier,
        task_id_masked="m1", agent_id="a1", checksum="c1",
        server_url="http://127.0.0.1:8666", max_budget_usd=10.0,
    )
    base.update(kw)
    return StageRequest(**base)


def _run(backend, req):
    return asyncio.run(backend.run_stage(req))


def test_recon_completes_with_parsed_json_and_accumulated_usage(tmp_path):
    script = [
        mock.message([mock.tool_use("t1", "glob", {"pattern": "**/*.c"})],
                     "tool_use", mock.usage(inp=1500, out=20, cache_write=4096)),
        mock.message([mock.text('done\n```json\n{"crash_type":"x","suspected_files":["a.c"]}\n```')],
                     "end_turn", mock.usage(inp=20, out=40, cache_read=4096)),
    ]
    fake = mock.FakeAnthropic(script)
    backend = ClaudeApiBackend(load_settings(), client=fake)
    res = _run(backend, _req(tmp_path, stage="recon", tier="read_only", model="haiku"))

    # StageResult contract
    assert res.stop_reason == "completed"
    assert res.structured_output == {"crash_type": "x", "suspected_files": ["a.c"]}
    assert isinstance(res.usage, Usage)
    # usage accumulated across BOTH turns, cache fields preserved
    assert res.usage.input_tokens == 1520
    assert res.usage.cache_write_tokens == 4096 and res.usage.cache_read_tokens == 4096
    assert res.cost_usd > 0

    # caching + loop wiring observable on the fake
    assert fake.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert fake.calls[0]["tool_choice"] == {"type": "auto"}
    assert "glob" in {t["name"] for t in fake.calls[0]["tools"]}
    assert "submit_poc" not in {t["name"] for t in fake.calls[0]["tools"]}  # read_only
    assert fake.calls[1]["n_messages"] > fake.calls[0]["n_messages"]        # transcript grew
    assert fake.calls[1]["last_marked"] is True                            # rolling breakpoint


def test_generate_stops_on_crash_and_records_submission(tmp_path, monkeypatch):
    from schemata.backends.tools import dispatcher as disp_mod
    (tmp_path / "poc").write_bytes(b"crashme")
    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=1, output="ASAN heap-overflow", poc_id="p7"))

    script = [
        mock.message([mock.text("submitting"), mock.tool_use("t1", "submit_poc", {"poc_path": "poc"})],
                     "tool_use", mock.usage(inp=2000, out=30, cache_write=4096)),
    ]
    fake = mock.FakeAnthropic(script)
    backend = ClaudeApiBackend(load_settings(), client=fake)
    res = _run(backend, _req(tmp_path, stage="generate", tier="full", model="opus"))

    assert res.stop_reason == "crash_found"
    assert res.artifacts.poc_path == "poc"
    assert len(res.artifacts.submissions) == 1 and res.artifacts.submissions[0].exit_code == 1
    assert res.usage.input_tokens == 2000 and res.usage.cache_write_tokens == 4096
    assert "submit_poc" in {t["name"] for t in fake.calls[0]["tools"]}


def test_generate_does_not_short_circuit_on_crash_in_a2a_mode(tmp_path):
    """In A2A mode (req.submit_fn set) the green only tests against the vul binary, so
    a crash may be a FALSE POSITIVE (also crashes fix, scoring 0). The loop must NOT exit
    on first crash — the agent has to see the verdict, compare the sanitizer trace to the
    target sink, and decide. M6-c regression guard."""
    (tmp_path / "poc").write_bytes(b"crashme")

    async def fake_submit(p):
        return Verdict(exit_code=1, output="ASAN heap-overflow at WRONG::function", poc_id="p7")

    script = [
        mock.message([mock.text("trying"), mock.tool_use("t1", "submit_poc", {"poc_path": "poc"})],
                     "tool_use", mock.usage(inp=2000, out=30)),
        # After seeing the crash verdict, the agent decides it's the wrong trace and stops normally.
        mock.message([mock.text('not the target bug; giving up\n```json\n{"final_exit_code":1}\n```')],
                     "end_turn", mock.usage(inp=50, out=40)),
    ]
    fake = mock.FakeAnthropic(script)
    backend = ClaudeApiBackend(load_settings(), client=fake)
    req = _req(tmp_path, stage="generate", tier="full", model="opus")
    req.submit_fn = fake_submit  # A2A arena mode
    res = _run(backend, req)

    # The loop did NOT exit on crash_found — the agent's end_turn determines stop_reason.
    assert res.stop_reason == "completed"
    # But the submission was still recorded.
    assert len(res.artifacts.submissions) == 1 and res.artifacts.submissions[0].exit_code == 1
    # The agent got a second turn to react to the crash verdict.
    assert len(fake.calls) == 2
