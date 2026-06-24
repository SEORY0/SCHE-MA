"""RoutingBackend dispatches each stage to the provider implied by its model alias."""
import asyncio
from pathlib import Path

import pytest

from schemata.backends import make_backend
from schemata.backends.base import AgentBackend
from schemata.backends.routing import RoutingBackend
from schemata.core.config import load_settings
from schemata.core.models import StageRequest, StageResult


class _Recorder(AgentBackend):
    def __init__(self, tag):
        self.tag = tag
        self.seen: list[str] = []

    async def run_stage(self, req: StageRequest) -> StageResult:
        self.seen.append(req.model)
        return StageResult(stage=req.stage, structured_output={"who": self.tag})


def _req(model):
    return StageRequest(
        stage="generate", system_prompt="s", kickoff="go", cwd=Path("/tmp"),
        model=model, allowed_tools=["Bash"], permission_tier="full",
    )


def test_routes_by_provider():
    anthropic = _Recorder("anthropic")
    openai = _Recorder("openai")
    backend = RoutingBackend(load_settings(), anthropic_backend=anthropic, openai_backend=openai)

    res_gpt = asyncio.run(backend.run_stage(_req("gpt5")))
    res_son = asyncio.run(backend.run_stage(_req("sonnet")))
    res_hai = asyncio.run(backend.run_stage(_req("haiku")))

    assert res_gpt.structured_output["who"] == "openai"
    assert res_son.structured_output["who"] == "anthropic"
    assert res_hai.structured_output["who"] == "anthropic"
    assert openai.seen == ["gpt5"]
    assert anthropic.seen == ["sonnet", "haiku"]


def test_lazy_no_openai_for_claude_only_run():
    """An Anthropic-only run must not construct the OpenAI sub-backend (no OPENAI_API_KEY)."""
    anthropic = _Recorder("anthropic")
    backend = RoutingBackend(load_settings(), anthropic_backend=anthropic)
    asyncio.run(backend.run_stage(_req("sonnet")))
    assert "openai" not in backend._cache  # never built


def test_make_backend_knows_new_names():
    settings = load_settings()
    assert isinstance(make_backend("routed_api", settings), RoutingBackend)
    # The openai_api name maps to OpenAiApiBackend, whose key guard fires before any SDK import
    # when no OPENAI_API_KEY is configured (proves the factory recognizes the name).
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        make_backend("openai_api", settings)


def test_make_backend_rejects_unknown():
    with pytest.raises(ValueError, match="unknown backend"):
        make_backend("nope", load_settings())
