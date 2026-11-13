"""Benchmark task specification (``benchmarks/tasks/<id>/task.yaml``).

Tasks are independent of agents and stacks. Adding a task never requires
changing the orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from string import Template

from pydantic import Field, model_validator

from ai_benchmark.config._base import SLUG_PATTERN, StrictModel
from ai_benchmark.models.enums import TaskMode


class InitialState(StrictModel):
    mode: TaskMode = TaskMode.BUILD_FROM_SCRATCH
    template: Path | None = Field(
        default=None,
        description="Directory (relative to the task file) copied into the workspace. "
        "May contain one sub-directory per stack id.",
    )

    @model_validator(mode="after")
    def _template_for_extend(self) -> InitialState:
        if self.mode is TaskMode.EXTEND_EXISTING and self.template is None:
            raise ValueError("'extend_existing' tasks must provide an initial 'template'")
        return self


class AcceptanceTests(StrictModel):
    """How the independent acceptance suite is run. Execution itself lands in Phase 2."""

    suite: Path | None = Field(
        default=None, description="Test files mounted read-only after the agent finishes."
    )
    command: list[str] | None = Field(default=None, description="Default command for all stacks.")
    per_stack: dict[str, list[str]] = Field(
        default_factory=dict, description="Stack id -> command, overriding 'command'."
    )
    result_format: str = Field(default="junit", description="How passed/failed counts are parsed.")

    def command_for(self, stack_id: str) -> list[str] | None:
        return self.per_stack.get(stack_id, self.command)


class Timeouts(StrictModel):
    setup_seconds: int = Field(default=600, gt=0)
    agent_seconds: int = Field(default=1800, gt=0)
    tests_seconds: int = Field(default=600, gt=0)


class ExtensionPhase(StrictModel):
    """A follow-up change request applied on top of the previous phase's result."""

    id: str = Field(pattern=SLUG_PATTERN)
    description: str
    prompt_template: str
    acceptance_tests: AcceptanceTests | None = None


class BenchmarkSpec(StrictModel):
    """A self-contained benchmark task definition."""

    id: str = Field(pattern=SLUG_PATTERN)
    name: str
    version: str = "1"
    description: str
    functional_requirements: list[str] = Field(min_length=1)
    technical_requirements: list[str] = Field(default_factory=list)
    initial_state: InitialState = Field(default_factory=InitialState)
    prompt_template: str = Field(
        description="string.Template text. Available: $task_name, $description, "
        "$functional_requirements, $technical_requirements, $language, $language_version, "
        "$framework, $framework_version, $workdir."
    )
    supported_stacks: list[str] = Field(min_length=1)
    acceptance_tests: AcceptanceTests
    timeouts: Timeouts = Field(default_factory=Timeouts)
    default_trials: int = Field(default=1, ge=1)
    extension_phases: list[ExtensionPhase] = Field(default_factory=list)

    source_dir: Path | None = Field(
        default=None, exclude=True, description="Set by the loader; not part of the file."
    )

    @model_validator(mode="after")
    def _tests_cover_stacks(self) -> BenchmarkSpec:
        if self.acceptance_tests.command is None:
            missing = [s for s in self.supported_stacks if s not in self.acceptance_tests.per_stack]
            if missing:
                raise ValueError(f"no acceptance test command for stacks: {', '.join(missing)}")
        return self

    def supports(self, stack_id: str) -> bool:
        return stack_id in self.supported_stacks

    def render_prompt(self, variables: dict[str, str]) -> str:
        """Render the prompt. Unknown ``$placeholders`` raise, so typos are caught early."""
        base = {
            "task_name": self.name,
            "description": self.description,
            "functional_requirements": "\n".join(f"- {r}" for r in self.functional_requirements),
            "technical_requirements": "\n".join(f"- {r}" for r in self.technical_requirements),
        }
        return Template(self.prompt_template).substitute({**base, **variables})
