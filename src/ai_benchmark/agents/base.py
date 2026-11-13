"""The provider-independent agent adapter contract.

Each coding agent (Codex CLI, Claude Code, OpenCode, ...) is integrated by
subclassing :class:`AgentAdapter` and registering it (see ``registry.py``).
The orchestrator only talks to this interface, so it contains no
agent-specific logic.

Execution model: the agent CLI runs *inside* the trial's stack container
(stack image + the adapter's :class:`AgentImageLayer`). Adapters therefore
never spawn host processes; every command goes through the
:class:`CommandRunner` they are handed.

Rules every adapter must follow:

* Never assume another agent's CLI flags, JSON format or accounting.
* Report a metric the agent does not expose as unavailable (``Metric.unavailable``),
  never as zero and never estimated.
* Preserve the agent's raw stdout/stderr on disk and raw structured output in
  ``AgentRunResult.raw``.
* Never log or return credential values.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.experiment import ToolingConfig
from ai_benchmark.environment.base import AgentImageLayer, CommandRunner
from ai_benchmark.models.enums import AuthMode
from ai_benchmark.models.metrics import Metric, UsageMetrics
from ai_benchmark.models.results import RateLimitEvent


class AgentCapabilities(BaseModel):
    """What an adapter can do and report. Used for fairness checks and report caveats."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    structured_output: bool = False
    """The CLI emits machine-readable (e.g. JSON/JSONL) output."""
    reports_token_usage: bool = False
    reports_cost: bool = False
    reports_turns: bool = False
    reports_rate_limits: bool = False
    """Limit hits can be recognised in the agent's output."""
    supports_model_selection: bool = True
    supports_reasoning_effort: bool = False
    supports_mcp: bool = False
    supports_non_interactive: bool = True
    supported_auth_modes: tuple[AuthMode, ...] = (AuthMode.SUBSCRIPTION, AuthMode.API_KEY)
    token_accounting_scheme: str | None = None
    """Tokenizer/provider family, e.g. 'openai' or 'anthropic'.

    Token counts are only comparable within one scheme.
    """


class AgentAvailability(BaseModel):
    """Result of a pre-flight check, run inside the agent image."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    installed: bool
    authenticated: bool | None = Field(
        default=None, description="None when authentication cannot be verified without a run."
    )
    version: str | None = None
    executable: str | None = None
    message: str | None = None

    @property
    def ready(self) -> bool:
        return self.installed and self.authenticated is not False


class AgentTask(BaseModel):
    """Everything an adapter needs to execute one coding task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    prompt: str
    workdir: str = Field(description="Workspace path inside the container, e.g. /workspace.")
    log_dir: Path = Field(description="Host directory for raw stdout/stderr/transcripts.")
    timeout_seconds: int = Field(gt=0)
    auth_mode: AuthMode
    model: str | None = None
    reasoning_effort: str | None = None
    tooling: ToolingConfig = Field(default_factory=ToolingConfig)
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Non-secret env vars. Credentials are injected separately.",
    )
    options: dict[str, Any] = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    """Normalized outcome of one agent execution, plus the raw provider data."""

    model_config = ConfigDict(extra="forbid")

    exit_code: int | None = Field(description="None if the process never started or was killed.")
    timed_out: bool = False
    started_at: datetime
    finished_at: datetime
    command: list[str] = Field(default_factory=list, description="Exact argv, secrets redacted.")
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    agent_version: str | None = None
    reported_model: Metric[str] = Field(default_factory=Metric[str])
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    rate_limit_events: list[RateLimitEvent] = Field(default_factory=list)
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Provider-native structured output, unmodified."
    )
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    @property
    def stopped_by_rate_limit(self) -> bool:
        return any(e.fatal for e in self.rate_limit_events)

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class AgentAdapter(ABC):
    """Base class for all coding-agent integrations."""

    name: ClassVar[str]
    """Stable identifier used on the CLI and in results, e.g. ``"codex"``."""
    display_name: ClassVar[str]

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options: dict[str, Any] = dict(options or {})

    # -- static description -------------------------------------------------

    @abstractmethod
    def capabilities(self) -> AgentCapabilities:
        """Static description of what this agent supports and reports."""

    @abstractmethod
    def image_layer(self) -> AgentImageLayer:
        """How to install this agent's CLI (pinned version) on top of a stack image."""

    @abstractmethod
    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        """Default host credentials for ``mode`` (env var names / file paths, never values).

        Raise :class:`~ai_benchmark.errors.ConfigurationError` if the mode is unsupported.
        """

    # -- pre-flight (run inside the agent image) -----------------------------

    @abstractmethod
    def get_version(self, runner: CommandRunner) -> str | None:
        """The agent CLI's version string, or None if it cannot be determined."""

    @abstractmethod
    def check_availability(self, runner: CommandRunner) -> AgentAvailability:
        """Installed, authenticated and usable. Must not run a coding task."""

    # -- execution ----------------------------------------------------------

    @abstractmethod
    def build_command(self, task: AgentTask) -> list[str]:
        """The argv run inside the container. Separate from :meth:`run` for dry-runs and tests."""

    @abstractmethod
    def run(self, task: AgentTask, runner: CommandRunner) -> AgentRunResult:
        """Execute ``task`` non-interactively in ``task.workdir`` and collect the result.

        Implementations must enforce ``task.timeout_seconds``, stream raw
        stdout/stderr to files under ``task.log_dir``, detect rate-limit hits
        (:meth:`detect_rate_limits`), and return a result even when the agent
        fails (populate ``exit_code``/``error`` rather than raising). Raise
        :class:`~ai_benchmark.errors.AgentExecutionError` only when the agent
        could not be launched at all.
        """

    @abstractmethod
    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        """Normalize provider-native output into usage metrics and the reported model.

        Report cost exactly as the agent does; the orchestrator applies the
        auth-mode policy (e.g. no billed cost under a subscription).
        """

    @abstractmethod
    def detect_rate_limits(
        self, stdout: str, stderr: str, raw: dict[str, Any]
    ) -> list[RateLimitEvent]:
        """Recognise usage/rate-limit messages in this agent's own output format."""

    # -- helpers ------------------------------------------------------------

    def validate_options(self, options: dict[str, Any]) -> None:  # noqa: B027
        """Reject unknown or invalid adapter-specific options. Default: accept all."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
