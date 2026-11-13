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
from ai_benchmark.config.stack import StackSpec
from ai_benchmark.environment import (
    CommandResult,
    EnvironmentHandle,
    ExecutionEnvironment,
    ImageInfo,
    Mount,
)
from ai_benchmark.errors import ConfigurationError, ExecutionEnvironmentError
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


JUNIT_ALL_PASS = (
    '<testsuites><testsuite name="s" tests="2">'
    '<testcase classname="t" name="a"/><testcase classname="t" name="b"/>'
    "</testsuite></testsuites>"
)
JUNIT_ONE_FAIL = (
    '<testsuites><testsuite name="s" tests="2">'
    '<testcase classname="t" name="a"/>'
    '<testcase classname="t" name="b"><failure message="boom"/></testcase>'
    "</testsuite></testsuites>"
)


class FakeEnvironment(ExecutionEnvironment):
    """In-memory ExecutionEnvironment that records calls and scripts command output."""

    def __init__(
        self,
        junit: str | None = JUNIT_ALL_PASS,
        agent_stderr: str = "",
        agent_exit: int = 0,
        fail_on: str | None = None,
    ) -> None:
        self.junit = junit
        self.agent_stderr = agent_stderr
        self.agent_exit = agent_exit
        self.fail_on = fail_on
        self.calls: list[tuple[str, Any]] = []
        self.destroyed: list[str] = []

    def _maybe_fail(self, step: str) -> None:
        self.calls.append((step, None))
        if self.fail_on == step:
            raise ExecutionEnvironmentError(f"{step} failed")

    def prepare_image(self, stack: StackSpec) -> ImageInfo:
        self._maybe_fail("prepare_image")
        return ImageInfo(stack_id=stack.id, reference=f"{stack.image}", digest="sha256:stack")

    def prepare_agent_image(self, stack_image: ImageInfo, layer: AgentImageLayer) -> ImageInfo:
        self._maybe_fail("prepare_agent_image")
        return ImageInfo(stack_id=stack_image.stack_id, reference=f"agent-{layer.tag_suffix}")

    def build_image(self, name: str, context: Path, dockerfile: Path | None = None) -> ImageInfo:
        self._maybe_fail("build_image")
        return ImageInfo(stack_id=name, reference=f"tester-{name}")

    def create(
        self,
        stack: StackSpec,
        image: ImageInfo,
        workspace: Path,
        trial_id: str,
        auth: AgentAuthConfig | None = None,
    ) -> EnvironmentHandle:
        self._maybe_fail("create")
        self.calls.append(("auth", auth))
        return EnvironmentHandle(
            environment_id=trial_id, trial_id=trial_id, stack_id=stack.id, image=image,
            workspace=workspace, container_id=f"c-{trial_id}", network_id=f"n-{trial_id}",
        )  # fmt: skip

    def start(self, handle: EnvironmentHandle, timeout_seconds: int) -> None:
        self._maybe_fail("start")

    def exec(
        self,
        handle: EnvironmentHandle,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> CommandResult:
        cmd = list(command)
        self.calls.append(("exec", cmd))
        out, err, code = "", "", 0
        script = cmd[-1] if cmd[:2] == ["sh", "-c"] else ""
        if "rev-parse HEAD" in script:
            out = "abc123\n"
        elif script.startswith("git diff --numstat"):
            out = "10\t2\tapp.py\n5\t0\tstart.sh\n-\t-\tlogo.png\n"
        elif cmd and cmd[0] == "fake":
            if cmd[1:2] == ["--version"]:
                out = "fake 1.0.0\n"
            else:
                err, code = self.agent_stderr, self.agent_exit
        for path, text in ((stdout_path, out), (stderr_path, err)):
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
        return CommandResult(
            command=cmd, exit_code=code, stdout=out, stderr=err, duration_seconds=0.01
        )

    def exec_detached(
        self,
        handle: EnvironmentHandle,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
    ) -> None:
        self.calls.append(("exec_detached", list(command)))

    def restart(self, handle: EnvironmentHandle, timeout_seconds: int) -> None:
        self.calls.append(("restart", None))

    def run_job(
        self,
        handle: EnvironmentHandle,
        image: ImageInfo,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        mounts: Sequence[Mount] = (),
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> CommandResult:
        self.calls.append(("run_job", {"image": image.reference, "env": dict(env or {})}))
        results = next(m.source for m in mounts if m.target == "/results")
        if self.junit is not None:
            (results / "junit.xml").write_text(self.junit)
        return CommandResult(command=list(command), exit_code=0, duration_seconds=0.1)

    def copy_into(self, handle: EnvironmentHandle, source: Path, destination: str) -> None:
        self.calls.append(("copy_into", destination))

    def collect_logs(self, handle: EnvironmentHandle, destination: Path) -> dict[str, Path]:
        return {}

    def destroy(self, handle: EnvironmentHandle) -> None:
        self.destroyed.append(handle.trial_id)
