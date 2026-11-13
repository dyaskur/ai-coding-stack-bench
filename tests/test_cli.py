from pathlib import Path

from typer.testing import CliRunner

from ai_benchmark.cli.app import app

from .conftest import REPO_ROOT

runner = CliRunner()
DIRS = [
    "--tasks-dir",
    str(REPO_ROOT / "benchmarks" / "tasks"),
    "--stacks-dir",
    str(REPO_ROOT / "benchmarks" / "stacks"),
]


def invoke(*args: str, results: Path | None = None) -> "object":
    extra = ["--results-dir", str(results)] if results else []
    return runner.invoke(app, [*DIRS, *extra, *args])


def test_listing_commands() -> None:
    for cmd, needle in [("tasks", "task-management-api"), ("stacks", "fastapi"), ("agents", "")]:
        result = invoke(cmd)
        assert result.exit_code == 0, result.output
        assert needle in result.output


def test_validate_catalog_and_files() -> None:
    assert invoke("validate").exit_code == 0
    assert invoke("validate", str(REPO_ROOT / "examples" / "experiment.yaml")).exit_code == 0


def test_validate_reports_bad_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: x\nlanguage: python\n")
    result = invoke("validate", str(bad))
    assert result.exit_code == 1


def test_run_dry_run_shows_plan() -> None:
    result = invoke(
        "run",
        "--agents",
        "codex,claude:m1",
        "--task",
        "task-management-api",
        "--stacks",
        "fastapi,laravel",
        "--trials",
        "2",
        "--seed",
        "1",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert "8 trial(s)" in result.output


def test_run_without_adapters_fails_preflight() -> None:
    result = invoke(
        "run", "--agents", "codex", "--task", "task-management-api", "--stacks", "fastapi"
    )
    assert result.exit_code == 1


def test_run_requires_arguments() -> None:
    assert invoke("run").exit_code == 2


def test_results_empty(tmp_path: Path) -> None:
    result = invoke("results", results=tmp_path)
    assert result.exit_code == 0 and "No results" in result.output


def test_run_dry_run_auth_mode() -> None:
    args = ["run", "--agents", "codex", "--task", "task-management-api", "--stacks", "fastapi"]
    assert "auth subscription" in invoke(*args, "--dry-run").output
    result = invoke(*args, "--auth", "api_key", "--dry-run")
    assert result.exit_code == 0 and "auth api_key" in result.output
