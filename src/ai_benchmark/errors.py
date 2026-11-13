"""Exception hierarchy for the benchmark platform.

Every error raised by the platform derives from :class:`BenchmarkError` so the
CLI can report failures cleanly without swallowing unexpected bugs.
"""


class BenchmarkError(Exception):
    """Base class for all platform errors."""


class ConfigurationError(BenchmarkError):
    """A task, stack, experiment or settings file is missing or invalid."""


class AgentNotFoundError(BenchmarkError):
    """No adapter is registered under the requested agent name."""


class AgentUnavailableError(BenchmarkError):
    """The agent CLI is not installed, not authenticated or otherwise unusable."""


class AgentExecutionError(BenchmarkError):
    """The agent could not be launched or its output could not be collected."""


class ExecutionEnvironmentError(BenchmarkError):
    """The isolated execution environment could not be prepared or used."""


class ResultStoreError(BenchmarkError):
    """Results could not be persisted or loaded."""


class PlanningError(BenchmarkError):
    """An experiment configuration cannot be expanded into a valid plan."""
