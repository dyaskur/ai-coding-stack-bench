"""Benchmark orchestrator (skeleton).

The orchestrator wires together the spec catalog, agent registry, execution
environment, workspace manager and result store. It only depends on their
interfaces and contains no agent- or stack-specific logic.

Phase 1 provides planning and the class shape. Trial execution and acceptance
testing are implemented in Phase 2.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field

from ai_benchmark.agents.base import AgentAdapter, AgentAvailability, AgentRunResult
from ai_benchmark.agents.registry import AgentRegistry
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.experiment import AgentSelection, ExperimentConfig
from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.config.stack import StackSpec
from ai_benchmark.config.task import BenchmarkSpec
from ai_benchmark.environment.base import CommandRunner, EnvironmentHandle, ExecutionEnvironment
from ai_benchmark.environment.workspace import WorkspaceManager
from ai_benchmark.models.enums import AuthMode
from ai_benchmark.models.metrics import QualityMetrics
from ai_benchmark.models.results import RunManifest, TrialResult
from ai_benchmark.orchestrator.plan import ExperimentPlan, PlannedTrial, build_plan
from ai_benchmark.orchestrator.timing import utcnow
from ai_benchmark.storage.base import ResultStore

log = logging.getLogger(__name__)

TrialCallback = Callable[[TrialResult], None]

_PHASE_2 = "trial execution is not implemented yet (planned for Phase 2)"


class PreflightIssue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    severity: str = Field(pattern="^(error|warning)$")
    subject: str
    message: str


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    total: int
    by_status: dict[str, int] = Field(default_factory=dict)
    trial_ids: list[str] = Field(default_factory=list)


def new_run_id() -> str:
    return f"{utcnow():%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


class BenchmarkOrchestrator:
    """Runs experiments sequentially (parallel execution is a later phase)."""

    def __init__(
        self,
        settings: PlatformSettings,
        catalog: SpecCatalog,
        registry: AgentRegistry,
        environment: ExecutionEnvironment,
        store: ResultStore,
        workspaces: WorkspaceManager | None = None,
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.registry = registry
        self.environment = environment
        self.store = store
        self.workspaces = workspaces or WorkspaceManager(settings.workspaces_dir)

    # -- planning (implemented) -------------------------------------------

    def plan(self, config: ExperimentConfig, run_id: str | None = None) -> ExperimentPlan:
        """Resolve the task and stacks and expand the config into ordered trials."""
        task = self.catalog.get_task(config.task)
        stacks = {s: self.catalog.get_stack(s) for s in config.stacks}
        return build_plan(config, task, stacks, run_id or new_run_id())

    # -- pre-flight --------------------------------------------------------

    def preflight(self, plan: ExperimentPlan) -> list[PreflightIssue]:
        """Check each agent is registered, supports the auth mode and has its credentials.

        Credentials are checked by name/path only. In-container availability
        and Docker checks come with Phase 2.
        """
        issues: list[PreflightIssue] = []
        mode = plan.auth_mode
        selections = {t.agent.key: t.agent for t in plan.trials}
        for key in sorted(selections):
            selection = selections[key]

            def error(message: str, subject: str = key) -> PreflightIssue:
                return PreflightIssue(severity="error", subject=subject, message=message)

            auth = selection.auth
            if selection.agent not in self.registry:
                issues.append(error("no adapter registered"))
            else:
                adapter = self.registry.create(selection.agent, selection.options)
                if mode not in adapter.capabilities().supported_auth_modes:
                    issues.append(error(f"does not support auth mode '{mode.value}'"))
                    continue
                auth = auth or adapter.default_auth(mode)
            if auth is not None:
                issues.extend(error(problem) for problem in auth.missing())
        defaults = sorted(k for k, s in selections.items() if s.model is None)
        if defaults:
            issues.append(
                PreflightIssue(
                    severity="warning",
                    subject="model",
                    message=f"no model set for {', '.join(defaults)}: the agent's default "
                    "model is used, which makes model comparisons less reliable",
                )
            )
        if mode is AuthMode.SUBSCRIPTION:
            issues.append(
                PreflightIssue(
                    severity="warning",
                    subject="auth",
                    message="subscription mode: cost is recorded as unavailable, and trials "
                    "that hit a usage limit are flagged",
                )
            )
        return issues

    def resolve_auth(self, selection: AgentSelection, mode: AuthMode) -> AgentAuthConfig:
        """The selection's explicit auth, else the adapter's default for ``mode``."""
        if selection.auth is not None:
            return selection.auth
        return self.registry.create(selection.agent, selection.options).default_auth(mode)

    def check_agent(self, adapter: AgentAdapter, runner: CommandRunner) -> AgentAvailability:
        """Run the adapter's availability check inside its agent image."""
        raise NotImplementedError(_PHASE_2)

    # -- execution (Phase 2) ----------------------------------------------

    def run(
        self, config: ExperimentConfig, on_trial_complete: TrialCallback | None = None
    ) -> RunSummary:
        """Plan, persist the manifest, execute every trial in order, and summarize.

        Every planned trial produces a stored ``TrialResult``; failures are
        recorded with their status, never skipped silently.
        """
        raise NotImplementedError(_PHASE_2)

    def build_manifest(self, plan: ExperimentPlan, config: ExperimentConfig) -> RunManifest:
        raise NotImplementedError(_PHASE_2)

    def run_trial(
        self, plan: ExperimentPlan, trial: PlannedTrial, task: BenchmarkSpec, stack: StackSpec
    ) -> TrialResult:
        """One trial, in order:

        fresh workspace -> stack image + agent layer -> container with credentials
        -> timed agent run (inside the container) -> rate-limit detection
        -> acceptance tests -> auth policy on metrics -> record.
        """
        raise NotImplementedError(_PHASE_2)

    def render_prompt(self, task: BenchmarkSpec, stack: StackSpec) -> str:
        """Build the agent prompt from the task template and stack facts."""
        return task.render_prompt(
            {
                "language": stack.language,
                "language_version": stack.language_version,
                "framework": stack.framework,
                "framework_version": stack.framework_version,
                "workdir": stack.workdir,
            }
        )

    def execute_agent(
        self,
        adapter: AgentAdapter,
        trial: PlannedTrial,
        handle: EnvironmentHandle,
        prompt: str,
        auth_mode: AuthMode,
    ) -> AgentRunResult:
        raise NotImplementedError(_PHASE_2)

    def run_acceptance_tests(
        self, task: BenchmarkSpec, handle: EnvironmentHandle
    ) -> QualityMetrics:
        raise NotImplementedError(_PHASE_2)

    def finalize_trial(self, result: TrialResult) -> None:
        """Persist the trial result and release its workspace unless kept."""
        raise NotImplementedError(_PHASE_2)
