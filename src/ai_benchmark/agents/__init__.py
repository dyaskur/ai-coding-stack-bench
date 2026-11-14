"""Agent adapter interface, registry and the built-in adapters."""

from ai_benchmark.agents.base import (
    AgentAdapter,
    AgentAvailability,
    AgentCapabilities,
    AgentRunResult,
    AgentTask,
)
from ai_benchmark.agents.registry import AgentRegistry, default_registry, register_agent
from ai_benchmark.environment.base import AgentImageLayer, CommandRunner

__all__ = [
    "AgentAdapter",
    "AgentAvailability",
    "AgentCapabilities",
    "AgentImageLayer",
    "AgentRegistry",
    "AgentRunResult",
    "AgentTask",
    "CommandRunner",
    "default_registry",
    "register_agent",
]

# Built-in adapters register themselves in ``default_registry`` on import.
from ai_benchmark.agents import claude_code, codex, opencode  # noqa: E402, F401
