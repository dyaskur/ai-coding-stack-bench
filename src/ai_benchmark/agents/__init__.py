"""Agent adapter interface and registry. Concrete adapters arrive in Phase 2."""

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
