"""OpenCode (``opencode``) adapter.

Checked against the official docs (opencode.ai/docs: cli, providers, config,
permissions) and the pinned CLI's own ``opencode run --help`` and source:

* ``opencode run --format json`` prints JSONL events
  ``{"type", "timestamp", "sessionID", ...}``. ``step_finish`` events carry a
  ``part`` with ``tokens`` (``input``, ``output``, ``reasoning``,
  ``cache.read``, ``cache.write``) and ``cost`` per model step; ``error``
  events carry ``error`` (``name``, ``data.message``).
* ``--auto`` auto-approves every permission that is not explicitly denied.
* Models are ``provider/model``; ``--variant`` is the provider's reasoning
  effort (e.g. high, max).
* Subscription auth: ``~/.local/share/opencode/auth.json`` from
  ``opencode auth login``. API auth depends on the provider; the variable
  name comes from the ``api_key_env`` option (default ``ANTHROPIC_API_KEY``)
  or the experiment's ``auth`` block.
* OpenCode does not report which model actually answered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from ai_benchmark.agents.base import AgentCapabilities, AgentTask
from ai_benchmark.agents.cli import (
    AGENT_HOME,
    CliAgentAdapter,
    ParsedOutput,
    json_lines,
    token_metric,
)
from ai_benchmark.agents.registry import register_agent
from ai_benchmark.config.auth import AgentAuthConfig, CredentialFile
from ai_benchmark.environment.base import AgentImageLayer
from ai_benchmark.models.enums import AuthMode, MetricSource
from ai_benchmark.models.metrics import Metric, TokenUsage, UsageMetrics

_OPTIONS = {"api_key_env"}


@register_agent
class OpenCodeAdapter(CliAgentAdapter):
    name = "opencode"
    display_name = "OpenCode"
    executable = "opencode"
    cli_version: ClassVar[str] = "1.19.5"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_cost=True,
            reports_turns=True,
            supports_reasoning_effort=True,
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            requires=["node"],
            install_commands=[
                f"npm install -g --no-audit --no-fund opencode-ai@{self.cli_version}"
            ],
            env={"OPENCODE_DISABLE_AUTOUPDATE": "1"},
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            return AgentAuthConfig(
                mode=mode,
                files=[
                    CredentialFile(
                        source=Path("~/.local/share/opencode/auth.json"),
                        target=f"{AGENT_HOME}/.local/share/opencode/auth.json",
                    )
                ],
            )
        return AgentAuthConfig(
            mode=mode, env=[str(self.options.get("api_key_env", "ANTHROPIC_API_KEY"))]
        )

    def build_command(self, task: AgentTask) -> list[str]:
        cmd = [self.executable, "run", "--format", "json", "--auto"]
        if task.model:
            cmd += ["--model", task.model]
        if task.reasoning_effort:
            cmd += ["--variant", task.reasoning_effort]
        return [*cmd, "--", task.prompt]

    def validate_options(self, options: dict[str, Any]) -> None:
        unknown = set(options) - _OPTIONS
        if unknown:
            raise ValueError(
                f"unknown opencode options {sorted(unknown)}; known: {sorted(_OPTIONS)}"
            )

    # -- output ---------------------------------------------------------------

    def parse_output(self, stdout: str) -> ParsedOutput:
        steps: list[dict[str, Any]] = []
        errors: list[str] = []
        session_id = None
        for event in json_lines(stdout):
            session_id = session_id or event.get("sessionID")
            kind = event.get("type")
            part = event.get("part") if isinstance(event.get("part"), dict) else {}
            assert isinstance(part, dict)
            if kind == "step_finish":
                steps.append({"tokens": part.get("tokens"), "cost": part.get("cost")})
            elif kind == "error":
                errors.append(_error_message(event.get("error")))
        raw = {"session_id": session_id, "steps": steps, "errors": errors}
        return ParsedOutput(raw=raw, errors=errors)

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        steps = [s for s in raw.get("steps") or [] if isinstance(s, dict)]
        sums: dict[str, int] = {}
        seen: set[str] = set()
        cost = 0.0
        has_cost = False
        for step in steps:
            tokens = step.get("tokens") if isinstance(step.get("tokens"), dict) else {}
            assert isinstance(tokens, dict)
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            assert isinstance(cache, dict)
            for key, value in (
                ("input", tokens.get("input")),
                ("output", tokens.get("output")),
                ("reasoning", tokens.get("reasoning")),
                ("cache_read", cache.get("read")),
                ("cache_write", cache.get("write")),
            ):
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    sums[key] = sums.get(key, 0) + int(value)
                    seen.add(key)
            if isinstance(step.get("cost"), (int, float)):
                cost += float(step["cost"])
                has_cost = True

        def get(key: str) -> int | None:
            return sums[key] if key in seen else None

        fresh, out = get("input"), get("output")
        read, written = get("cache_read"), get("cache_write") or 0
        # OpenCode reports cache reads/writes outside "input"; normalise like Claude Code.
        total_in = None if fresh is None else fresh + written + (read or 0)
        tokens_metrics = TokenUsage(
            accounting_scheme=None,  # depends on the provider behind the model
            input_tokens=token_metric(total_in, "input + cache writes + cache reads"),
            cached_input_tokens=token_metric(read),
            output_tokens=token_metric(out),
            reasoning_tokens=token_metric(get("reasoning")),
            total_tokens=token_metric(None if total_in is None or out is None else total_in + out),
        )
        usage = UsageMetrics(
            tokens=tokens_metrics,
            cost=(
                Metric[float].of(cost, MetricSource.AGENT_REPORTED, unit="USD")
                if has_cost
                else Metric[float].unavailable("not reported by the agent", unit="USD")
            ),
            turns=(
                Metric[int].of(len(steps), MetricSource.AGENT_REPORTED, note="model steps")
                if steps
                else Metric[int].unavailable("not reported by the agent")
            ),
        )
        return usage, Metric[str].unavailable("opencode does not report the model used")


def _error_message(error: Any) -> str:
    if not isinstance(error, dict):
        return str(error)
    data = error.get("data")
    if isinstance(data, dict) and data.get("message"):
        status = data.get("statusCode")
        return f"{data['message']} (HTTP {status})" if status else str(data["message"])
    return str(error.get("name") or error)
