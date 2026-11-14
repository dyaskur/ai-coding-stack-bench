"""Aggregate comparable trial results for the CLI and future reports."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, stdev

from ai_benchmark.models.enums import ComparisonDimension, TrialStatus
from ai_benchmark.models.results import TrialResult


@dataclass(frozen=True)
class ComparisonRow:
    value: str
    trials: int
    passed: int
    rate_limited: int
    timed: int
    mean_seconds: float | None
    stdev_seconds: float | None
    low_confidence: bool


def compare_trials(
    trials: list[TrialResult], dimension: ComparisonDimension, min_trials: int = 3
) -> list[ComparisonRow]:
    if min_trials < 1:
        raise ValueError("min_trials must be at least 1")
    keys = {trial.comparability_key() for trial in trials}
    if len(keys) > 1:
        raise ValueError("results mix task/version, tooling mode, or auth mode; filter to one set")
    stack_axes = {
        ComparisonDimension.STACK, ComparisonDimension.LANGUAGE, ComparisonDimension.FRAMEWORK
    }
    if dimension in stack_axes and len({trial.agent.agent_model for trial in trials}) > 1:
        raise ValueError("stack comparison mixes agent/models; filter to one agent and model")
    if dimension not in stack_axes and len({trial.environment.stack for trial in trials}) > 1:
        raise ValueError("model comparison mixes stacks; filter to one stack")
    if dimension is ComparisonDimension.MODEL and len({trial.agent.name for trial in trials}) > 1:
        raise ValueError("model comparison mixes agents; filter to one agent")
    if dimension is ComparisonDimension.AGENT and len(
        {trial.agent.model_label for trial in trials}
    ) > 1:
        raise ValueError("agent comparison mixes models; filter to one model")

    groups: dict[str, list[TrialResult]] = {}
    for trial in trials:
        groups.setdefault(trial.dimension_value(dimension), []).append(trial)

    rows = []
    for value, group in groups.items():
        times = [
            trial.metrics.performance.agent_execution_seconds.value
            for trial in group
            if trial.status is TrialStatus.PASSED
            and not trial.rate_limited
            and trial.metrics.performance.agent_execution_seconds.is_available
        ]
        rows.append(
            ComparisonRow(
                value=value,
                trials=len(group),
                passed=sum(trial.status is TrialStatus.PASSED for trial in group),
                rate_limited=sum(trial.rate_limited for trial in group),
                timed=len(times),
                mean_seconds=mean(times) if times else None,
                stdev_seconds=stdev(times) if len(times) > 1 else None,
                low_confidence=len(group) < min_trials,
            )
        )
    return sorted(
        rows, key=lambda row: (row.mean_seconds is None, row.mean_seconds or 0, row.value)
    )
