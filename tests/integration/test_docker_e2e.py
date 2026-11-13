"""End-to-end trial against a real Docker daemon, with a scripted stand-in agent.

Run with: BENCHMARK_DOCKER_TESTS=1 pytest tests/integration

Hosts without outbound package mirrors can point the test at prebuilt images:
BENCHMARK_IT_STACK_IMAGE (needs git, curl and CA certificates already installed)
and BENCHMARK_IT_TESTER_IMAGE (needs pytest and httpx).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from ai_benchmark.agents import (
    AgentAdapter,
    AgentAvailability,
    AgentCapabilities,
    AgentImageLayer,
    AgentRegistry,
    AgentRunResult,
    AgentTask,
    CommandRunner,
)
from ai_benchmark.config import AgentAuthConfig, ExperimentConfig, SpecCatalog
from ai_benchmark.config.settings import DockerSettings, PlatformSettings
from ai_benchmark.environment import DockerExecutionEnvironment
from ai_benchmark.models import AuthMode, Metric, RateLimitEvent, TrialStatus, UsageMetrics
from ai_benchmark.orchestrator import BenchmarkOrchestrator
from ai_benchmark.storage import JsonResultStore

pytestmark = pytest.mark.skipif(
    os.environ.get("BENCHMARK_DOCKER_TESTS") != "1"
    or subprocess.run(["docker", "info"], capture_output=True).returncode != 0,
    reason="set BENCHMARK_DOCKER_TESTS=1 with a running Docker daemon",
)

FIXTURES = Path(__file__).parent / "fixtures"
STACK_IMAGE = os.environ.get("BENCHMARK_IT_STACK_IMAGE", "python:3.12.7-slim-bookworm")
TESTER_IMAGE = os.environ.get("BENCHMARK_IT_TESTER_IMAGE")

APP = """\
import json, os, socket
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/cache":
            s = socket.create_connection(("cache", 6379), timeout=5)
            s.sendall(b"PING\\r\\n")
            body = {"cache": s.recv(64).decode().strip().lstrip("+")}
        else:
            body = {"status": "ok"}
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

HTTPServer(("0.0.0.0", 8000), H).serve_forever()
"""


class ScriptedAgent(AgentAdapter):
    """Writes a tiny app through the CommandRunner, like a real agent would."""

    name = "scripted"
    display_name = "Scripted agent"

    def capabilities(self) -> AgentCapabilities:
        return AgentCapabilities()

    def image_layer(self) -> AgentImageLayer:
        return AgentImageLayer(agent="scripted", cli_version="1", install_commands=["true"])

    def default_auth(self, mode: AuthMode) -> AgentAuthConfig:
        return AgentAuthConfig(mode=mode, env=["SCRIPTED_TOKEN"])

    def get_version(self, runner: CommandRunner) -> str | None:
        return runner.run(["sh", "-c", "echo scripted-1"], timeout_seconds=30).stdout.strip()

    def check_availability(self, runner: CommandRunner) -> AgentAvailability:
        return AgentAvailability(installed=True)

    def build_command(self, task: AgentTask) -> list[str]:
        app = APP.replace("'", "'\\''")
        return [
            "sh",
            "-c",
            f"test -n \"$SCRIPTED_TOKEN\" && whoami && printf '%s' '{app}' > app.py "
            "&& printf 'exec python app.py\\n' > start.sh",
        ]

    def run(self, task: AgentTask, runner: CommandRunner) -> AgentRunResult:
        start = datetime.now(timezone.utc)
        res = runner.run(
            self.build_command(task),
            timeout_seconds=task.timeout_seconds,
            workdir=task.workdir,
            stdout_path=task.log_dir / "agent.stdout.log",
            stderr_path=task.log_dir / "agent.stderr.log",
        )
        return AgentRunResult(
            exit_code=res.exit_code,
            timed_out=res.timed_out,
            started_at=start,
            finished_at=datetime.now(timezone.utc),
            stdout_path=task.log_dir / "agent.stdout.log",
            stderr_path=task.log_dir / "agent.stderr.log",
        )

    def parse_usage(self, raw: dict[str, Any]) -> tuple[UsageMetrics, Metric[str]]:
        return UsageMetrics(), Metric[str]()

    def detect_rate_limits(
        self, stdout: str, stderr: str, raw: dict[str, Any]
    ) -> list[RateLimitEvent]:
        return []


def test_full_trial_in_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCRIPTED_TOKEN", "do-not-leak")
    tasks = tmp_path / "tasks" / "hello"
    tasks.mkdir(parents=True)
    shutil.copytree(FIXTURES / "acceptance", tasks / "acceptance")
    tester = f", image: {TESTER_IMAGE}" if TESTER_IMAGE else ""
    (tasks / "task.yaml").write_text(
        "id: hello\nname: Hello\ndescription: d\nfunctional_requirements: [serve /]\n"
        "prompt_template: build $framework\nsupported_stacks: [py]\n"
        f"acceptance_tests: {{suite: acceptance{tester}}}\n"
        "timeouts: {setup_seconds: 300, agent_seconds: 120, tests_seconds: 300}\n"
    )
    stacks = tmp_path / "stacks"
    stacks.mkdir()
    (stacks / "py.yaml").write_text(
        "id: py\nlanguage: python\nlanguage_version: '3.12'\nframework: stdlib\n"
        f"framework_version: '3.12'\nimage: {STACK_IMAGE}\n"
        "services:\n  - name: cache\n    image: redis:7.4.1-alpine\n"
        "    healthcheck: [redis-cli, ping]\n"
    )
    settings = PlatformSettings(
        tasks_dir=tmp_path / "tasks",
        stacks_dir=stacks,
        results_dir=tmp_path / "results",
        workspaces_dir=tmp_path / "ws",
        docker=DockerSettings(),
    )
    registry = AgentRegistry()
    registry._entry_points_loaded = True
    registry.register(ScriptedAgent)
    env = DockerExecutionEnvironment(settings.docker, run_id="it")
    o = BenchmarkOrchestrator(
        settings, SpecCatalog(settings), registry, env, JsonResultStore(settings.results_dir)
    )
    cfg = ExperimentConfig.model_validate(
        {"task": "hello", "agents": [{"agent": "scripted", "model": "none"}], "stacks": ["py"]}
    )
    summary = o.run(cfg, run_id="it")
    [r] = list(o.store.iter_trials())
    assert r.status is TrialStatus.PASSED, (r.error, r.log_files)
    assert summary.by_status == {"passed": 1}
    assert r.metrics.quality.tests_passed.value == 2
    assert r.metrics.code.files_changed.value == 2
    assert r.agent.version == "scripted-1"
    who = Path(r.log_files["agent-stdout"]).read_text().strip()
    assert who != "root"
    leaked = [p for p in (tmp_path / "results").rglob("*") if p.is_file()
              and "do-not-leak" in p.read_text(errors="ignore")]  # fmt: skip
    assert not leaked
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=io.ai-benchmark.run_id=it"],
        capture_output=True, text=True,
    ).stdout.split()  # fmt: skip
    assert left == []
