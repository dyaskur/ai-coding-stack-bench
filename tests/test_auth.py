from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_benchmark.config import AgentAuthConfig, AgentSelection, CredentialFile, ExperimentConfig
from ai_benchmark.models import AuthMode, Metric, MetricSource, TrialMetrics
from ai_benchmark.orchestrator import apply_auth_policy
from ai_benchmark.orchestrator.policy import REPORTED_COST_KEY


def test_auth_lists_names_not_secret_values() -> None:
    with pytest.raises(ValidationError, match="environment variable name"):
        AgentAuthConfig(env=["sk-ant-oat01-abc123"])
    assert AgentAuthConfig(env=["CLAUDE_CODE_OAUTH_TOKEN"]).mode is AuthMode.SUBSCRIPTION


def test_auth_requires_some_credential() -> None:
    with pytest.raises(ValidationError):
        AgentAuthConfig()


def test_credential_target_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        CredentialFile(source=Path("~/.codex/auth.json"), target=".codex/auth.json")


def test_missing_reports_names_only(tmp_path: Path) -> None:
    present = tmp_path / "auth.json"
    present.write_text("{}")
    auth = AgentAuthConfig(
        env=["TOKEN_A", "TOKEN_B"],
        files=[
            CredentialFile(source=present, target="/home/agent/auth.json"),
            CredentialFile(source=tmp_path / "nope.json", target="/home/agent/x.json"),
        ],
    )
    problems = auth.missing({"TOKEN_A": "secret-value"})
    assert len(problems) == 2
    assert not any("secret-value" in p for p in problems)
    assert any("TOKEN_B" in p for p in problems) and any("nope.json" in p for p in problems)


def test_experiment_rejects_mixed_auth_modes() -> None:
    api = AgentAuthConfig(mode=AuthMode.API_KEY, env=["OPENAI_API_KEY"])
    with pytest.raises(ValidationError, match="separate experiment"):
        ExperimentConfig(
            task="t",
            agents=[AgentSelection(agent="codex", auth=api)],
            stacks=["fastapi"],
            auth_mode=AuthMode.SUBSCRIPTION,
        )


def test_subscription_cost_is_unavailable_but_preserved() -> None:
    m = TrialMetrics()
    m.usage.cost = Metric[float].of(1.25, MetricSource.AGENT_REPORTED, unit="USD")
    out = apply_auth_policy(m, AuthMode.SUBSCRIPTION)
    assert not out.usage.cost.is_available
    assert out.usage.cost.note and "subscription" in out.usage.cost.note
    assert out.provider_specific[REPORTED_COST_KEY] == 1.25
    assert m.usage.cost.is_available  # input not mutated


def test_api_key_cost_kept() -> None:
    m = TrialMetrics()
    m.usage.cost = Metric[float].of(1.25, MetricSource.AGENT_REPORTED, unit="USD")
    assert apply_auth_policy(m, AuthMode.API_KEY).usage.cost.value == 1.25
