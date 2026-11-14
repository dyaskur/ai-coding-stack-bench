import csv
import io
import json
from pathlib import Path

from typer.testing import CliRunner

from ai_benchmark.cli.app import app
from ai_benchmark.models import Metric, MetricSource, TrialStatus
from ai_benchmark.reporting import export_trials
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
