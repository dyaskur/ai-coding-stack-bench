"""Metric schemas.

Every metric is wrapped in :class:`Metric`, which carries an explicit
availability flag. This is how the platform keeps "the agent did not report
this" separate from "the value is zero", and why nothing is ever estimated
silently.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_benchmark.models.enums import MetricAvailability, MetricSource

T = TypeVar("T", int, float, str)


class Metric(BaseModel, Generic[T]):
    """A single metric value with provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: T | None = None
    availability: MetricAvailability = MetricAvailability.UNAVAILABLE
    source: MetricSource | None = None
    unit: str | None = None
    note: str | None = Field(default=None, description="Why a value is missing, or caveats.")

    @model_validator(mode="after")
    def _value_matches_availability(self) -> Metric[T]:
        if self.availability is MetricAvailability.AVAILABLE:
            if self.value is None:
                raise ValueError("an AVAILABLE metric must have a value")
            if self.source is None:
                raise ValueError("an AVAILABLE metric must declare its source")
        elif self.value is not None:
            raise ValueError(f"a {self.availability.value} metric must not carry a value")
        return self

    @property
    def is_available(self) -> bool:
        return self.availability is MetricAvailability.AVAILABLE

    @classmethod
    def of(
        cls, value: T, source: MetricSource, unit: str | None = None, note: str | None = None
    ) -> Metric[T]:
        return cls(
            value=value,
            availability=MetricAvailability.AVAILABLE,
            source=source,
            unit=unit,
            note=note,
        )

    @classmethod
    def unavailable(cls, note: str | None = None, unit: str | None = None) -> Metric[T]:
        return cls(availability=MetricAvailability.UNAVAILABLE, note=note, unit=unit)

    @classmethod
    def not_applicable(cls, note: str | None = None) -> Metric[T]:
        return cls(availability=MetricAvailability.NOT_APPLICABLE, note=note)


def _seconds() -> Metric[float]:
    return Metric[float](unit="s")


def _tokens() -> Metric[int]:
    return Metric[int](unit="tokens")


def _count() -> Metric[int]:
    return Metric[int]()


class TokenUsage(BaseModel):
    """Token counts as reported by the agent.

    Token counts are only comparable within one ``accounting_scheme``: providers
    tokenize and count (e.g. cached or reasoning tokens) differently.
    """

    model_config = ConfigDict(extra="forbid")

    accounting_scheme: str | None = Field(
        default=None,
        description="Provider/tokenizer family the counts belong to, e.g. 'anthropic', 'openai'.",
    )
    input_tokens: Metric[int] = Field(default_factory=_tokens)
    output_tokens: Metric[int] = Field(default_factory=_tokens)
    cached_input_tokens: Metric[int] = Field(default_factory=_tokens)
    reasoning_tokens: Metric[int] = Field(default_factory=_tokens)
    total_tokens: Metric[int] = Field(default_factory=_tokens)


class UsageMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: TokenUsage = Field(default_factory=TokenUsage)
    cost: Metric[float] = Field(default_factory=lambda: Metric[float](unit="USD"))
    turns: Metric[int] = Field(default_factory=_count)


class PerformanceMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_execution_seconds: Metric[float] = Field(default_factory=_seconds)
    time_to_first_implementation_seconds: Metric[float] = Field(default_factory=_seconds)
    time_to_all_tests_pass_seconds: Metric[float] = Field(default_factory=_seconds)
    total_wall_clock_seconds: Metric[float] = Field(default_factory=_seconds)


class QualityMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tests_passed: Metric[int] = Field(default_factory=_count)
    tests_failed: Metric[int] = Field(default_factory=_count)
    tests_total: Metric[int] = Field(default_factory=_count)
    repair_iterations: Metric[int] = Field(default_factory=_count)
    retry_count: Metric[int] = Field(default_factory=_count)
    manual_intervention_count: Metric[int] = Field(
        default_factory=lambda: Metric[int].of(0, MetricSource.MEASURED),
        description="Always 0 for a valid trial; trials are fully unattended.",
    )


class CodeMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines_of_code: Metric[int] = Field(default_factory=_count)
    files_changed: Metric[int] = Field(default_factory=_count)


class TrialMetrics(BaseModel):
    """All metrics captured for one trial."""

    model_config = ConfigDict(extra="forbid")

    performance: PerformanceMetrics = Field(default_factory=PerformanceMetrics)
    usage: UsageMetrics = Field(default_factory=UsageMetrics)
    quality: QualityMetrics = Field(default_factory=QualityMetrics)
    code: CodeMetrics = Field(default_factory=CodeMetrics)
    provider_specific: dict[str, Any] = Field(
        default_factory=dict,
        description="Provider-native metrics that do not fit the common schema, kept verbatim.",
    )
