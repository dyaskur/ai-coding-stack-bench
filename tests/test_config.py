from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_benchmark.config import (
    AgentSelection,
    ExperimentConfig,
    McpServerConfig,
    SpecCatalog,
    StackSpec,
    ToolingConfig,
    load_task,
)
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.errors import ConfigurationError
from ai_benchmark.models import ToolingMode

STACK = dict(
    id="fastapi",
    language="python",
    language_version="3.12",
    framework="fastapi",
    framework_version="0.115",
)


def test_catalog_loads_examples(settings: PlatformSettings) -> None:
    catalog = SpecCatalog(settings)
    assert {"fastapi", "laravel", "express"} <= set(catalog.stacks())
    task = catalog.get_task("task-management-api")
    assert task.source_dir is not None
    assert all(catalog.get_stack(s) for s in task.supported_stacks)


def test_unknown_task_lists_known(settings: PlatformSettings) -> None:
    with pytest.raises(ConfigurationError, match="task-management-api"):
        SpecCatalog(settings).get_task("nope")


@pytest.mark.parametrize("image", ["python", "python:latest", "localhost:5000/python"])
def test_stack_image_must_be_pinned(image: str) -> None:
    with pytest.raises(ValidationError, match="pinned"):
        StackSpec(**STACK, image=image)


@pytest.mark.parametrize("image", ["python:3.12.7-slim", "ghcr.io/x/y:1.0", "python@sha256:abc"])
def test_stack_pinned_images_accepted(image: str) -> None:
    assert StackSpec(**STACK, image=image).image == image


def test_stack_needs_image_or_dockerfile() -> None:
    with pytest.raises(ValidationError, match="image' or 'dockerfile"):
        StackSpec(**STACK)


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        StackSpec(**STACK, image="python:3.12", imagee="typo")


def test_prompt_rendering(settings: PlatformSettings) -> None:
    task = SpecCatalog(settings).get_task("task-management-api")
    prompt = task.render_prompt(
        dict(
            language="python",
            language_version="3.12",
            framework="fastapi",
            framework_version="0.115",
            workdir="/workspace",
            environment_notes="- Postgres at host postgres",
        )
    )
    assert "fastapi 0.115" in prompt and "POST /projects creates a project" in prompt
    assert "Environment:\n- Postgres at host postgres" in prompt
    assert "sh start.sh" in prompt
    with pytest.raises(KeyError):
        task.render_prompt({})


def test_extend_mode_requires_template(tmp_path: Path) -> None:
    f = tmp_path / "task.yaml"
    f.write_text(
        "id: t\nname: T\ndescription: d\nfunctional_requirements: [a]\n"
        "prompt_template: x\nsupported_stacks: [fastapi]\n"
        "acceptance_tests: {command: [pytest]}\n"
        "initial_state: {mode: extend_existing}\n"
    )
    with pytest.raises(ConfigurationError, match="template"):
        load_task(f)


def test_acceptance_defaults_and_stack_source_dir(settings: PlatformSettings) -> None:
    catalog = SpecCatalog(settings)
    spec = catalog.get_task("task-management-api").acceptance_tests
    assert spec.app_start_command == ["sh", "start.sh"] and spec.app_port == 8000
    assert "--junitxml=/results/junit.xml" in spec.command
    stack = catalog.get_stack("fastapi")
    assert stack.source_dir is not None
    assert stack.resolve(Path("x/Dockerfile")) == stack.source_dir / "x/Dockerfile"


def test_agent_selection_parse() -> None:
    assert AgentSelection.parse("claude:some-model").model == "some-model"
    assert AgentSelection.parse("codex").model is None


def test_experiment_rejects_duplicates_and_parallelism() -> None:
    base = dict(task="t", agents=[AgentSelection(agent="a")], stacks=["s"])
    with pytest.raises(ValidationError):
        ExperimentConfig(**{**base, "stacks": ["s", "s"]})
    with pytest.raises(ValidationError):
        ExperimentConfig(**{**base, "agents": [AgentSelection(agent="a")] * 2})
    with pytest.raises(ValidationError):
        ExperimentConfig(**base, max_parallel=2)


def test_mcp_only_in_best_ecosystem_mode() -> None:
    server = McpServerConfig(name="docs", url="https://example.invalid/mcp")
    with pytest.raises(ValidationError):
        ToolingConfig(mode=ToolingMode.STANDARD, mcp_servers=[server])
    assert ToolingConfig(mode=ToolingMode.BEST_ECOSYSTEM, mcp_servers=[server])


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCHMARK_RESULTS_DIR", "/tmp/out")
    monkeypatch.setenv("BENCHMARK_DOCKER__DEFAULT_MEMORY_LIMIT", "8g")
    s = PlatformSettings()
    assert s.results_dir == Path("/tmp/out")
    assert s.docker.default_memory_limit == "8g"


def test_shipped_stacks_build_from_files_and_describe_their_environment(
    settings: PlatformSettings,
) -> None:
    catalog = SpecCatalog(settings)
    for stack_id in ("fastapi", "laravel"):
        stack = catalog.get_stack(stack_id)
        assert stack.dockerfile is not None
        assert stack.resolve(stack.dockerfile).is_file()
    for stack_id in catalog.get_task("task-management-api").supported_stacks:
        # Every stack the task runs on must tell the agent where its database is.
        assert catalog.get_stack(stack_id).environment_notes.strip(), stack_id


def test_shipped_task_has_its_acceptance_suite(settings: PlatformSettings) -> None:
    task = SpecCatalog(settings).get_task("task-management-api")
    assert task.acceptance_tests.suite is not None and task.source_dir is not None
    suite = task.source_dir / task.acceptance_tests.suite
    assert (suite / "Dockerfile").is_file()
    assert len(list(suite.glob("test_*.py"))) >= 4


@pytest.mark.parametrize("image", ["tester", "tester:latest"])
def test_tester_image_must_be_pinned(image: str) -> None:
    from ai_benchmark.config.task import AcceptanceTests

    with pytest.raises(ValueError, match="must be pinned"):
        AcceptanceTests(image=image)
    assert AcceptanceTests(image="tester:1.2").image == "tester:1.2"
