"""Auth-mode policies applied to collected metrics.

Kept separate from adapters so every agent is treated identically.
"""

from __future__ import annotations

from ai_benchmark.models.enums import AuthMode
from ai_benchmark.models.metrics import Metric, TrialMetrics

SUBSCRIPTION_COST_NOTE = "subscription auth: no per-run cost is billed"
REPORTED_COST_KEY = "agent_reported_cost_usd"


def apply_auth_policy(metrics: TrialMetrics, mode: AuthMode) -> TrialMetrics:
    """Return metrics adjusted for how the agent was billed.

    Under a subscription nothing is billed per run, so ``usage.cost`` becomes
    unavailable. If the agent still printed a cost figure (some CLIs show an
    API-price equivalent), it is kept verbatim in ``provider_specific`` so no
    data is lost, but it is never presented as the run's cost.
    """
    if mode is not AuthMode.SUBSCRIPTION:
        return metrics
    cost = metrics.usage.cost
    provider_specific = dict(metrics.provider_specific)
    if cost.is_available:
        provider_specific[REPORTED_COST_KEY] = cost.value
    usage = metrics.usage.model_copy(
        update={"cost": Metric[float].unavailable(note=SUBSCRIPTION_COST_NOTE, unit="USD")}
    )
    return metrics.model_copy(update={"usage": usage, "provider_specific": provider_specific})
