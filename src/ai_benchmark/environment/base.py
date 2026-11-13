"""Execution environment interface.

An execution environment provides an isolated, reproducible runtime for one
stack (language + framework + services). The agent CLI runs *inside* this
container (the stack image plus a thin per-agent layer), so builds and tests
the agent triggers use the stack's pinned runtimes, never the host's, and the
agent never needs the Docker socket.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.stack import StackSpec


class ImageInfo(BaseModel):
    """A resolved, ready-to-run image."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stack_id: str
    reference: str
    digest: str | None = Field(default=None, description="Content digest, for reproducibility.")
    built_locally: bool = False


class AgentImageLayer(BaseModel):
    """How to add one agent CLI on top of any stack image.

    Provided by the adapter, built by the environment as
    ``<stack image> + layer -> ai-benchmark/<stack>-<agent>:<content hash>`` and cached.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent: str
    cli_version: str = Field(description="Pinned CLI version; never 'latest'.")
    install_commands: list[str] = Field(
        description="Shell commands (Dockerfile RUN lines) that install the CLI as root."
    )
    requires: list[str] = Field(
        default_factory=list,
        description="Base tools the commands need (e.g. 'curl', 'nodejs>=20'); "
        "installed first if the stack image lacks them.",
    )
    env: dict[str, str] = Field(default_factory=dict)
    home_dir: str = Field(default="/home/agent", description="HOME for the non-root agent user.")

    @property
    def tag_suffix(self) -> str:
        return f"{self.agent}-{self.cli_version}"


class EnvironmentHandle(BaseModel):
    """A live environment for one trial. Opaque to everything except its environment."""

    model_config = ConfigDict(extra="forbid")

    environment_id: str
    trial_id: str
    stack_id: str
    image: ImageInfo
    agent: str | None = None
    workspace: Path = Field(description="Host path mounted at the stack's workdir.")
    container_id: str | None = None
    network_id: str | None = None
    service_container_ids: dict[str, str] = Field(default_factory=dict)


class CommandResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    command: list[str]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class CommandRunner(Protocol):
    """Runs a command in a trial's container. This is all an agent adapter can execute."""

    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> CommandResult: ...


class ExecutionEnvironment(ABC):
    """Lifecycle: ``prepare_image`` -> ``prepare_agent_image`` -> ``create`` -> ``start``
    -> ``exec``* -> ``destroy``.

    Implementations must guarantee:

    * one trial cannot observe or affect another (separate container, network, workspace);
    * only the trial workspace and the listed credential files are mounted from the
      host; never the Docker socket;
    * credential values are never written to logs, results or image layers;
    * ``destroy`` is idempotent and releases every resource, even after failures.
    """

    @abstractmethod
    def prepare_image(self, stack: StackSpec) -> ImageInfo:
        """Build or pull the stack's image (honouring the pull policy and cache)."""

    @abstractmethod
    def prepare_agent_image(self, stack_image: ImageInfo, layer: AgentImageLayer) -> ImageInfo:
        """Build (or reuse from cache) the stack image with the agent CLI layer on top."""

    @abstractmethod
    def create(
        self,
        stack: StackSpec,
        image: ImageInfo,
        workspace: Path,
        trial_id: str,
        auth: AgentAuthConfig | None = None,
    ) -> EnvironmentHandle:
        """Create (but do not start) the containers and network for one trial.

        ``auth`` env vars are read from the host at this point and injected
        into the container; ``auth`` files are copied or bind-mounted per
        their mode.
        """

    @abstractmethod
    def start(self, handle: EnvironmentHandle, timeout_seconds: int) -> None:
        """Start containers, wait for service health checks, run stack setup commands."""

    @abstractmethod
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
        """Run a command inside the stack container as the non-root user.

        When ``stdout_path``/``stderr_path`` are given, output is streamed to
        those host files (raw logs) as well as returned.
        """

    @abstractmethod
    def copy_into(self, handle: EnvironmentHandle, source: Path, destination: str) -> None:
        """Copy host files (e.g. the acceptance suite) into the container, read-only if possible."""

    @abstractmethod
    def collect_logs(self, handle: EnvironmentHandle, destination: Path) -> dict[str, Path]:
        """Write container and service logs under ``destination``; return name -> path."""

    @abstractmethod
    def destroy(self, handle: EnvironmentHandle) -> None:
        """Stop and remove everything ``create`` made. Must be idempotent."""

    def runner(self, handle: EnvironmentHandle) -> CommandRunner:
        """A :class:`CommandRunner` bound to one trial's container."""
        return ContainerCommandRunner(self, handle)

    @contextmanager
    def session(
        self,
        stack: StackSpec,
        workspace: Path,
        trial_id: str,
        start_timeout_seconds: int,
        layer: AgentImageLayer | None = None,
        auth: AgentAuthConfig | None = None,
    ) -> Iterator[EnvironmentHandle]:
        """Prepare images, then create, start and always destroy an environment around a block."""
        image = self.prepare_image(stack)
        if layer is not None:
            image = self.prepare_agent_image(image, layer)
        handle = self.create(stack, image, workspace, trial_id, auth)
        try:
            self.start(handle, start_timeout_seconds)
            yield handle
        finally:
            self.destroy(handle)


class ContainerCommandRunner:
    """Binds :meth:`ExecutionEnvironment.exec` to one handle."""

    def __init__(self, environment: ExecutionEnvironment, handle: EnvironmentHandle) -> None:
        self.environment = environment
        self.handle = handle

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
        return self.environment.exec(
            self.handle,
            command,
            timeout_seconds=timeout_seconds,
            env=env,
            workdir=workdir,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
