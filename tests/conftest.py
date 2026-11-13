from __future__ import annotations

from pathlib import Path

import pytest

from ai_benchmark.config.settings import PlatformSettings

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path: Path) -> PlatformSettings:
    return PlatformSettings(
        tasks_dir=REPO_ROOT / "benchmarks" / "tasks",
        stacks_dir=REPO_ROOT / "benchmarks" / "stacks",
        results_dir=tmp_path / "results",
        workspaces_dir=tmp_path / "workspaces",
    )
