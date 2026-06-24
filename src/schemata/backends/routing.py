"""Provider-routing backend — dispatches each stage to a provider-specific backend.

The orchestrator builds ONE backend per run, but in routed mode different stages resolve to
different providers (e.g. recon/analyze → Anthropic, hard generate → OpenAI). This backend
keeps that single-object contract: it reads each stage's model alias, looks up the provider
in the registry, and delegates to the matching sub-backend.

Sub-backends are built lazily and cached, so a Claude-only run never constructs the OpenAI
client and never requires OPENAI_API_KEY. The Anthropic sub-backend name comes from config
([backend].anthropic_backend, default "claude_api").
"""
from __future__ import annotations

from ..core.models import StageRequest, StageResult
from .base import AgentBackend, provider_of


class RoutingBackend(AgentBackend):
    name = "routed_api"

    def __init__(self, settings, *, anthropic_backend=None, openai_backend=None):
        super().__init__(settings)
        self._cache: dict[str, AgentBackend] = {}
        if anthropic_backend is not None:  # test seam
            self._cache["anthropic"] = anthropic_backend
        if openai_backend is not None:     # test seam
            self._cache["openai"] = openai_backend
        self._anthropic_name = settings.raw.get("backend", {}).get("anthropic_backend", "claude_api")

    def _backend_for(self, provider: str) -> AgentBackend:
        if provider not in self._cache:
            if provider == "anthropic":
                if self._anthropic_name == "claude_code":
                    from .claude_code import ClaudeCodeBackend
                    self._cache[provider] = ClaudeCodeBackend(self.settings)
                else:
                    from .claude_api import ClaudeApiBackend
                    self._cache[provider] = ClaudeApiBackend(self.settings)
            elif provider == "openai":
                from .openai_api import OpenAiApiBackend
                self._cache[provider] = OpenAiApiBackend(self.settings)
            else:
                raise ValueError(f"no backend for provider {provider!r}")
        return self._cache[provider]

    async def run_stage(self, req: StageRequest) -> StageResult:
        return await self._backend_for(provider_of(req.model)).run_stage(req)
