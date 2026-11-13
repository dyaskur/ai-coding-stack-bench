"""Experiment configuration: what a single ``benchmark run`` executes."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from ai_benchmark.config._base import SLUG_PATTERN, StrictModel
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.task import Timeouts
from ai_benchmark.models.enums import AuthMode, ToolingMode


class AgentSelection(StrictModel):
    """One agent/model combination to benchmark."""

    agent: str = Field(pattern=SLUG_PATTERN, description="Registered adapter name.")
    model: str | None = Field(default=None, description="None = the agent's own default model.")
    reasoning_effort: str | None = None
    options: dict[str, Any] = Field(
        default_factory=dict, description="Adapter-specific options, validated by the adapter."
    )
    auth: AgentAuthConfig | None = Field(
        default=None,
        description="Override the adapter's default credentials for the experiment's auth mode.",
    )

    @classmethod
    def parse(cls, spec: str) -> AgentSelection:
        """Parse the CLI shorthand ``agent`` or ``agent:model``."""
        agent, _, model = spec.strip().partition(":")
        return cls(agent=agent, model=model or None)

    @property
    def key(self) -> str:
        return f"{self.agent}:{self.model}" if self.model else self.agent


class McpServerConfig(StrictModel):
    name: str
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_transport(self) -> McpServerConfig:
        if (self.command is None) == (self.url is None):
            raise ValueError("an MCP server needs exactly one of 'command' or 'url'")
        return self


class ToolingConfig(StrictModel):
    mode: ToolingMode = ToolingMode.STANDARD
    enabled_tools: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _standard_has_no_mcp(self) -> ToolingConfig:
        if self.mode is ToolingMode.STANDARD and self.mcp_servers:
            raise ValueError("MCP servers are only allowed in 'best_ecosystem' tooling mode")
        return self


class ExperimentConfig(StrictModel):
    """A full experiment matrix: agents x stacks x trials for one task."""

    task: str = Field(pattern=SLUG_PATTERN)
    agents: list[AgentSelection] = Field(min_length=1)
    stacks: list[str] = Field(min_length=1)
    trials: int | None = Field(default=None, ge=1, description="None = the task's default.")
    tooling: ToolingConfig = Field(default_factory=ToolingConfig)
    auth_mode: AuthMode = Field(
        default=AuthMode.SUBSCRIPTION,
        description="Applies to every agent in the run, so results stay comparable.",
    )
    timeouts: Timeouts | None = Field(
        default=None, description="Overrides the task's timeouts for every trial."
    )
    randomize_order: bool = True
    seed: int | None = Field(default=None, description="None = a random seed, recorded in results.")
    max_parallel: int = Field(
        default=1, ge=1, le=1, description="Only sequential execution is supported for now."
    )
    stop_on_infra_error: bool = Field(
        default=False, description="Abort the run when the platform itself fails."
    )
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("stacks")
    @classmethod
    def _unique_stacks(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("stacks must be unique")
        return v

    @field_validator("agents")
    @classmethod
    def _unique_agents(cls, v: list[AgentSelection]) -> list[AgentSelection]:
        keys = [a.key for a in v]
        if len(set(keys)) != len(keys):
            raise ValueError("agent/model combinations must be unique")
        return v

    @model_validator(mode="after")
    def _single_auth_mode(self) -> ExperimentConfig:
        mixed = [a.key for a in self.agents if a.auth and a.auth.mode is not self.auth_mode]
        if mixed:
            raise ValueError(
                f"agents {', '.join(mixed)} override auth with a different mode than "
                f"auth_mode '{self.auth_mode.value}'; run each auth mode as a separate experiment"
            )
        return self
