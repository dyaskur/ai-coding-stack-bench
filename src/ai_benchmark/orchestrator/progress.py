"""Live progress for a running trial.

The agent phase can take tens of minutes with nothing on the console, so a
background thread logs a heartbeat while it runs: elapsed time against the
limit, how much output the agent has written, and a one-line summary of its
latest event. It only reads the agent's log file, which the environment
streams to disk, so it never touches the container or the agent.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import Any

log = logging.getLogger(__name__)

_SUMMARY_CHARS = 100
_TAIL_BYTES = 64 * 1024


def format_duration(seconds: float) -> str:
    """Seconds as ``m:ss``, or ``h:mm:ss`` from one hour."""
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def summarize_line(line: str) -> str:
    """A short, human-readable label for one line of agent output.

    JSON events (Codex and OpenCode write JSONL) are reduced to their event
    type plus the nested item type or a short text when present; anything else
    is the line itself, truncated.
    """
    line = line.strip()
    try:
        event = json.loads(line)
    except ValueError:
        event = None
    if not isinstance(event, dict):
        return _truncate(line)
    parts = [str(event.get("type", "event"))]
    item = event.get("item") or event.get("part")
    if isinstance(item, dict):
        detail = item.get("type")
        if detail:
            parts.append(str(detail))
        text = _first_text(item)
        if text:
            parts.append(text)
    return _truncate(" · ".join(parts))


def _first_text(item: dict[str, Any]) -> str | None:
    for key in ("command", "text", "tool", "path"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return None


def _truncate(text: str) -> str:
    return text if len(text) <= _SUMMARY_CHARS else text[: _SUMMARY_CHARS - 1] + "…"


def read_progress(path: Path) -> tuple[int, str | None]:
    """Count the lines in ``path`` and summarize its last non-empty one."""
    try:
        with path.open("rb") as fh:
            lines = sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 16), b""))
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return 0, None
    last = next((ln for ln in reversed(tail.splitlines()) if ln.strip()), None)
    return lines, summarize_line(last) if last is not None else None


class AgentHeartbeat:
    """Logs the agent's progress every ``interval`` seconds while in a ``with`` block."""

    def __init__(self, trial_id: str, output: Path, limit_seconds: int, interval: float) -> None:
        self.trial_id = trial_id
        self.output = output
        self.limit_seconds = limit_seconds
        self.interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = 0.0

    def __enter__(self) -> AgentHeartbeat:
        self._started = time.monotonic()
        if self.interval > 0:
            self._thread = threading.Thread(
                target=self._loop, name=f"heartbeat-{self.trial_id}", daemon=True
            )
            self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.beat()

    def beat(self) -> None:
        lines, last = read_progress(self.output)
        log.info(
            "trial %s: agent running %s / %s, %d output lines%s",
            self.trial_id,
            format_duration(time.monotonic() - self._started),
            format_duration(self.limit_seconds),
            lines,
            f", last: {last}" if last else "",
        )
