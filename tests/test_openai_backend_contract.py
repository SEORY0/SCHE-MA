"""OpenAiApiBackend produces a valid StageResult with NO API key (mock LLM).

Mirror of test_backend_contract.py for the OpenAI Responses backend: exercises the full
agent loop (function-tool dispatch, usage accumulation with cached-input split, early-stop
on crash, final-JSON parse, StageResult contract) entirely offline.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))
import mock_openai as mock  # noqa: E402

from schemata.backends.openai_api import OpenAiApiBackend  # noqa: E402
from schemata.core.config import load_settings  # noqa: E402
from schemata.core.models import StageRequest, Usage, Verdict  # noqa: E402


def _req(cwd, *, stage, tier, model="gpt5", **kw):
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
        mock.response(
            [mock.function_call("c1", "glob", json.dumps({"pattern": "**/*.c"}))],
            status="completed", u=mock.usage(inp=1500, out=20, cached=0)),
        mock.response(
            [mock.output_text('done\n```json\n{"crash_type":"x","suspected_files":["a.c"]}\n```')],
            status="completed", u=mock.usage(inp=1520, out=40, cached=500)),
    ]
    fake = mock.FakeOpenAI(script)
    backend = OpenAiApiBackend(load_settings(), client=fake)
    res = _run(backend, _req(tmp_path, stage="recon", tier="read_only"))

    assert res.stop_reason == "completed"
    assert res.structured_output == {"crash_type": "x", "suspected_files": ["a.c"]}
    assert isinstance(res.usage, Usage)
    # input accumulated across turns; cached 500 split out of the 2nd turn's 1520 input
    assert res.usage.input_tokens == 1500 + (1520 - 500)
    assert res.usage.cache_read_tokens == 500
    assert res.cost_usd > 0

    # loop wiring observable on the fake
    assert fake.calls[0]["tool_choice"] == "auto"
    assert "glob" in {t["name"] for t in fake.calls[0]["tools"]}
    assert all(t["type"] == "function" for t in fake.calls[0]["tools"])
    assert "submit_poc" not in {t["name"] for t in fake.calls[0]["tools"]}  # read_only
    assert fake.calls[1]["n_input"] > fake.calls[0]["n_input"]              # transcript grew


def test_generate_stops_on_crash_and_records_submission(tmp_path, monkeypatch):
    from schemata.backends.tools import dispatcher as disp_mod
    (tmp_path / "poc").write_bytes(b"crashme")
    monkeypatch.setattr(disp_mod.SubmitClient, "submit",
                        lambda self, p: Verdict(exit_code=1, output="ASAN heap-overflow", poc_id="p7"))

    script = [
        mock.response(
            [mock.output_text("submitting"),
             mock.function_call("c1", "submit_poc", json.dumps({"poc_path": "poc"}))],
            status="completed", u=mock.usage(inp=2000, out=30)),
    ]
    fake = mock.FakeOpenAI(script)
    backend = OpenAiApiBackend(load_settings(), client=fake)
    res = _run(backend, _req(tmp_path, stage="generate", tier="full"))

    assert res.stop_reason == "crash_found"
    assert res.artifacts.poc_path == "poc"
    assert len(res.artifacts.submissions) == 1 and res.artifacts.submissions[0].exit_code == 1
    assert res.usage.input_tokens == 2000
    assert "submit_poc" in {t["name"] for t in fake.calls[0]["tools"]}


def test_generate_does_not_short_circuit_on_crash_in_a2a_mode(tmp_path):
    """A2A mode (req.submit_fn set): a crash may be a false positive, so the loop must NOT
    exit on first crash — the agent gets another turn to react to the verdict."""
    (tmp_path / "poc").write_bytes(b"crashme")

    async def fake_submit(p):
        return Verdict(exit_code=1, output="ASAN heap-overflow at WRONG::function", poc_id="p7")

    script = [
        mock.response(
            [mock.output_text("trying"),
             mock.function_call("c1", "submit_poc", json.dumps({"poc_path": "poc"}))],
            status="completed", u=mock.usage(inp=2000, out=30)),
        mock.response(
            [mock.output_text('not the target bug; giving up\n```json\n{"final_exit_code":1}\n```')],
            status="completed", u=mock.usage(inp=50, out=40)),
    ]
    fake = mock.FakeOpenAI(script)
    backend = OpenAiApiBackend(load_settings(), client=fake)
    req = _req(tmp_path, stage="generate", tier="full")
    req.submit_fn = fake_submit
    res = _run(backend, req)

    assert res.stop_reason == "completed"
    assert len(res.artifacts.submissions) == 1 and res.artifacts.submissions[0].exit_code == 1
    assert len(fake.calls) == 2


def test_json_flush_recovers_contract_when_unemitted(tmp_path):
    """A turn that ends (completed) without emitting the contract JSON triggers one no-tools
    flush turn that forces the deliverable."""
    script = [
        mock.response([mock.output_text("I explored but forgot the JSON")],
                      status="completed", u=mock.usage(inp=900, out=10)),
        # flush turn (tool_choice=none) returns the JSON
        mock.response([mock.output_text('```json\n{"crash_type":"y"}\n```')],
                      status="completed", u=mock.usage(inp=200, out=20)),
    ]
    fake = mock.FakeOpenAI(script)
    backend = OpenAiApiBackend(load_settings(), client=fake)
    res = _run(backend, _req(tmp_path, stage="recon", tier="read_only"))

    assert res.structured_output == {"crash_type": "y"}
    assert fake.calls[-1]["tool_choice"] == "none"   # the flush call
