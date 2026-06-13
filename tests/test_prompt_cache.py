"""M3-2: prompt cache placement + model params."""
import logging
from pathlib import Path

from schemata.config import load_settings
from schemata.models import StageRequest, ThinkingConfig
from schemata.backends import prompt_cache as pc
from schemata.backends.base import MODEL_IDS


def _req(model="opus", thinking=False, system="x" * 40000):
    return StageRequest(
        stage="generate", system_prompt=system, kickoff="go", cwd=Path("/tmp"),
        model=model, allowed_tools=["Bash"], permission_tier="full",
        thinking=ThinkingConfig() if thinking else None,
    )


def test_system_block_has_single_cache_control():
    blocks = pc.system_blocks(_req())
    assert len(blocks) == 1
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert blocks[0]["text"].startswith("x")


def test_model_params_thinking_opus():
    p = pc.model_params(_req(model="opus", thinking=True), load_settings())
    assert p["model"] == MODEL_IDS["opus"]   # whatever opus is pinned to (e.g. claude-opus-4-6)
    assert p["thinking"] == {"type": "adaptive"}
    assert p["output_config"]["effort"]  # effort allowed on opus
    assert p["max_tokens"] >= 16000


def test_model_params_haiku_recon_no_thinking_no_effort():
    p = pc.model_params(_req(model="haiku", thinking=False), load_settings())
    assert p["model"] == "claude-haiku-4-5"
    assert "thinking" not in p and "output_config" not in p  # effort would 400 on Haiku
    assert p["max_tokens"] == 8000


def test_rolling_breakpoint_moves_forward():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": ["<sdk-block-object>"]},  # not dicts -> untouched
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "a"},
            {"type": "tool_result", "tool_use_id": "t2", "content": "b"},
        ]},
    ]
    pc.with_breakpoints(messages)
    last = messages[-1]["content"]
    assert "cache_control" not in last[0]            # only the last block is marked
    assert last[1]["cache_control"] == {"type": "ephemeral"}

    # next turn: append a new tool_result message -> marker must move, old stripped
    messages.append({"role": "assistant", "content": ["<obj>"]})
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t3", "content": "c"}]})
    pc.with_breakpoints(messages)
    assert "cache_control" not in last[1]            # previous marker removed
    assert messages[-1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    # never more than one message-level breakpoint
    marks = sum(
        1 for m in messages if isinstance(m.get("content"), list)
        for b in m["content"] if isinstance(b, dict) and "cache_control" in b
    )
    assert marks == 1


def test_small_prompt_warns(caplog):
    with caplog.at_level(logging.WARNING, logger="schemata.prompt_cache"):
        pc.system_blocks(_req(model="opus", system="tiny"))
    assert any("cache floor" in r.message for r in caplog.records)
