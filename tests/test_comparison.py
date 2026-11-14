from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_trials
from ai_benchmark.models import ComparisonDimension, Metric, MetricSource, TrialStatus
from ai_benchmark.storage import JsonResultStore

from .test_storage import manifest, trial


def test_compare_ranks_passing_times_and_counts_limit_hits() -> None:
    fast = [trial("r1", f"f{i}", "codex", TrialStatus.PASSED) for i in range(3)]
    for item, seconds in zip(fast, [10.0, 12.0, 14.0], strict=True):
        item.metrics.performance.agent_execution_seconds = Metric[float].of(
            seconds, MetricSource.MEASURED
        )
        item.metrics.usage.cost = Metric[float].of(0.02, MetricSource.AGENT_REPORTED)
    slow = trial("r1", "s1", "codex", TrialStatus.PASSED, limited=True)
    slow.environment.stack = "laravel"
    slow.metrics.performance.agent_execution_seconds = Metric[float].of(1.0, MetricSource.MEASURED)
    failed = trial("r1", "s2", "codex", TrialStatus.FAILED)
    failed.environment.stack = "laravel"

    rows = compare_trials([*fast, slow, failed], ComparisonDimension.STACK)
    assert [row.value for row in rows] == ["fastapi", "laravel"]
    assert (rows[0].passed, rows[0].trials, rows[0].mean_seconds, rows[0].stdev_seconds) == (
        3, 3, 12.0, 2.0
    )
    assert (rows[1].passed, rows[1].rate_limited, rows[1].timed, rows[1].mean_seconds) == (
        1, 1, 0, None
    )
    assert rows[1].low_confidence and not rows[0].low_confidence
    assert rows[0].mean_cost_usd == 0.02 and rows[1].mean_cost_usd is None
    assert (rows[0].cost_samples, rows[1].cost_samples) == (3, 0)


def test_compare_rejects_mixed_task_versions() -> None:
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    b.task_version = "2"
    with pytest.raises(ValueError, match="task/version"):
        compare_trials([a, b], ComparisonDimension.STACK)


def test_compare_rejects_mixed_model_in_stack_view() -> None:
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    b.environment.stack = "laravel"
    b.agent.resolved_model = "other-model"
    with pytest.raises(ValueError, match="agent/models"):
        compare_trials([a, b], ComparisonDimension.STACK)


def test_compare_cli_reads_saved_results(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.metrics.performance.agent_execution_seconds = Metric[float].of(9.0, MetricSource.MEASURED)
    store.save_trial(item)
    result = CliRunner().invoke(app, ["--results-dir", str(tmp_path), "compare", "--run", "r1"])
    assert result.exit_code == 0, result.output
    assert "fastapi" in result.output and "1/1" in result.output and "9.0s" in result.output
