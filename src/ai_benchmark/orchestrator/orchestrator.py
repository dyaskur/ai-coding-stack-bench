"""Benchmark orchestrator.

The orchestrator wires together the spec catalog, agent registry, execution
environment, workspace manager and result store. It only depends on their
interfaces and contains no agent- or stack-specific logic.

Trials run sequentially in the planned (randomized) order. Each one gets a
fresh workspace and a fresh container; the timer covers only the agent run;
acceptance tests run afterwards in a separate tester container.
"""

from __future__ import annotations

import logging
import os
import platform
import shlex
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ai_benchmark import __version__
from ai_benchmark.agents.base import AgentAdapter, AgentAvailability, AgentRunResult, AgentTask
from ai_benchmark.agents.registry import AgentRegistry
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.config.experiment import AgentSelection, ExperimentConfig
from ai_benchmark.config.loader import SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.config.stack import StackSpec
from ai_benchmark.config.task import BenchmarkSpec
from ai_benchmark.environment.base import (
    CommandRunner,
    EnvironmentHandle,
    ExecutionEnvironment,
    ImageInfo,
    Mount,
)
from ai_benchmark.environment.workspace import WorkspaceManager
from ai_benchmark.errors import (
    AgentExecutionError,
    ConfigurationError,
    ExecutionEnvironmentError,
    PlanningError,
)
from ai_benchmark.models.enums import AuthMode, MetricSource, TrialStatus
from ai_benchmark.models.metrics import Metric, QualityMetrics
from ai_benchmark.models.results import (
    AgentRecord,
    EnvironmentRecord,
    RunManifest,
    ToolingRecord,
    TrialResult,
)
from ai_benchmark.orchestrator.code_metrics import snapshot_baseline, snapshot_result
from ai_benchmark.orchestrator.junit import parse_junit
from ai_benchmark.orchestrator.plan import ExperimentPlan, PlannedTrial, build_plan
from ai_benchmark.orchestrator.policy import apply_auth_policy
from ai_benchmark.orchestrator.timing import Stopwatch, utcnow
from ai_benchmark.progress import AgentHeartbeat, format_duration
from ai_benchmark.storage.base import ResultStore

log = logging.getLogger(__name__)

TrialCallback = Callable[[TrialResult], None]


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

        Credentials are checked by name/path only, without reading values.
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
        return adapter.check_availability(runner)

    # -- execution --------------------------------------------------------

    def run(
        self,
        config: ExperimentConfig,
        on_trial_complete: TrialCallback | None = None,
        run_id: str | None = None,
    ) -> RunSummary:
        """Plan, persist the manifest, execute every trial in order, and summarize.

        Every planned trial produces a stored ``TrialResult``; failures are
        recorded with their status, never skipped silently.
        """
        plan = self.plan(config, run_id)
        errors = [i for i in self.preflight(plan) if i.severity == "error"]
        if errors:
            details = "; ".join(f"{i.subject}: {i.message}" for i in errors)
            raise PlanningError(f"pre-flight checks failed: {details}")
        task = self.catalog.get_task(plan.task_id)
        stacks = {s: self.catalog.get_stack(s) for s in config.stacks}
        manifest = self.build_manifest(plan, config, task)
        self.store.save_manifest(manifest)

        summary = RunSummary(run_id=plan.run_id, total=len(plan.trials))
        abort_reason: str | None = None
        for trial in plan.trials:
            if abort_reason is not None:
                result = self._skeleton(plan, trial, task, stacks[trial.stack_id], config)
                result.status = TrialStatus.CANCELLED
                result.error = abort_reason
            else:
                log.info(
                    "trial %d/%d %s (%s)",
                    trial.execution_order + 1,
                    len(plan.trials),
                    trial.trial_id,
                    trial.combination,
                )
                result = self.run_trial(plan, trial, task, stacks[trial.stack_id], config)
                if result.status is TrialStatus.INFRA_ERROR and config.stop_on_infra_error:
                    abort_reason = f"run aborted after infra error in {trial.trial_id}"
            self.store.save_trial(result)
            summary.trial_ids.append(result.trial_id)
            summary.by_status[result.status.value] = (
                summary.by_status.get(result.status.value, 0) + 1
            )
            if on_trial_complete is not None:
                on_trial_complete(result)
        return summary

    def build_manifest(
        self, plan: ExperimentPlan, config: ExperimentConfig, task: BenchmarkSpec
    ) -> RunManifest:
        return RunManifest(
            run_id=plan.run_id,
            created_at=utcnow(),
            platform_version=__version__,
            platform_git_commit=_platform_commit(),
            seed=plan.seed,
            auth_mode=plan.auth_mode,
            experiment=config.model_dump(mode="json"),
            task_snapshot=task.model_dump(mode="json"),
            trial_ids=[t.trial_id for t in plan.trials],
            host={
                "platform": platform.platform(),
                "python": platform.python_version(),
                "machine": platform.machine(),
            },
        )

    def _skeleton(
        self,
        plan: ExperimentPlan,
        trial: PlannedTrial,
        task: BenchmarkSpec,
        stack: StackSpec,
        config: ExperimentConfig,
    ) -> TrialResult:
        return TrialResult(
            trial_id=trial.trial_id,
            run_id=plan.run_id,
            task_id=task.id,
            task_version=task.version,
            trial_index=trial.trial_index,
            execution_order=trial.execution_order,
            status=TrialStatus.PENDING,
            agent=AgentRecord(
                name=trial.agent.agent,
                requested_model=trial.agent.model,
                reasoning_effort=trial.agent.reasoning_effort,
                auth_mode=plan.auth_mode,
                resolved_model=trial.agent.model,
            ),
            environment=EnvironmentRecord(
                stack=stack.id,
                language=stack.language,
                language_version=stack.language_version,
                framework=stack.framework,
                framework_version=stack.framework_version,
                services=[s.name for s in stack.services],
            ),
            tooling=ToolingRecord(
                mode=config.tooling.mode,
                enabled_tools=list(config.tooling.enabled_tools),
                mcp_servers=[m.name for m in config.tooling.mcp_servers],
            ),
        )

    def run_trial(
        self,
        plan: ExperimentPlan,
        trial: PlannedTrial,
        task: BenchmarkSpec,
        stack: StackSpec,
        config: ExperimentConfig,
    ) -> TrialResult:
        """One trial, in order:

        fresh workspace -> stack image + agent layer -> container with credentials
        -> timed agent run (inside the container) -> rate-limit detection
        -> acceptance tests -> auth policy on metrics -> record.

        Never raises for trial-level failures; they become the trial's status.
        """
        result = self._skeleton(plan, trial, task, stack, config)
        result.status = TrialStatus.RUNNING
        result.started_at = utcnow()
        wall = Stopwatch()
        wall.start()
        log_dir = self.workspaces.log_dir(plan.run_id, trial.trial_id)
        handle: EnvironmentHandle | None = None
        try:
            adapter = self.registry.create(trial.agent.agent, trial.agent.options)
            auth = self.resolve_auth(trial.agent, plan.auth_mode)
            workspace = self.workspaces.create(plan.run_id, trial.trial_id, task, stack.id)
            log_dir.mkdir(parents=True, exist_ok=True)

            _phase(trial.trial_id, "preparing images")
            stack_image = self.environment.prepare_image(stack)
            image = self.environment.prepare_agent_image(stack_image, adapter.image_layer())
            result.environment.image_ref = stack_image.reference
            result.environment.image_digest = stack_image.digest
            result.environment.agent_image_ref = image.reference

            _phase(trial.trial_id, "starting containers")
            handle = self.environment.create(stack, image, workspace, trial.trial_id, auth)
            self.environment.start(handle, plan.timeouts.setup_seconds)
            runner = self.environment.runner(handle)
            if stack.setup_commands:
                _phase(trial.trial_id, f"running {len(stack.setup_commands)} setup command(s)")
            for i, cmd in enumerate(stack.setup_commands):
                res = runner.run(
                    cmd,
                    timeout_seconds=plan.timeouts.setup_seconds,
                    workdir=stack.workdir,
                    stdout_path=log_dir / f"setup-{i}.stdout.log",
                    stderr_path=log_dir / f"setup-{i}.stderr.log",
                )
                if not res.ok:
                    raise ExecutionEnvironmentError(f"stack setup command failed: {cmd}")
            snapshot_baseline(runner, stack.workdir)
            result.agent.version = adapter.get_version(runner)

            agent_task = AgentTask(
                trial_id=trial.trial_id,
                prompt=self.render_prompt(task, stack),
                workdir=stack.workdir,
                log_dir=log_dir,
                timeout_seconds=plan.timeouts.agent_seconds,
                auth_mode=plan.auth_mode,
                model=trial.agent.model,
                reasoning_effort=trial.agent.reasoning_effort,
                tooling=config.tooling,
                options=trial.agent.options,
            )
            _phase(
                trial.trial_id,
                f"agent running (limit {format_duration(agent_task.timeout_seconds)}), "
                f"output: {log_dir / 'agent.stdout.log'}",
            )
            with AgentHeartbeat(
                trial.trial_id,
                log_dir / "agent.stdout.log",
                agent_task.timeout_seconds,
                self.settings.progress_interval_seconds,
            ):
                agent_result = self.execute_agent(adapter, agent_task, runner)
            self._record_agent(result, agent_result)

            change = snapshot_result(runner, stack.workdir)
            result.workspace_git_commit = change.commit
            result.metrics.code.lines_of_code = Metric[int].of(
                change.lines_added,
                MetricSource.MEASURED,
                unit="lines",
                note="lines added since the initial state, excluding dependencies and lockfiles",
            )
            result.metrics.code.files_changed = Metric[int].of(
                change.files_changed, MetricSource.MEASURED
            )

            _phase(trial.trial_id, "running acceptance tests")
            quality, test_logs, tester_ok = self.run_acceptance_tests(
                task, stack, handle, log_dir, plan
            )
            result.metrics.quality = quality
            result.log_files.update({k: str(v) for k, v in test_logs.items()})
            result.status = _final_status(agent_result, quality, tester_ok)
            if not tester_ok and result.status is TrialStatus.FAILED and not result.error:
                result.error = "acceptance tester did not finish cleanly (see acceptance logs)"
        except (ExecutionEnvironmentError, ConfigurationError, AgentExecutionError) as exc:
            log.error("trial %s failed: %s", trial.trial_id, exc)
            result.status = TrialStatus.INFRA_ERROR
            result.error = str(exc)
        except Exception as exc:  # never lose a trial to an unexpected bug
            log.exception("trial %s crashed", trial.trial_id)
            result.status = TrialStatus.INFRA_ERROR
            result.error = f"{type(exc).__name__}: {exc}"
        finally:
            if handle is not None:
                self._cleanup(handle, log_dir, result)
            if not self.settings.keep_workspaces and result.status is not TrialStatus.INFRA_ERROR:
                self.workspaces.remove(plan.run_id, trial.trial_id)
        result.finished_at = utcnow()
        result.metrics.performance.total_wall_clock_seconds = Metric[float].of(
            wall.stop(), MetricSource.MEASURED, unit="s"
        )
        result.metrics = apply_auth_policy(result.metrics, plan.auth_mode)
        _phase(
            trial.trial_id,
            f"{result.status.value} in "
            f"{format_duration(result.metrics.performance.total_wall_clock_seconds.value or 0)}",
        )
        return result

    def _cleanup(self, handle: EnvironmentHandle, log_dir: Path, result: TrialResult) -> None:
        """Collect logs and destroy the environment; a failure here fails only this trial."""
        problems = []
        try:
            logs = self.environment.collect_logs(handle, log_dir)
            result.log_files.update({k: str(v) for k, v in logs.items()})
        except Exception as exc:
            log.exception("trial %s: collecting logs failed", result.trial_id)
            problems.append(f"collecting logs failed: {exc}")
        try:
            self.environment.destroy(handle)
        except Exception as exc:
            log.exception("trial %s: cleanup failed", result.trial_id)
            problems.append(f"cleanup failed: {exc}")
        if problems:
            result.status = TrialStatus.INFRA_ERROR
            result.error = "; ".join(filter(None, [result.error, *problems]))

    def render_prompt(self, task: BenchmarkSpec, stack: StackSpec) -> str:
        """Build the agent prompt from the task template and stack facts."""
        return task.render_prompt(
            {
                "language": stack.language,
                "language_version": stack.language_version,
                "framework": stack.framework,
                "framework_version": stack.framework_version,
                "workdir": stack.workdir,
                "environment_notes": stack.environment_notes.strip(),
            }
        )

    def execute_agent(
        self, adapter: AgentAdapter, task: AgentTask, runner: CommandRunner
    ) -> AgentRunResult:
        """Run the agent with the timer around nothing but the agent itself."""
        return adapter.run(task, runner)

    def _record_agent(self, result: TrialResult, agent: AgentRunResult) -> None:
        perf = result.metrics.performance
        seconds = agent.duration_seconds
        perf.agent_execution_seconds = Metric[float].of(seconds, MetricSource.MEASURED, unit="s")
        perf.time_to_first_implementation_seconds = Metric[float].of(
            seconds,
            MetricSource.DERIVED,
            unit="s",
            note="no repair iterations: equals agent execution time",
        )
        perf.time_to_all_tests_pass_seconds = Metric[float].not_applicable(
            "repair iterations are disabled"
        )
        result.metrics.usage = agent.usage
        result.agent.version = agent.agent_version or result.agent.version
        result.agent.reported_model = agent.reported_model
        result.agent.resolved_model = AgentRecord.resolve_model(
            result.agent.requested_model, agent.reported_model
        )
        result.rate_limit_events = list(agent.rate_limit_events)
        result.raw = dict(agent.raw)
        for name, path in (
            ("agent-stdout", agent.stdout_path),
            ("agent-stderr", agent.stderr_path),
        ):
            if path is not None:
                result.log_files[name] = str(path)
        if agent.error:
            result.error = agent.error
        elif not agent.timed_out and not agent.succeeded:
            result.error = f"agent exited with code {agent.exit_code}"

    def run_acceptance_tests(
        self,
        task: BenchmarkSpec,
        stack: StackSpec,
        handle: EnvironmentHandle,
        log_dir: Path,
        plan: ExperimentPlan,
    ) -> tuple[QualityMetrics, dict[str, Path], bool]:
        """Restart the container, start the app, and run the black-box suite against it.

        Returns the quality metrics, log files, and whether the tester finished cleanly.
        """
        spec = task.acceptance_tests
        quality = QualityMetrics()
        if spec.suite is None:
            return quality, {}, False
        suite = spec.suite if spec.suite.is_absolute() else (task.source_dir or Path()) / spec.suite
        if not suite.is_dir():
            raise ConfigurationError(f"acceptance suite not found: {suite}")
        # An explicit image wins, so a prebuilt tester can replace the suite's Dockerfile.
        if spec.image:
            tester = ImageInfo(stack_id=f"acceptance-{task.id}", reference=spec.image)
        elif (suite / "Dockerfile").is_file():
            tester = self.environment.build_image(f"acceptance-{task.id}", suite)
        else:
            raise ConfigurationError("acceptance suite needs a Dockerfile or an 'image'")

        self.environment.restart(handle, plan.timeouts.setup_seconds)
        self.environment.exec_detached(
            handle,
            ["sh", "-c", f"{shlex.join(spec.app_start_command)} >/tmp/app.log 2>&1"],
            workdir=stack.workdir,
        )
        results_dir = log_dir / "acceptance"
        results_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(results_dir, 0o777)  # written by the tester's non-root user
        # A report left by an earlier run of this trial must never count for this one.
        (results_dir / spec.results_file).unlink(missing_ok=True)
        res = self.environment.run_job(
            handle,
            tester,
            spec.command,
            timeout_seconds=plan.timeouts.tests_seconds,
            mounts=[
                Mount(source=suite, target="/acceptance"),
                Mount(source=results_dir, target="/results", read_only=False),
            ],
            env={"BASE_URL": f"http://app:{spec.app_port}", "APP_PORT": str(spec.app_port)},
            stdout_path=log_dir / "acceptance.stdout.log",
            stderr_path=log_dir / "acceptance.stderr.log",
        )
        app_log = log_dir / "app.log"
        app = self.environment.exec(
            handle, ["cat", "/tmp/app.log"], timeout_seconds=30, stdout_path=app_log
        )
        files = {
            "acceptance-stdout": log_dir / "acceptance.stdout.log",
            "acceptance-stderr": log_dir / "acceptance.stderr.log",
        }
        if app.ok:
            files["app"] = app_log
        junit = results_dir / spec.results_file
        if not junit.is_file():
            why = "timed out" if res.timed_out else f"exit code {res.exit_code}"
            note = f"acceptance suite produced no {spec.results_file} ({why})"
            quality.tests_passed = Metric[int].unavailable(note)
            quality.tests_failed = Metric[int].unavailable(note)
            quality.tests_total = Metric[int].unavailable(note)
            return quality, files, False
        files["acceptance-junit"] = junit
        summary = parse_junit(junit)
        quality.tests_total = Metric[int].of(summary.tests, MetricSource.MEASURED)
        quality.tests_passed = Metric[int].of(summary.passed, MetricSource.MEASURED)
        quality.tests_failed = Metric[int].of(summary.failed, MetricSource.MEASURED)
        quality.repair_iterations = Metric[int].of(0, MetricSource.MEASURED)
        quality.retry_count = Metric[int].of(0, MetricSource.MEASURED)
        # A tester that timed out or crashed after writing a report did not finish the suite.
        return quality, files, res.ok


def _final_status(
    agent: AgentRunResult, quality: QualityMetrics, tester_ok: bool = True
) -> TrialStatus:
    if agent.timed_out:
        return TrialStatus.TIMED_OUT
    if agent.stopped_by_rate_limit:
        return TrialStatus.RATE_LIMITED
    if not agent.succeeded:
        return TrialStatus.AGENT_ERROR
    passed, total = quality.tests_passed, quality.tests_total
    all_passed = passed.is_available and total.is_available and passed.value == total.value
    if tester_ok and all_passed and total.value:
        return TrialStatus.PASSED
    return TrialStatus.FAILED


def _platform_commit() -> str | None:
    root = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() or None


def _phase(trial_id: str, message: str) -> None:
    log.info("trial %s: %s", trial_id, message)
