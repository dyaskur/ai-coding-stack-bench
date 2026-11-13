"""Result storage interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from ai_benchmark.models.enums import AuthMode, ToolingMode, TrialStatus
from ai_benchmark.models.results import RunManifest, TrialResult


class ResultFilter(BaseModel):
    """Filters applied when reading trials. ``None`` means 'any'."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str | None = None
    agent: str | None = None
    model: str | None = None
    stack: str | None = None
    language: str | None = None
    framework: str | None = None
    task: str | None = None
    tooling_mode: ToolingMode | None = None
    status: TrialStatus | None = None
    auth_mode: AuthMode | None = None
    rate_limited: bool | None = None

    def matches(self, r: TrialResult) -> bool:
        checks = (
            (self.run_id, r.run_id),
            (self.agent, r.agent.name),
            (self.model, r.agent.requested_model),
            (self.stack, r.environment.stack),
            (self.language, r.environment.language),
            (self.framework, r.environment.framework),
            (self.task, r.task_id),
            (self.tooling_mode, r.tooling.mode),
            (self.status, r.status),
            (self.auth_mode, r.agent.auth_mode),
            (self.rate_limited, r.rate_limited),
        )
        return all(want is None or want == have for want, have in checks)


class ResultStore(ABC):
    @abstractmethod
    def save_manifest(self, manifest: RunManifest) -> None: ...

    @abstractmethod
    def load_manifest(self, run_id: str) -> RunManifest: ...

    @abstractmethod
    def list_runs(self) -> list[RunManifest]:
        """All runs, newest first."""

    @abstractmethod
    def save_trial(self, result: TrialResult) -> None:
        """Insert or overwrite a trial result (written as soon as it finishes)."""

    @abstractmethod
    def iter_trials(self, filters: ResultFilter | None = None) -> Iterator[TrialResult]: ...
