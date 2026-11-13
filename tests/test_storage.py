from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_benchmark.errors import ResultStoreError
from ai_benchmark.models import (
    AgentRecord,
    AuthMode,
    EnvironmentRecord,
    RateLimitEvent,
    RunManifest,
    ToolingMode,
    ToolingRecord,
    TrialResult,
    TrialStatus,
)
from ai_benchmark.storage import JsonResultStore, ResultFilter


def manifest(run_id: str) -> RunManifest:
    return RunManifest(
        run_id=run_id,
        created_at=datetime.now(timezone.utc),
        platform_version="0.1.0",
        seed=1,
        auth_mode=AuthMode.SUBSCRIPTION,
        experiment={},
        task_snapshot={},
    )


def trial(
    run_id: str, trial_id: str, agent: str, status: TrialStatus, limited: bool = False
) -> TrialResult:
    return TrialResult(
        rate_limit_events=[RateLimitEvent(message="usage limit reached")] if limited else [],
        trial_id=trial_id,
        run_id=run_id,
        task_id="task",
        task_version="1",
        trial_index=0,
        execution_order=0,
        status=status,
        agent=AgentRecord(name=agent, auth_mode=AuthMode.SUBSCRIPTION),
        environment=EnvironmentRecord(
            stack="fastapi",
            language="python",
            language_version="3.12",
            framework="fastapi",
            framework_version="0.115",
        ),
        tooling=ToolingRecord(mode=ToolingMode.STANDARD),
    )


def test_save_and_filter(tmp_path: Path) -> None:
    store = JsonResultStore(tmp_path)
    store.save_manifest(manifest("r1"))
    store.save_trial(trial("r1", "t1", "a", TrialStatus.PASSED))
    store.save_trial(trial("r1", "t2", "b", TrialStatus.AGENT_ERROR, limited=True))

    assert [m.run_id for m in store.list_runs()] == ["r1"]
    assert len(list(store.iter_trials())) == 2
    failed = list(store.iter_trials(ResultFilter(status=TrialStatus.AGENT_ERROR)))
    assert [t.trial_id for t in failed] == ["t2"]
    assert [t.trial_id for t in store.iter_trials(ResultFilter(agent="a"))] == ["t1"]
    limited = list(store.iter_trials(ResultFilter(rate_limited=True)))
    assert [t.trial_id for t in limited] == ["t2"] and limited[0].rate_limited
    assert len(list(store.iter_trials(ResultFilter(auth_mode=AuthMode.API_KEY)))) == 0


def test_trial_requires_manifest(tmp_path: Path) -> None:
    with pytest.raises(ResultStoreError):
        JsonResultStore(tmp_path).save_trial(trial("r9", "t1", "a", TrialStatus.PASSED))


def test_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(ResultStoreError):
        JsonResultStore(tmp_path).load_manifest("../x")
