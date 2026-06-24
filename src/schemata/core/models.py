"""Core data models shared across orchestrator, router, backends, and cybergym client."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Stage = Literal["recon", "analyze", "generate", "discriminate"]
Tier = Literal["read_only", "write", "full"]
Difficulty = Literal["easy", "medium", "hard"]


class TaskMeta(BaseModel):
    """One row of tasks_metadata.json (subset of fields we route on)."""
    task_id: str
    masked_id: str | None = None
    source: str = "arvo"
    project: str = "unknown"
    crash_type: str = "unknown"
    crash_type_category: str = "unknown"
    sanitizer: str | None = None
    input_format: str = "unknown"
    project_complexity: str = "unknown"
    difficulty_estimate: Difficulty = "medium"


class Usage(BaseModel):
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            model=self.model or other.model,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
        )


class Verdict(BaseModel):
    """Result of one /submit-vul call."""
    exit_code: int
    output: str = ""
    poc_id: str | None = None

    @property
    def crashed(self) -> bool:
        # Server folds timeout(300) -> 0, so the agent-facing rule is simply != 0.
        return self.exit_code != 0


class SubmissionRecord(BaseModel):
    poc_path: str
    poc_sha256: str = ""
    exit_code: int = 0
    output_excerpt: str = ""
    poc_id: str | None = None

    @property
    def crashed(self) -> bool:
        return self.exit_code != 0


class Artifacts(BaseModel):
    poc_path: str | None = None
    submissions: list[SubmissionRecord] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class ThinkingConfig(BaseModel):
    budget_tokens: int = 16000


class CacheContext(BaseModel):
    system: str | None = None
    codebase_summary: dict | None = None


class StageRequest(BaseModel):
    stage: Stage
    system_prompt: str
    kickoff: str
    cwd: Path
    model: str
    allowed_tools: list[str]
    permission_tier: Tier
    max_turns: int = 20
    max_budget_usd: float | None = None
    thinking: ThinkingConfig | None = None
    prior_results: dict[str, dict] = Field(default_factory=dict)
    instrument_container: str | None = None
    mcp_endpoint: str | None = None
    recon_summary: dict | None = None
    cache_context: CacheContext | None = None
    # stage3 / submit
    submit_sh: str | None = None
    task_id_masked: str | None = None
    agent_id: str | None = None
    checksum: str | None = None
    server_url: str | None = None
    # A2A mode: async (poc_path) -> Optional[Verdict] transport; when set, the submit_poc
    # tool routes through it (green test_vulnerable) instead of the local SubmitClient.
    submit_fn: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)


class StageResult(BaseModel):
    stage: Stage
    structured_output: dict = Field(default_factory=dict)
    raw_transcript_tail: str = ""
    usage: Usage = Field(default_factory=Usage)
    cost_usd: float = 0.0
    artifacts: Artifacts = Field(default_factory=Artifacts)
    stop_reason: Literal["completed", "max_turns", "early_stop", "crash_found", "error"] = "completed"
    error: str | None = None


class PipelinePlan(BaseModel):
    difficulty: Difficulty
    stages: list[Stage]
    stage_models: dict[str, str]          # stage -> model alias ("haiku"/"sonnet"/"opus")
    has_instrument: bool = False
    has_mcp_index: bool = False
    thinking: bool = False
    minimize_info: bool = False
    # Routing agent metadata (additive, backward-compatible defaults)
    routing_source: str = "default"                            # "llm" | "llm_refined" | "default"
    vuln_classes: list[str] = Field(default_factory=list)      # pre-classified atomic vuln types
    generate_strategy_hint: str | None = None                  # e.g. "seed-mutate"
    budget_hint: str | None = None                             # "low" | "normal" | "high"
    routing_reasoning: str | None = None                       # LLM one-line justification


class TaskOutcome(BaseModel):
    task_id: str
    backend: str
    success: bool
    final_exit_code: int | None = None
    poc_id: str | None = None
    official_verified: bool = False
    official_reproduced: bool | None = None
    official_vul_exit_code: int | None = None
    official_fix_exit_code: int | None = None
    official_error: str | None = None
    failure_class: str | None = None
    failure_details: dict[str, Any] = Field(default_factory=dict)
    discriminator_accept: bool | None = None
    discriminator_verdict: str | None = None
    cost_usd: float = 0.0
    stages_run: list[str] = Field(default_factory=list)
    # tool name -> total call count across all stages (tool-adoption measurement)
    tool_calls: dict[str, int] = Field(default_factory=dict)
    run_dir: str = ""
    error: str | None = None
    # True when cheap recon failed to localize and we promoted the analyze stage mid-run
    # (bounded escalation). Lets the subset report measure how often the escalation fires.
    escalated: bool = False
