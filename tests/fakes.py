"""Test doubles that implement the public interfaces without touching the core."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_benchmark.agents import (
    AgentAdapter,
    AgentAvailability,
    AgentCapabilities,
    AgentImageLayer,
    AgentRunResult,
    AgentTask,
    CommandRunner,
)
from ai_benchmark.config import AgentAuthConfig
from ai_benchmark.environment import CommandResult
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models import AuthMode, Metric, RateLimitEvent, UsageMetrics

FAKE_TOKEN_ENV = "FAKE_AGENT_TOKEN"
FAKE_API_KEY_ENV = "FAKE_AGENT_API_KEY"


class FakeAdapter(AgentAdapter):
    name = "fake"
    display_name = "Fake Agent"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(reports_rate_limits=True)

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent="fake", cli_version="1.0.0", install_commands=["true"], requires=["curl"]
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            return AgentAuthConfig(mode=mode, env=[FAKE_TOKEN_ENV])
        return AgentAuthConfig(mode=mode, env=[FAKE_API_KEY_ENV])

    def get_version(self, runner: CommandRunner) -> str | None:
        return runner.run(["fake", "--version"], timeout_seconds=10).stdout.strip() or None

    def check_availability(self, runner: CommandRunner) -> AgentAvailability:
        return AgentAvailability(installed=True, version=self.get_version(runner))

    def build_command(self, task: AgentTask) -> list[str]:
        return ["fake", "--prompt", task.prompt]

    def run(self, task: AgentTask, runner: CommandRunner) -> AgentRunResult:
        start = datetime.now(timezone.utc)
        res = runner.run(
            self.build_command(task), timeout_seconds=task.timeout_seconds, workdir=task.workdir
        )
        return AgentRunResult(
            exit_code=res.exit_code,
            started_at=start,
            finished_at=datetime.now(timezone.utc),
            rate_limit_events=self.detect_rate_limits(res.stdout, res.stderr, {}),
        )

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        return UsageMetrics(), Metric[str].unavailable()

    def detect_rate_limits(
        self, stdout: str, stderr: str, raw: dict[str, Any]
    ) -> list[RateLimitEvent]:
        if "usage limit" in stderr:
            return [RateLimitEvent(message="usage limit", fatal=True)]
        return []


class ApiKeyOnlyAdapter(FakeAdapter):
    name = "apikey-only"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(supported_auth_modes=(AuthMode.API_KEY,))

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is not AuthMode.API_KEY:
            raise ConfigurationError("unsupported")
        return super().default_auth(mode)


class ScriptedRunner:
    """A CommandRunner returning canned results and recording calls."""

    def __init__(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self._result = (stdout, stderr, exit_code)

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> CommandResult:
        self.calls.append(list(command))
        out, err, code = self._result
        return CommandResult(
            command=list(command), exit_code=code, stdout=out, stderr=err, duration_seconds=0.0
        )
