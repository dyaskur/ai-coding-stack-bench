"""Benchmark task specification (``benchmarks/tasks/<id>/task.yaml``).

Tasks are independent of agents and stacks. Adding a task never requires
changing the orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Literal

from pydantic import Field, field_validator, model_validator

from ai_benchmark.config._base import SLUG_PATTERN, StrictModel
from ai_benchmark.config.stack import _is_pinned
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
    """How the independent, black-box acceptance suite is run.

    After the agent finishes, the stack container is restarted (killing
    anything the agent left running), the app is started with
    ``app_start_command``, and the suite runs in a separate tester container
    on the trial network against ``http://app:<app_port>``. The agent never
    sees the suite.
    """

    suite: Path | None = Field(
        default=None,
        description="Suite directory relative to the task file, mounted read-only at "
        "/acceptance in the tester container. Unless 'image' is set, the tester image "
        "is built from the suite's Dockerfile. None = no acceptance tests "
        "(quality is unavailable).",
    )
    image: str | None = Field(
        default=None,
        description="Pinned, prebuilt tester image. Takes precedence over the suite's Dockerfile.",
    )
    command: list[str] = Field(
        default_factory=lambda: ["pytest", "-q", "/acceptance", "--junitxml=/results/junit.xml"],
        description="Run in the tester container. Must write JUnit XML into /results.",
    )
    results_file: str = Field(default="junit.xml", description="File name inside /results.")
    app_start_command: list[str] = Field(
        default_factory=lambda: ["sh", "start.sh"],
        description="Starts the app in the stack container, from the workspace root.",
    )
    app_port: int = Field(default=8000, gt=0, lt=65536)
    result_format: Literal["junit"] = "junit"

    @field_validator("image")
    @classmethod
    def _image_pinned(cls, v: str | None) -> str | None:
        if v is not None and not _is_pinned(v):
            raise ValueError(f"tester image '{v}' must be pinned to a version tag or digest")
        return v


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
