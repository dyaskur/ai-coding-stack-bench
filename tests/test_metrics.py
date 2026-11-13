import pytest
from pydantic import ValidationError

from ai_benchmark.models import Metric, MetricAvailability, MetricSource, TrialMetrics


def test_default_metrics_are_unavailable_not_zero() -> None:
    m = TrialMetrics()
    assert m.usage.tokens.input_tokens.value is None
    assert m.usage.tokens.input_tokens.availability is MetricAvailability.UNAVAILABLE
    assert m.usage.cost.unit == "USD"
    assert not m.performance.agent_execution_seconds.is_available


def test_zero_is_a_real_value() -> None:
    m = Metric[int].of(0, MetricSource.AGENT_REPORTED)
    assert m.is_available and m.value == 0


def test_available_requires_value_and_source() -> None:
    with pytest.raises(ValidationError):
        Metric[int](availability=MetricAvailability.AVAILABLE)
    with pytest.raises(ValidationError):
        Metric[int](value=3, availability=MetricAvailability.AVAILABLE)


def test_unavailable_cannot_carry_value() -> None:
    with pytest.raises(ValidationError):
        Metric[int](value=5, availability=MetricAvailability.UNAVAILABLE)


def test_round_trip_json() -> None:
    m = TrialMetrics()
    m.usage.tokens.output_tokens = Metric[int].of(42, MetricSource.AGENT_REPORTED, unit="tokens")
    restored = TrialMetrics.model_validate_json(m.model_dump_json())
    assert restored.usage.tokens.output_tokens.value == 42
    assert not restored.usage.tokens.input_tokens.is_available
