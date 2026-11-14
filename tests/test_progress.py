from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import pytest

from ai_benchmark.progress import (
    AgentHeartbeat,
    BuildHeartbeat,
    format_duration,
    last_build_step,
    read_progress,
    summarize_line,
)


def test_format_duration() -> None:
    assert format_duration(0) == "0:00"
    assert format_duration(75.9) == "1:15"
    assert format_duration(3725) == "1:02:05"
    assert format_duration(-3) == "0:00"


def test_summarize_codex_and_opencode_events() -> None:
    codex = {
        "type": "item.completed",
        "item": {"type": "command_execution", "command": "php  artisan migrate"},
    }
    assert (
        summarize_line(json.dumps(codex))
        == "item.completed · command_execution · php artisan migrate"
    )
    opencode = {"type": "tool_use", "part": {"type": "tool", "tool": "edit"}}
    assert summarize_line(json.dumps(opencode)) == "tool_use · tool · edit"
    assert summarize_line(json.dumps({"type": "turn.started"})) == "turn.started"


def test_summarize_plain_text_is_truncated() -> None:
    assert summarize_line("  hello  ") == "hello"
    long = summarize_line("x" * 500)
    assert len(long) == 100 and long.endswith("…")
    assert summarize_line("[1, 2]") == "[1, 2]"


def test_read_progress(tmp_path: Path) -> None:
    path = tmp_path / "agent.stdout.log"
    assert read_progress(path) == (0, None)
    path.write_text('{"type": "thread.started"}\n{"type": "turn.started"}\n\n')
    assert read_progress(path) == (3, "turn.started")


def test_heartbeat_logs_while_running(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "agent.stdout.log"
    path.write_text('{"type": "turn.started"}\n')
    caplog.set_level(logging.INFO, logger="ai_benchmark.progress")
    with AgentHeartbeat("t-1", path, limit_seconds=1800, interval=0.05):
        time.sleep(0.2)
    beats = [r.getMessage() for r in caplog.records]
    assert beats, "expected at least one heartbeat"
    assert beats[0].startswith("trial t-1: agent running 0:00 / 30:00, 1 output lines")
    assert beats[0].endswith("last: turn.started")


def test_heartbeat_disabled_with_zero_interval(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ai_benchmark.progress")
    with AgentHeartbeat("t-1", tmp_path / "missing.log", limit_seconds=60, interval=0):
        time.sleep(0.05)
    assert not caplog.records


def test_last_build_step_buildkit_and_classic(tmp_path: Path) -> None:
    path = tmp_path / "build.log"
    assert last_build_step(path) is None
    path.write_text(
        "#5 [2/6] RUN set -e; apt-get update\n#5 DONE 3.1s\n"
        "#7 [4/6] RUN npm install -g --no-audit --no-fund @openai/codex@0.167.2\n"
        "#7 12.3 npm warn deprecated something\n"
    )
    assert last_build_step(path) == (
        "#7 [4/6] RUN npm install -g --no-audit --no-fund @openai/codex@0.167.2"
    )
    path.write_text("Step 3/8 : RUN useradd agent\n ---> Running in abc\n")
    assert last_build_step(path) == "Step 3/8 : RUN useradd agent"


def test_build_heartbeat_reports_step(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "build.log"
    path.write_text("#4 [3/6] RUN curl nodejs\n")
    caplog.set_level(logging.INFO, logger="ai_benchmark.progress")
    with BuildHeartbeat("img:1", path, interval=0.05):
        time.sleep(0.2)
    assert caplog.records
    assert caplog.records[0].getMessage() == "building img:1: 0:00, at: #4 [3/6] RUN curl nodejs"
