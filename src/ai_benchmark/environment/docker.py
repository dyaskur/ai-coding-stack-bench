"""Docker-backed execution environment (skeleton).

Phase 1 defines the class and its configuration only. The Docker API calls are
implemented in Phase 2.

Security model (to be enforced by the implementation):

* the agent CLI runs inside the stack container, built as a cached
  ``<stack image> + agent layer`` image; there is no separate agent container;
* containers run as a non-root user with resource limits (CPU, memory, PIDs);
* credentials (subscription tokens, login files, API keys) are injected at
  ``create`` time only, never baked into an image or written to logs;
* each trial gets its own user-defined network shared only with its services;
* only the trial workspace is bind-mounted; the Docker socket is never mounted;
* all resources carry ``<label_prefix>.run_id`` / ``.trial_id`` labels so a
  crashed run can be cleaned up reliably.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.settings import DockerSettings
from ai_benchmark.config.stack import StackSpec
from ai_benchmark.environment.base import (
    AgentImageLayer,
    CommandResult,
    EnvironmentHandle,
    ExecutionEnvironment,
    ImageInfo,
)

_PHASE_2 = "Docker execution is not implemented yet (planned for Phase 2)"


class DockerExecutionEnvironment(ExecutionEnvironment):
    def __init__(self, settings: DockerSettings, run_id: str | None = None) -> None:
        self.settings = settings
        self.run_id = run_id

    def labels_for(self, trial_id: str) -> dict[str, str]:
        """Labels applied to every container, network and volume of a trial."""
        prefix = self.settings.label_prefix
        labels = {f"{prefix}.managed": "true", f"{prefix}.trial_id": trial_id}
        if self.run_id:
            labels[f"{prefix}.run_id"] = self.run_id
        return labels

    def check_daemon(self) -> None:
        """Verify the Docker daemon is reachable and usable."""
        raise NotImplementedError(_PHASE_2)

    def prepare_image(self, stack: StackSpec) -> ImageInfo:
        raise NotImplementedError(_PHASE_2)

    def prepare_agent_image(self, stack_image: ImageInfo, layer: AgentImageLayer) -> ImageInfo:
        raise NotImplementedError(_PHASE_2)

    def create(
        self,
        stack: StackSpec,
        image: ImageInfo,
        workspace: Path,
        trial_id: str,
        auth: AgentAuthConfig | None = None,
    ) -> EnvironmentHandle:
        raise NotImplementedError(_PHASE_2)

    def start(self, handle: EnvironmentHandle, timeout_seconds: int) -> None:
        raise NotImplementedError(_PHASE_2)

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
        raise NotImplementedError(_PHASE_2)

    def copy_into(self, handle: EnvironmentHandle, source: Path, destination: str) -> None:
        raise NotImplementedError(_PHASE_2)

    def collect_logs(self, handle: EnvironmentHandle, destination: Path) -> dict[str, Path]:
        raise NotImplementedError(_PHASE_2)

    def destroy(self, handle: EnvironmentHandle) -> None:
        raise NotImplementedError(_PHASE_2)

    def cleanup_stale(self) -> int:
        """Remove resources left behind by crashed runs (matched by label). Returns the count."""
        raise NotImplementedError(_PHASE_2)
