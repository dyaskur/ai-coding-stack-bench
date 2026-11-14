"""Shared plumbing for adapters that drive a non-interactive agent CLI.

Subclasses describe the CLI (install, command line, output format); this base
runs it through the trial's :class:`CommandRunner`, keeps the raw output on
disk and turns it into an :class:`AgentRunResult`.
"""

from __future__ import annotations

import json
import re
from abc import abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from ai_benchmark.agents.base import (
    AgentAdapter,
    AgentAvailability,
    AgentRunResult,
    AgentTask,
)
from ai_benchmark.environment.base import CommandRunner
from ai_benchmark.models.enums import MetricSource
from ai_benchmark.models.metrics import Metric
from ai_benchmark.models.results import RateLimitEvent

AGENT_HOME = "/home/agent"

# Wording the three CLIs use when a provider usage or rate limit stops them.
# Matched case-insensitively against the agent's own error text only.
RATE_LIMIT_PATTERNS = (
    r"usage limit",
    r"usage_limit_reached",
    r"hit your (?:\w+ )*limit",
    r"rate[ _-]?limit",
    r"too many requests",
    r"\b429\b",
    r"quota exceeded",
    r"insufficient_quota",
)
_RATE_LIMIT_RE = re.compile("|".join(RATE_LIMIT_PATTERNS), re.IGNORECASE)


@dataclass
class ParsedOutput:
    """What an adapter extracted from its CLI's stdout."""

    raw: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    """The agent's own error messages, in order."""


def json_lines(text: str) -> Iterable[dict[str, Any]]:
    """Yield every line of ``text`` that is a JSON object; skip anything else."""
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def token_metric(value: int | None, note: str | None = None) -> Metric[int]:
    if value is None:
        return Metric[int].unavailable(note or "not reported by the agent", unit="tokens")
    return Metric[int].of(value, MetricSource.AGENT_REPORTED, unit="tokens", note=note)


def rate_limit_events(messages: Iterable[str], *, failed: bool) -> list[RateLimitEvent]:
    """One event per distinct limit message; fatal when the run failed."""
    seen: set[str] = set()
    events = []
    for message in messages:
        text = " ".join(message.split())[:500]
        if not text or text in seen or not _RATE_LIMIT_RE.search(text):
            continue
        seen.add(text)
        events.append(RateLimitEvent(message=text, fatal=failed))
    return events


class CliAgentAdapter(AgentAdapter):
    """Base for adapters whose agent is a CLI run once per trial."""

    executable: ClassVar[str]
    """Binary name on PATH inside the agent image."""
    cli_version: ClassVar[str]
    """Pinned CLI version installed into the agent image."""

    # -- subclass hooks -------------------------------------------------------

    @abstractmethod
    def parse_output(self, stdout: str) -> ParsedOutput:
        """Extract structured output and error messages from the CLI's stdout."""

    def stderr_errors(self, stderr: str) -> list[str]:
        """Lines of stderr worth checking for limit messages. Default: the last 20."""
        return [line for line in stderr.splitlines()[-20:] if line.strip()]

    # -- AgentAdapter ---------------------------------------------------------

    def get_version(self, runner: CommandRunner) -> str | None:
        res = runner.run([self.executable, "--version"], timeout_seconds=60)
        if not res.ok:
            return None
        match = re.search(r"\d+\.\d+\.\d+\S*", res.stdout)
        return match.group(0) if match else (res.stdout.strip() or None)

    def check_availability(self, runner: CommandRunner) -> AgentAvailability:
        version = self.get_version(runner)
        if version is None:
            return AgentAvailability(
                installed=False, message=f"'{self.executable} --version' failed in the image"
            )
        message = None
        if version != self.cli_version:
            message = f"expected {self.cli_version}, found {version}"
        # Checking credentials would cost a model call, so it is left to the trial.
        return AgentAvailability(
            installed=True, version=version, executable=self.executable, message=message
        )

    def run(self, task: AgentTask, runner: CommandRunner) -> AgentRunResult:
        stdout_path = task.log_dir / "agent.stdout.log"
        stderr_path = task.log_dir / "agent.stderr.log"
        command = self.build_command(task)
        started = datetime.now(timezone.utc)
        res = runner.run(
            command,
            timeout_seconds=task.timeout_seconds,
            env=task.env or None,
            workdir=task.workdir,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
        finished = datetime.now(timezone.utc)
        stdout = _read(stdout_path, res.stdout)
        stderr = _read(stderr_path, res.stderr)
        parsed = self.parse_output(stdout)
        usage, model = self.parse_usage(parsed.raw)
        failed = res.timed_out or res.exit_code != 0 or bool(parsed.errors)
        events = self.detect_rate_limits(stdout, stderr, parsed.raw)
        if not events:
            events = rate_limit_events([*parsed.errors, *self.stderr_errors(stderr)], failed=failed)
        error = parsed.errors[-1][:1000] if parsed.errors else None
        return AgentRunResult(
            exit_code=res.exit_code,
            timed_out=res.timed_out,
            started_at=started,
            finished_at=finished,
            command=self.redact(command, task),
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            reported_model=model,
            usage=usage,
            rate_limit_events=events,
            raw=parsed.raw,
            error=error,
        )

    def detect_rate_limits(
        self, stdout: str, stderr: str, raw: dict[str, Any]
    ) -> list[RateLimitEvent]:
        """Adapters override this when their output marks limits explicitly.

        Returning nothing falls back to matching the agent's error messages
        against :data:`RATE_LIMIT_PATTERNS`.
        """
        return []

    def redact(self, command: list[str], task: AgentTask) -> list[str]:
        """The argv as recorded in results: the (long) prompt is replaced by a marker."""
        return ["<prompt>" if arg == task.prompt else arg for arg in command]


def _read(path: Path, fallback: str) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return fallback
