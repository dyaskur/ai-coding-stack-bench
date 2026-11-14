"""Flat trial exports and aggregate report rendering."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from html import escape

from ai_benchmark.comparison import ComparisonRow
from ai_benchmark.models.results import TrialResult


def trial_row(trial: TrialResult) -> dict[str, str | int | float | bool | None]:
    """One stable, dashboard-friendly row; unavailable metrics remain null."""
    performance = trial.metrics.performance
    quality = trial.metrics.quality
    usage = trial.metrics.usage
    tokens = usage.tokens
    return {
        "run_id": trial.run_id,
        "trial_id": trial.trial_id,
        "trial_index": trial.trial_index,
        "task": trial.task_id,
        "task_version": trial.task_version,
        "agent": trial.agent.name,
        "model": trial.agent.model_label,
        "stack": trial.environment.stack,
        "language": trial.environment.language,
        "language_version": trial.environment.language_version,
        "framework": trial.environment.framework,
        "framework_version": trial.environment.framework_version,
        "tooling_mode": trial.tooling.mode.value,
        "auth_mode": trial.agent.auth_mode.value,
        "status": trial.status.value,
        "rate_limited": trial.rate_limited,
        "agent_seconds": performance.agent_execution_seconds.value,
        "wall_seconds": performance.total_wall_clock_seconds.value,
        "tests_passed": quality.tests_passed.value,
        "tests_failed": quality.tests_failed.value,
        "tests_total": quality.tests_total.value,
        "token_scheme": tokens.accounting_scheme,
        "input_tokens": tokens.input_tokens.value,
        "cached_input_tokens": tokens.cached_input_tokens.value,
        "output_tokens": tokens.output_tokens.value,
        "reasoning_tokens": tokens.reasoning_tokens.value,
        "total_tokens": tokens.total_tokens.value,
        "cost_usd": usage.cost.value,
        "cost_availability": usage.cost.availability.value,
    }


def csv_text(rows: list[dict]) -> str:
    if not rows:
        return ""
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(
        {
            key: "'" + value
            if isinstance(value, str)
            and (
                value.startswith(("\t", "\r", "\n"))
                or value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
            )
            else value
            for key, value in row.items()
        }
        for row in rows
    )
    return stream.getvalue()


def export_trials(trials: list[TrialResult], fmt: str) -> str:
    rows = [trial_row(trial) for trial in trials]
    if fmt == "csv":
        return csv_text(rows)
    if fmt == "jsonl":
        return "".join(json.dumps(row, allow_nan=False) + "\n" for row in rows)
    raise ValueError("format must be csv or jsonl")


def _markdown_label(value: str) -> str:
    return escape(value).replace("|", "&#124;").replace("\n", " ").replace("\r", " ")


def render_report(rows: list[ComparisonRow], dimension: str, fmt: str) -> str:
    data = [asdict(row) for row in rows]
    if fmt == "json":
        return json.dumps(
            {"compare_by": dimension, "groups": data}, indent=2, allow_nan=False
        ) + "\n"
    if fmt == "csv":
        return csv_text(data)
    if fmt != "markdown":
        raise ValueError("format must be markdown, json, or csv")
    lines = [
        f"# Benchmark comparison by {dimension}",
        "",
        "| Group | Passed/trials | Rate limited | Timed | Mean time (s) | "
        "Std dev (s) | Mean cost (USD) | Cost samples | Confidence |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        label = _markdown_label(row.value)
        mean = f"{row.mean_seconds:.2f}" if row.mean_seconds is not None else "n/a"
        dev = f"{row.stdev_seconds:.2f}" if row.stdev_seconds is not None else "n/a"
        cost = f"{row.mean_cost_usd:.4f}" if row.mean_cost_usd is not None else "n/a"
        lines.append(
            f"| {label} | "
            f"{row.passed}/{row.trials} | {row.rate_limited} | "
            f"{row.timed} | {mean} | {dev} | {cost} | {row.cost_samples} | "
            f"{'low' if row.low_confidence else 'ok'} |"
        )
    lines.extend(["", "## Success rate", "", "| Group | Success |", "| --- | --- |"])
    for row in rows:
        rate = row.passed / row.trials if row.trials else 0
        label = _markdown_label(row.value)
        lines.append(f"| {label} | {'█' * round(rate * 20)} {rate:.0%} |")
    lines.extend([
        "",
        "Agent time uses passing trials without rate-limit events and an available timer.",
        "Success rate includes all trials. Cost is n/a when unavailable.", "",
    ])
    return "\n".join(lines)
