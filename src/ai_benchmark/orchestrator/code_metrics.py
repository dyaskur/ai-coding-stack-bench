"""Git-based workspace snapshots and code metrics, run inside the trial container."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ai_benchmark.environment.base import CommandRunner
from ai_benchmark.errors import ExecutionEnvironmentError

# Dependency, build and cache directories never count as written code.
IGNORED_PATHS = (
    "node_modules/",
    "vendor/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    "dist/",
    "build/",
    "storage/framework/",
    "bootstrap/cache/",
    "*.lock",
    "package-lock.json",
    "*.log",
    ".claude/",
    ".codex/",
    ".opencode/",
)
_GIT = ["git", "-c", "user.name=ai-benchmark", "-c", "user.email=ai-benchmark@localhost"]


class CodeChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    commit: str
    lines_added: int
    lines_deleted: int
    files_changed: int


def _sh(runner: CommandRunner, script: str, workdir: str, timeout: int = 300) -> str:
    res = runner.run(["sh", "-c", script], timeout_seconds=timeout, workdir=workdir)
    if not res.ok:
        raise ExecutionEnvironmentError(f"workspace git step failed: {res.stderr.strip()[-500:]}")
    return res.stdout


def snapshot_baseline(runner: CommandRunner, workdir: str) -> str:
    """Make the initial project state a git commit and tag it ``ai-benchmark-baseline``."""
    excludes = "\\n".join(IGNORED_PATHS)
    git = " ".join(_GIT)
    script = (
        "set -e; [ -d .git ] || git init -q; "
        f"printf '{excludes}\\n' >> .git/info/exclude; "
        f"git add -A && {git} commit -q --allow-empty -m 'ai-benchmark: baseline' && "
        "git tag -f ai-benchmark-baseline >/dev/null && git rev-parse HEAD"
    )
    return _sh(runner, script, workdir).strip()


def snapshot_result(runner: CommandRunner, workdir: str) -> CodeChange:
    """Commit whatever the agent left and measure it against the baseline."""
    git = " ".join(_GIT)
    commit = _sh(
        runner,
        f"set -e; git add -A && {git} commit -q --allow-empty -m 'ai-benchmark: agent result' "
        "--no-verify && git rev-parse HEAD",
        workdir,
    ).strip()
    numstat = _sh(runner, "git diff --numstat ai-benchmark-baseline HEAD", workdir)
    added = deleted = files = 0
    for line in numstat.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        if parts[0] != "-":  # binary files report '-'
            added += int(parts[0])
            deleted += int(parts[1])
    return CodeChange(commit=commit, lines_added=added, lines_deleted=deleted, files_changed=files)
