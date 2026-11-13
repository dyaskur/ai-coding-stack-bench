"""Result records persisted by the result store."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_benchmark.models.enums import AuthMode, ComparisonDimension, ToolingMode, TrialStatus
from ai_benchmark.models.metrics import Metric, TrialMetrics


class AgentRecord(BaseModel):
    """Which agent ran, as observed at run time."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str | None = None
    requested_model: str | None = None
    reported_model: Metric[str] = Field(default_factory=Metric[str])
    reasoning_effort: str | None = None
    auth_mode: AuthMode
    resolved_model: str | None = Field(
        default=None,
        description="The model used for comparisons: reported model if available, "
        "else the requested one. None only if neither is known.",
    )

    @staticmethod
    def resolve_model(requested: str | None, reported: Metric[str]) -> str | None:
        if reported.is_available and reported.value:
            return reported.value
        return requested

    @property
    def model_label(self) -> str:
        return self.resolved_model or f"{self.name}-default"

    @property
    def agent_model(self) -> str:
        return f"{self.name}:{self.model_label}"


class RateLimitEvent(BaseModel):
    """A provider usage/rate limit observed during a trial."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(description="The agent's own wording, trimmed. Never contains secrets.")
    detected_at: datetime | None = None
    retry_after_seconds: float | None = None
    fatal: bool = Field(default=False, description="True if the agent stopped because of it.")


class EnvironmentRecord(BaseModel):
    """The execution environment a trial ran in, for reproducibility."""

    model_config = ConfigDict(extra="forbid")

    stack: str
    language: str
    language_version: str
    framework: str
    framework_version: str
    image_ref: str | None = None
    image_digest: str | None = None
    agent_image_ref: str | None = Field(
        default=None, description="Stack image plus the agent CLI layer the agent ran in."
    )
    services: list[str] = Field(default_factory=list)


class ToolingRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: ToolingMode
    enabled_tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)


class TrialResult(BaseModel):
    """The outcome of one (agent, model, stack, task, trial index) execution."""

    model_config = ConfigDict(extra="forbid")

    trial_id: str
    run_id: str
    task_id: str
    task_version: str
    trial_index: int = Field(ge=0)
    execution_order: int = Field(ge=0, description="Position in the randomized run order.")
    status: TrialStatus = TrialStatus.PENDING
    agent: AgentRecord
    environment: EnvironmentRecord
    tooling: ToolingRecord
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: TrialMetrics = Field(default_factory=TrialMetrics)
    workspace_git_commit: str | None = Field(
        default=None, description="Commit of the agent's final workspace state."
    )
    rate_limit_events: list[RateLimitEvent] = Field(
        default_factory=list,
        description="Limit hits. Trials with any are flagged so their timings can be excluded.",
    )
    log_files: dict[str, str] = Field(
        default_factory=dict, description="Name -> path of raw logs (agent stdout/stderr, tests)."
    )
    error: str | None = None
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Unmodified provider-specific data."
    )

    @property
    def rate_limited(self) -> bool:
        return bool(self.rate_limit_events)

    def dimension_value(self, dimension: ComparisonDimension) -> str:
        """This trial's value on a comparison dimension (used for grouping)."""
        values = {
            ComparisonDimension.AGENT: self.agent.name,
            ComparisonDimension.MODEL: self.agent.model_label,
            ComparisonDimension.AGENT_MODEL: self.agent.agent_model,
            ComparisonDimension.STACK: self.environment.stack,
            ComparisonDimension.LANGUAGE: self.environment.language,
            ComparisonDimension.FRAMEWORK: self.environment.framework,
            ComparisonDimension.TASK: self.task_id,
        }
        return values[dimension]

    def comparability_key(self) -> tuple[str, str, str, str]:
        """Trials can only be compared when these match."""
        return (
            self.task_id,
            self.task_version,
            self.tooling.mode.value,
            self.agent.auth_mode.value,
        )


class RunManifest(BaseModel):
    """Metadata for one ``benchmark run`` invocation (a set of trials)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: datetime
    platform_version: str
    platform_git_commit: str | None = None
    seed: int
    auth_mode: AuthMode
    experiment: dict[str, Any] = Field(description="Snapshot of the resolved ExperimentConfig.")
    task_snapshot: dict[str, Any] = Field(description="Snapshot of the BenchmarkSpec used.")
    trial_ids: list[str] = Field(default_factory=list)
    host: dict[str, str] = Field(default_factory=dict)
