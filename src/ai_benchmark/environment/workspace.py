"""Per-trial workspace directories on the host."""

from __future__ import annotations

import shutil
from pathlib import Path

from ai_benchmark.config.task import BenchmarkSpec
from ai_benchmark.errors import ExecutionEnvironmentError


class WorkspaceManager:
    """Creates a fresh, isolated directory for every trial.

    Layout: ``<root>/<run_id>/<trial_id>/{workspace,logs}``.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def trial_dir(self, run_id: str, trial_id: str) -> Path:
        return self.root / run_id / trial_id

    def create(self, run_id: str, trial_id: str, task: BenchmarkSpec, stack_id: str) -> Path:
        """Create the workspace and seed it with the task's initial state for ``stack_id``.

        If the template directory has a sub-directory named after the stack,
        only that sub-directory is copied; otherwise the whole template is.
        """
        base = self.trial_dir(run_id, trial_id)
        workspace = base / "workspace"
        if workspace.exists():
            raise ExecutionEnvironmentError(f"workspace already exists: {workspace}")
        (base / "logs").mkdir(parents=True, exist_ok=True)

        template = task.initial_state.template
        if template is None:
            workspace.mkdir(parents=True)
            return workspace
        if not template.is_absolute():
            if task.source_dir is None:
                raise ExecutionEnvironmentError(f"cannot resolve relative template {template}")
            template = task.source_dir / template
        source = template / stack_id if (template / stack_id).is_dir() else template
        if not source.is_dir():
            raise ExecutionEnvironmentError(f"initial-state template not found: {source}")
        shutil.copytree(source, workspace, symlinks=True)
        return workspace

    def log_dir(self, run_id: str, trial_id: str) -> Path:
        return self.trial_dir(run_id, trial_id) / "logs"

    def remove(self, run_id: str, trial_id: str) -> None:
        shutil.rmtree(self.trial_dir(run_id, trial_id) / "workspace", ignore_errors=True)
