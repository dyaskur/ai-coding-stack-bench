"""Expanding an experiment configuration into an ordered list of trials.

Pure logic with no side effects, so the full matrix can be inspected with
``benchmark run --dry-run`` before anything executes.
"""

from __future__ import annotations

import hashlib
import random
import secrets

from pydantic import BaseModel, ConfigDict, Field

from ai_benchmark.config.experiment import AgentSelection, ExperimentConfig
from ai_benchmark.config.stack import StackSpec
from ai_benchmark.config.task import BenchmarkSpec, Timeouts
from ai_benchmark.errors import PlanningError
from ai_benchmark.models.enums import AuthMode


class PlannedTrial(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trial_id: str
    execution_order: int = Field(ge=0)
    trial_index: int = Field(ge=0, description="0-based repetition number for this combination.")
    agent: AgentSelection
    stack_id: str

    @property
    def combination(self) -> str:
        return f"{self.agent.key} @ {self.stack_id}"


class ExperimentPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    seed: int
    task_id: str
    trials_per_combination: int
    auth_mode: AuthMode
    timeouts: Timeouts
    trials: list[PlannedTrial]


def _trial_id(run_id: str, agent_key: str, stack_id: str, index: int) -> str:
    digest = hashlib.sha256(f"{run_id}|{agent_key}|{stack_id}|{index}".encode()).hexdigest()
    return f"t-{digest[:12]}"


def build_plan(
    config: ExperimentConfig,
    task: BenchmarkSpec,
    stacks: dict[str, StackSpec],
    run_id: str,
) -> ExperimentPlan:
    """Validate the matrix against the task/stack catalog and order the trials.

    Ordering is shuffled (unless disabled) with a recorded seed, so the same
    seed always reproduces the same order.
    """
    if config.task != task.id:
        raise PlanningError(f"config is for task '{config.task}' but got spec '{task.id}'")
    unknown = [s for s in config.stacks if s not in stacks]
    if unknown:
        raise PlanningError(f"unknown stacks: {', '.join(unknown)}")
    unsupported = [s for s in config.stacks if not task.supports(s)]
    if unsupported:
        raise PlanningError(
            f"task '{task.id}' does not support stacks: {', '.join(unsupported)} "
            f"(supported: {', '.join(task.supported_stacks)})"
        )

    seed = config.seed if config.seed is not None else secrets.randbits(32)
    n_trials = config.trials or task.default_trials

    combos = [
        (agent, stack_id, i)
        for agent in config.agents
        for stack_id in config.stacks
        for i in range(n_trials)
    ]
    if config.randomize_order:
        random.Random(seed).shuffle(combos)

    trials = [
        PlannedTrial(
            trial_id=_trial_id(run_id, agent.key, stack_id, i),
            execution_order=order,
            trial_index=i,
            agent=agent,
            stack_id=stack_id,
        )
        for order, (agent, stack_id, i) in enumerate(combos)
    ]
    return ExperimentPlan(
        run_id=run_id,
        seed=seed,
        task_id=task.id,
        trials_per_combination=n_trials,
        auth_mode=config.auth_mode,
        timeouts=config.timeouts or task.timeouts,
        trials=trials,
    )
