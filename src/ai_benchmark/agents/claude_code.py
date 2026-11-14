"""Claude Code (``claude``) adapter.

Checked against the official docs (code.claude.com/docs/en/headless,
cli-reference, authentication, env-vars) and the pinned CLI itself:

* ``claude -p <prompt> --output-format json`` prints one JSON result object
  with ``is_error``, ``result``, ``num_turns``, ``total_cost_usd``, ``usage``
  (Anthropic message usage) and ``modelUsage`` (per model).
* ``--permission-mode bypassPermissions`` runs unattended. The container is
  the sandbox, and the CLI refuses this mode as root, which we never are.
* ``--strict-mcp-config`` without ``--mcp-config`` loads no MCP servers, so
  every agent starts from the same tool set in ``standard`` tooling mode.
* Subscription auth: ``CLAUDE_CODE_OAUTH_TOKEN`` from ``claude setup-token``.
  API auth: ``ANTHROPIC_API_KEY``. Only the variable for the chosen mode is
  passed in, so an API key can never silently take over a subscription run.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

from ai_benchmark.agents.base import AgentCapabilities, AgentTask
from ai_benchmark.agents.cli import (
    CliAgentAdapter,
    ParsedOutput,
    int_or_none,
    json_lines,
    token_metric,
)
from ai_benchmark.agents.registry import register_agent
from ai_benchmark.config.auth import AgentAuthConfig
from ai_benchmark.environment.base import AgentImageLayer
from ai_benchmark.models.enums import AuthMode, MetricSource
from ai_benchmark.models.metrics import Metric, TokenUsage, UsageMetrics

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@register_agent
class ClaudeCodeAdapter(CliAgentAdapter):
    name = "claude"
    display_name = "Claude Code"
    executable = "claude"
    cli_version: ClassVar[str] = "2.1.323"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities(
            structured_output=True,
            reports_token_usage=True,
            reports_cost=True,
            reports_turns=True,
            reports_rate_limits=True,
            supports_reasoning_effort=True,
            token_accounting_scheme="anthropic",
        )

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(
            agent=self.name,
            cli_version=self.cli_version,
            requires=["node"],
            install_commands=[
                f"npm install -g --no-audit --no-fund @anthropic-ai/claude-code@{self.cli_version}"
            ],
            env={
                # Pinned version: no background updates, telemetry or error reports.
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
        )

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        if mode is AuthMode.SUBSCRIPTION:
            return AgentAuthConfig(mode=mode, env=["CLAUDE_CODE_OAUTH_TOKEN"])
        return AgentAuthConfig(mode=mode, env=["ANTHROPIC_API_KEY"])

    def build_command(self, task: AgentTask) -> list[str]:
        cmd = [
            self.executable,
            "--print",
            "--output-format",
            "json",
            "--permission-mode",
            "bypassPermissions",
            "--strict-mcp-config",
        ]
        if task.model:
            cmd += ["--model", task.model]
        if task.reasoning_effort:
            cmd += ["--effort", task.reasoning_effort]
        return [*cmd, "--", task.prompt]

    def validate_options(self, options: dict[str, Any]) -> None:
        if options:
            raise ValueError(f"claude takes no options, got {sorted(options)}")

    # -- output ---------------------------------------------------------------

    def parse_output(self, stdout: str) -> ParsedOutput:
        results = [o for o in json_lines(stdout) if "result" in o or o.get("type") == "result"]
        if not results:
            try:
                value = json.loads(stdout)
            except json.JSONDecodeError:
                value = None
            results = [value] if isinstance(value, dict) else []
        if not results:
            return ParsedOutput()
        raw = results[-1]
        errors = []
        if raw.get("is_error") or str(raw.get("subtype", "")).startswith("error"):
            errors.append(str(raw.get("result") or raw.get("subtype") or "error"))
        return ParsedOutput(raw=raw, errors=errors)

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        assert isinstance(usage, dict)
        fresh = int_or_none(usage.get("input_tokens"))
        written = int_or_none(usage.get("cache_creation_input_tokens")) or 0
        read = int_or_none(usage.get("cache_read_input_tokens"))
        output = int_or_none(usage.get("output_tokens"))
        details = usage.get("output_tokens_details")
        thinking = (
            int_or_none(details.get("thinking_tokens")) if isinstance(details, dict) else None
        )
        # Anthropic counts cache reads and writes outside input_tokens. Normalise to
        # "all input tokens, of which cached" so the fields mean the same per agent.
        total_in = None if fresh is None else fresh + written + (read or 0)
        tokens = TokenUsage(
            accounting_scheme="anthropic",
            input_tokens=token_metric(total_in, "input + cache writes + cache reads"),
            cached_input_tokens=token_metric(read),
            output_tokens=token_metric(output),
            reasoning_tokens=token_metric(thinking),
            total_tokens=token_metric(
                None if total_in is None or output is None else total_in + output
            ),
        )
        cost = raw.get("total_cost_usd")
        turns = int_or_none(raw.get("num_turns"))
        metrics = UsageMetrics(
            tokens=tokens,
            cost=(
                Metric[float].of(float(cost), MetricSource.AGENT_REPORTED, unit="USD")
                if isinstance(cost, (int, float)) and not isinstance(cost, bool)
                else Metric[float].unavailable("not reported by the agent", unit="USD")
            ),
            turns=(
                Metric[int].of(turns, MetricSource.AGENT_REPORTED, note="agentic turns")
                if turns is not None
                else Metric[int].unavailable("not reported by the agent")
            ),
        )
        return metrics, _main_model(raw.get("modelUsage"))


def _main_model(model_usage: Any) -> Metric[str]:
    """The model that produced most output; helpers (e.g. a small model) rank below it."""
    if not isinstance(model_usage, dict) or not model_usage:
        return Metric[str].unavailable("not reported by the agent")

    def output(item: tuple[str, Any]) -> int:
        stats = item[1] if isinstance(item[1], dict) else {}
        return int_or_none(stats.get("outputTokens")) or 0

    name = max(model_usage.items(), key=output)[0]
    note = None
    if len(model_usage) > 1:
        note = "main model; also used: " + ", ".join(sorted(k for k in model_usage if k != name))
    return Metric[str].of(name, MetricSource.AGENT_REPORTED, note=note)
