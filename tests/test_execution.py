"""Orchestrator trial execution against an in-memory environment."""

import shutil
from pathlib import Path
from typing import Any

import pytest

from ai_benchmark.agents import AgentRegistry
from ai_benchmark.config import AgentSelection, ExperimentConfig, SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.models import AuthMode, Metric, MetricSource, TrialStatus, UsageMetrics
from ai_benchmark.orchestrator import BenchmarkOrchestrator
from ai_benchmark.orchestrator.policy import REPORTED_COST_KEY
from ai_benchmark.storage import JsonResultStore, ResultFilter

from .conftest import REPO_ROOT
from .fakes import FAKE_TOKEN_ENV, JUNIT_ONE_FAIL, FakeAdapter, FakeEnvironment


class CostlyAdapter(FakeAdapter):
    name = "costly"

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        usage = UsageMetrics()
        usage.cost = Metric[float].of(0.42, MetricSource.AGENT_REPORTED, unit="USD")
        return usage, Metric[str].of("fake-model-2026", MetricSource.AGENT_REPORTED)

    def run(self, task: Any, runner: Any) -> Any:
        result = super().run(task, runner)
        result.usage, result.reported_model = self.parse_usage({})
        return result


@pytest.fixture
def env_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlatformSettings:
    tasks = tmp_path / "tasks"
    shutil.copytree(REPO_ROOT / "benchmarks" / "tasks", tasks)
    (tasks / "task-management-api" / "acceptance").mkdir(exist_ok=True)
    (tasks / "task-management-api" / "acceptance" / "Dockerfile").write_text("FROM scratch\n")
    monkeypatch.setenv(FAKE_TOKEN_ENV, "secret-token-value")
    return PlatformSettings(
        tasks_dir=tasks,
        stacks_dir=REPO_ROOT / "benchmarks" / "stacks",
        results_dir=tmp_path / "results",
        workspaces_dir=tmp_path / "workspaces",
    )


def build(settings: PlatformSettings, env: FakeEnvironment) -> BenchmarkOrchestrator:
    reg = AgentRegistry()
    reg._entry_points_loaded = True
    reg.register(FakeAdapter)
    reg.register(CostlyAdapter)
    return BenchmarkOrchestrator(
        settings=settings,
        catalog=SpecCatalog(settings),
        registry=reg,
        environment=env,
        store=JsonResultStore(settings.results_dir),
    )


def experiment(**kw: Any) -> ExperimentConfig:
    base: dict[str, Any] = dict(
        task="task-management-api",
        agents=[AgentSelection(agent="fake", model="m1")],
        stacks=["fastapi"],
        trials=1,
        seed=3,
    )
    return ExperimentConfig.model_validate({**base, **kw})


def test_passing_trial_is_recorded(env_settings: PlatformSettings) -> None:
    env = FakeEnvironment()
    o = build(env_settings, env)
    seen: list[str] = []
    summary = o.run(experiment(), on_trial_complete=lambda r: seen.append(r.trial_id))
    assert summary.by_status == {"passed": 1} and seen == summary.trial_ids

    [r] = list(o.store.iter_trials(ResultFilter(run_id=summary.run_id)))
    assert r.status is TrialStatus.PASSED
    assert r.agent.version == "fake 1.0.0" and r.agent.resolved_model == "m1"
    assert r.metrics.quality.tests_passed.value == 2 and r.metrics.quality.tests_failed.value == 0
    assert r.metrics.code.lines_of_code.value == 15
    assert r.metrics.code.files_changed.value == 3
    assert r.metrics.performance.agent_execution_seconds.is_available
    assert r.metrics.performance.total_wall_clock_seconds.is_available
    assert not r.metrics.performance.time_to_all_tests_pass_seconds.is_available
    assert r.workspace_git_commit == "abc123"
    assert r.environment.agent_image_ref == "agent-fake-1.0.0"
    assert env.destroyed == [r.trial_id]
    # the app is started fresh after a restart, then tested from outside
    steps = [c[0] for c in env.calls]
    assert steps.index("restart") < steps.index("exec_detached") < steps.index("run_job")
    job = next(c[1] for c in env.calls if c[0] == "run_job")
    assert job["env"]["BASE_URL"] == "http://app:8000"
    # workspace removed after a clean trial
    assert not (env_settings.workspaces_dir / summary.run_id / r.trial_id / "workspace").exists()

    manifest = o.store.load_manifest(summary.run_id)
    assert manifest.trial_ids == [r.trial_id] and manifest.auth_mode is AuthMode.SUBSCRIPTION


def test_failed_tests_mark_trial_failed(env_settings: PlatformSettings) -> None:
    o = build(env_settings, FakeEnvironment(junit=JUNIT_ONE_FAIL))
    summary = o.run(experiment())
    [r] = list(o.store.iter_trials())
    assert summary.by_status == {"failed": 1}
    assert r.metrics.quality.tests_failed.value == 1


def test_missing_junit_makes_tests_unavailable(env_settings: PlatformSettings) -> None:
    o = build(env_settings, FakeEnvironment(junit=None))
    o.run(experiment())
    [r] = list(o.store.iter_trials())
    assert r.status is TrialStatus.FAILED
    assert not r.metrics.quality.tests_total.is_available
    assert "junit.xml" in (r.metrics.quality.tests_total.note or "")


def test_rate_limit_and_agent_error(env_settings: PlatformSettings) -> None:
    o = build(env_settings, FakeEnvironment(agent_stderr="usage limit reached", agent_exit=1))
    o.run(experiment())
    [r] = list(o.store.iter_trials())
    assert r.status is TrialStatus.RATE_LIMITED and r.rate_limited

    o = build(env_settings, FakeEnvironment(agent_exit=2))
    o.run(experiment(), run_id="r-agent-error")
    [r] = list(o.store.iter_trials(ResultFilter(run_id="r-agent-error")))
    assert r.status is TrialStatus.AGENT_ERROR
    assert r.error == "agent exited with code 2"


def test_infra_error_is_recorded_and_can_abort(env_settings: PlatformSettings) -> None:
    env = FakeEnvironment(fail_on="start")
    o = build(env_settings, env)
    cfg = experiment(trials=2, stop_on_infra_error=True)
    summary = o.run(cfg)
    results = sorted(o.store.iter_trials(), key=lambda r: r.execution_order)
    assert [r.status for r in results] == [TrialStatus.INFRA_ERROR, TrialStatus.CANCELLED]
    assert "start failed" in (results[0].error or "")
    assert summary.total == 2 and env.destroyed == [results[0].trial_id]
    # the workspace of an infra failure is kept for debugging
    ws = env_settings.workspaces_dir / summary.run_id / results[0].trial_id / "workspace"
    assert ws.is_dir()


def test_subscription_hides_cost_and_secrets_never_stored(env_settings: PlatformSettings) -> None:
    o = build(env_settings, FakeEnvironment())
    summary = o.run(experiment(agents=[AgentSelection(agent="costly", model="alias")]))
    [r] = list(o.store.iter_trials())
    assert not r.metrics.usage.cost.is_available
    assert r.metrics.provider_specific[REPORTED_COST_KEY] == 0.42
    assert r.agent.resolved_model == "fake-model-2026"
    stored = (env_settings.results_dir / summary.run_id).rglob("*.json")
    assert all("secret-token-value" not in p.read_text() for p in stored)


def test_api_key_mode_keeps_cost(env_settings: PlatformSettings, monkeypatch: Any) -> None:
    from .fakes import FAKE_API_KEY_ENV

    monkeypatch.setenv(FAKE_API_KEY_ENV, "k")
    o = build(env_settings, FakeEnvironment())
    o.run(experiment(agents=[AgentSelection(agent="costly")], auth_mode=AuthMode.API_KEY))
    [r] = list(o.store.iter_trials())
    assert r.metrics.usage.cost.value == 0.42
