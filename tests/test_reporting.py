import csv
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ai_benchmark.cli.app import app
from ai_benchmark.comparison import compare_trials
from ai_benchmark.models import (
    AuthMode,
    ComparisonDimension,
    Metric,
    MetricSource,
    ToolingMode,
    TrialStatus,
)
from ai_benchmark.reporting import csv_text, export_trials, render_report
from ai_benchmark.storage import JsonResultStore

from .test_storage import manifest, trial


def test_flat_export_keeps_unavailable_metrics_null() -> None:
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.metrics.performance.agent_execution_seconds = Metric[float].of(7.5, MetricSource.MEASURED)
    row = json.loads(export_trials([item], "jsonl"))
    assert row["agent_seconds"] == 7.5
    assert row["cost_usd"] is None and row["cost_availability"] == "unavailable"
    assert row["input_tokens"] is None
    csv_row = next(csv.DictReader(io.StringIO(export_trials([item], "csv"))))
    assert csv_row["agent_seconds"] == "7.5" and csv_row["cost_usd"] == ""


def test_report_and_export_cli(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.metrics.performance.agent_execution_seconds = Metric[float].of(7.5, MetricSource.MEASURED)
    store.save_trial(item)
    runner = CliRunner()
    args = ["--results-dir", str(tmp_path)]

    exported = runner.invoke(app, [*args, "export", "--run", "r1", "--format", "jsonl"])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.output)["trial_id"] == "t1"

    report = runner.invoke(app, [*args, "report", "--run", "r1"])
    assert report.exit_code == 0, report.output
    assert "| fastapi | 1/1 |" in report.output
    assert "100%" in report.output
    report_json = runner.invoke(app, [*args, "report", "--run", "r1", "--format", "json"])
    assert json.loads(report_json.output)["groups"][0]["mean_seconds"] == 7.5
    report_csv = runner.invoke(app, [*args, "report", "--run", "r1", "--format", "csv"])
    assert next(csv.DictReader(io.StringIO(report_csv.output)))["value"] == "fastapi"


def test_report_rejects_mixed_task_versions(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r1", "b", "codex", TrialStatus.PASSED)
    b.task_version = "2"
    store.save_trial(a)
    store.save_trial(b)
    result = CliRunner().invoke(app, ["--results-dir", str(tmp_path), "report", "--run", "r1"])
    assert result.exit_code == 1 and "task/version" in result.output


def test_markdown_report_escapes_group_label() -> None:
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    item.environment.stack = "bad|stack\nnext"
    rows = compare_trials([item], ComparisonDimension.STACK)
    report = render_report(rows, "stack", "markdown")
    assert "bad&#124;stack next" in report
    assert "| bad|stack" not in report


def test_csv_neutralizes_formula_labels_without_changing_numbers() -> None:
    output = csv_text([{"label": "=1+1", "spaced": "  +cmd", "tab": "\tcmd", "count": -3}])
    row = next(csv.DictReader(io.StringIO(output)))
    assert row == {"label": "'=1+1", "spaced": "'  +cmd", "tab": "'\tcmd", "count": "-3"}


def test_report_json_rejects_nonfinite_aggregate() -> None:
    item = trial("r1", "t1", "codex", TrialStatus.PASSED)
    row = compare_trials([item], ComparisonDimension.STACK)[0]
    with pytest.raises(ValueError):
        render_report([replace(row, mean_seconds=float("nan"))], "stack", "json")


def test_report_filters_mixed_modes_and_auth(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    store.save_manifest(manifest("r2"))
    a = trial("r1", "a", "codex", TrialStatus.PASSED)
    b = trial("r2", "b", "codex", TrialStatus.PASSED)
    b.agent.auth_mode = AuthMode.API_KEY
    b.tooling.mode = ToolingMode.BEST_ECOSYSTEM
    store.save_trial(a)
    store.save_trial(b)
    args = ["--results-dir", str(tmp_path), "report", "--format", "json"]
    runner = CliRunner()
    assert runner.invoke(app, args).exit_code == 1
    result = runner.invoke(app, [*args, "--auth", "subscription", "--mode", "standard"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["groups"][0]["trials"] == 1
