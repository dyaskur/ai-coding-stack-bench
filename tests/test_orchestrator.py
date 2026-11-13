from pathlib import Path

import pytest

from ai_benchmark.agents import AgentRegistry
from ai_benchmark.config import AgentSelection, ExperimentConfig, SpecCatalog
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.environment import DockerExecutionEnvironment, WorkspaceManager
from ai_benchmark.errors import PlanningError
from ai_benchmark.models import AuthMode
from ai_benchmark.orchestrator import BenchmarkOrchestrator, Stopwatch
from ai_benchmark.storage import JsonResultStore

from .fakes import FAKE_TOKEN_ENV, ApiKeyOnlyAdapter, FakeAdapter


def make(settings: PlatformSettings) -> BenchmarkOrchestrator:
    reg = AgentRegistry()
    reg._entry_points_loaded = True
    return BenchmarkOrchestrator(
        settings=settings,
        catalog=SpecCatalog(settings),
        registry=reg,
        environment=DockerExecutionEnvironment(settings.docker),
        store=JsonResultStore(settings.results_dir),
    )


def config(**kw: object) -> ExperimentConfig:
    base: dict[str, object] = dict(
        task="task-management-api",
        agents=[AgentSelection(agent="a"), AgentSelection(agent="b", model="m")],
        stacks=["fastapi", "laravel"],
        trials=3,
        seed=7,
    )
    return ExperimentConfig.model_validate({**base, **kw})


def test_plan_expands_full_matrix(settings: PlatformSettings) -> None:
    plan = make(settings).plan(config(), run_id="r1")
    assert len(plan.trials) == 2 * 2 * 3
    assert len({t.trial_id for t in plan.trials}) == len(plan.trials)
    assert [t.execution_order for t in plan.trials] == list(range(12))


def test_plan_is_reproducible_with_seed(settings: PlatformSettings) -> None:
    o = make(settings)
    a = [t.trial_id for t in o.plan(config(), run_id="r1").trials]
    b = [t.trial_id for t in o.plan(config(), run_id="r1").trials]
    unshuffled = [t.trial_id for t in o.plan(config(randomize_order=False), run_id="r1").trials]
    assert a == b
    assert a != unshuffled and sorted(a) == sorted(unshuffled)


def test_plan_uses_task_default_trials(settings: PlatformSettings) -> None:
    plan = make(settings).plan(config(trials=None), run_id="r1")
    assert plan.trials_per_combination == 3


def test_plan_rejects_unsupported_stack(settings: PlatformSettings, tmp_path: Path) -> None:
    (tmp_path / "go.yaml").write_text(
        "id: gin\nlanguage: go\nlanguage_version: '1.23'\nframework: gin\n"
        "framework_version: '1.10'\nimage: golang:1.23.3\n"
    )
    s = settings.model_copy(update={"stacks_dir": tmp_path})
    with pytest.raises(PlanningError, match="does not support"):
        from ai_benchmark.orchestrator.plan import build_plan

        cat = SpecCatalog(s)
        build_plan(config(stacks=["gin"]), cat.get_task("task-management-api"), cat.stacks(), "r1")


def test_preflight_reports_unregistered_agents(settings: PlatformSettings) -> None:
    o = make(settings)
    issues = o.preflight(o.plan(config(), run_id="r1"))
    errors = [i for i in issues if i.severity == "error"]
    assert [i.subject for i in errors] == ["a", "b:m"]
    assert any(i.subject == "auth" and i.severity == "warning" for i in issues)
    model_warning = next(i for i in issues if i.subject == "model")
    assert model_warning.severity == "warning" and "a" in model_warning.message
    assert "b:m" not in model_warning.message


def test_preflight_checks_credentials_and_auth_support(
    settings: PlatformSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    o = make(settings)
    o.registry.register(FakeAdapter)
    o.registry.register(ApiKeyOnlyAdapter)
    cfg = config(agents=[{"agent": "fake"}, {"agent": "apikey-only"}], trials=1)
    monkeypatch.delenv(FAKE_TOKEN_ENV, raising=False)
    errors = [i for i in o.preflight(o.plan(cfg, run_id="r1")) if i.severity == "error"]
    assert {i.subject: i.message for i in errors} == {
        "apikey-only": "does not support auth mode 'subscription'",
        "fake": f"environment variable {FAKE_TOKEN_ENV} is not set",
    }
    monkeypatch.setenv(FAKE_TOKEN_ENV, "x")
    cfg = config(agents=[{"agent": "fake"}], trials=1)
    assert not [i for i in o.preflight(o.plan(cfg, run_id="r1")) if i.severity == "error"]
    assert o.plan(cfg, run_id="r1").auth_mode is AuthMode.SUBSCRIPTION


def test_run_refuses_when_preflight_fails(settings: PlatformSettings) -> None:
    with pytest.raises(PlanningError, match="no adapter registered"):
        make(settings).run(config())


def test_workspace_manager_isolates_trials(settings: PlatformSettings, tmp_path: Path) -> None:
    task = SpecCatalog(settings).get_task("task-management-api")
    wm = WorkspaceManager(tmp_path / "ws")
    a = wm.create("r1", "t1", task, "fastapi")
    b = wm.create("r1", "t2", task, "fastapi")
    assert a.is_dir() and b.is_dir() and a != b
    assert wm.log_dir("r1", "t1").is_dir()


def test_stopwatch() -> None:
    sw = Stopwatch()
    sw.start()
    assert sw.mark("x") >= 0
    assert sw.stop() >= sw.marks["x"]
    assert sw.started_at is not None and sw.stopped_at is not None


def test_container_runner_delegates_to_environment(settings: PlatformSettings) -> None:
    from ai_benchmark.environment import ContainerCommandRunner

    env = DockerExecutionEnvironment(settings.docker)
    assert isinstance(env.runner(object()), ContainerCommandRunner)  # type: ignore[arg-type]
