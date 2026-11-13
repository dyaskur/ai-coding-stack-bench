"""Shared enumerations used across configuration, metrics and results."""

from enum import Enum


class ToolingMode(str, Enum):
    """How much tooling an agent may use. Results from different modes are never mixed."""

    STANDARD = "standard"
    """All agents receive equivalent, basic tool access."""

    BEST_ECOSYSTEM = "best_ecosystem"
    """Agents may use ecosystem-specific tools, MCP servers and framework integrations."""


class TaskMode(str, Enum):
    """Whether the agent starts from an empty project or extends an existing one."""

    BUILD_FROM_SCRATCH = "build_from_scratch"
    EXTEND_EXISTING = "extend_existing"


class TrialStatus(str, Enum):
    """Lifecycle and outcome of a single trial. Failed trials are recorded, never dropped."""

    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    """Agent finished and every acceptance test passed."""
    FAILED = "failed"
    """Agent finished but acceptance tests failed."""
    AGENT_ERROR = "agent_error"
    """The agent crashed or exited non-zero."""
    TIMED_OUT = "timed_out"
    RATE_LIMITED = "rate_limited"
    """The agent stopped because the provider's usage/rate limit was hit."""
    INFRA_ERROR = "infra_error"
    """The platform (Docker, workspace, storage) failed. Not the agent's fault."""
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self not in (TrialStatus.PENDING, TrialStatus.RUNNING)


class AuthMode(str, Enum):
    """How an agent authenticates with its provider. Recorded on every trial."""

    SUBSCRIPTION = "subscription"
    """A consumer/team subscription login (OAuth token or login file).

    No per-run cost is billed, so cost is recorded as unavailable. Usage limits
    are shared across runs, so trials run sequentially and limit hits are flagged.
    """

    API_KEY = "api_key"
    """Pay-as-you-go API key. Cost is recorded when the agent reports it."""


class ComparisonDimension(str, Enum):
    """A dimension results can be grouped and compared by (the dashboard's "compare by")."""

    AGENT = "agent"
    MODEL = "model"
    """The trial's resolved model."""
    AGENT_MODEL = "agent_model"
    """Agent + resolved model: the default unit for model comparisons."""
    STACK = "stack"
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    TASK = "task"


class MetricAvailability(str, Enum):
    """Distinguishes a real value from 'not reported' and from 'does not apply'.

    A metric with value ``0`` and availability ``AVAILABLE`` means the agent really
    reported zero. ``UNAVAILABLE`` means nobody reported it; it must never be
    displayed or aggregated as zero.
    """

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class MetricSource(str, Enum):
    """Where a metric value came from."""

    AGENT_REPORTED = "agent_reported"
    """Taken from the agent's own output (e.g. token usage JSON)."""
    MEASURED = "measured"
    """Measured by the platform (e.g. wall-clock time, test counts)."""
    DERIVED = "derived"
    """Computed from other metrics by a documented formula (never an estimate)."""
