"""Loading and validating YAML/JSON specification files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from ai_benchmark.config.experiment import ExperimentConfig
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.config.stack import StackSpec
from ai_benchmark.config.task import BenchmarkSpec
from ai_benchmark.errors import ConfigurationError

M = TypeVar("M", bound=BaseModel)

TASK_FILENAMES = ("task.yaml", "task.yml", "task.json")
_SPEC_SUFFIXES = (".yaml", ".yml", ".json")


def read_document(path: Path) -> dict[str, Any]:
    """Read a YAML or JSON file into a mapping."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{path} is not valid {path.suffix.lstrip('.')}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path} must contain a mapping at the top level")
    return data


def format_validation_error(exc: ValidationError) -> str:
    lines = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def load_model(path: Path, model: type[M]) -> M:
    data = read_document(path)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(
            f"invalid {model.__name__} in {path}:\n{format_validation_error(exc)}"
        ) from exc


def load_task(path: Path) -> BenchmarkSpec:
    spec = load_model(path, BenchmarkSpec)
    return spec.model_copy(update={"source_dir": path.parent.resolve()})


def load_stack(path: Path) -> StackSpec:
    spec = load_model(path, StackSpec)
    return spec.model_copy(update={"source_dir": path.parent.resolve()})


def load_experiment(path: Path) -> ExperimentConfig:
    return load_model(path, ExperimentConfig)


class SpecCatalog:
    """Discovers tasks and stacks from the configured directories."""

    def __init__(self, settings: PlatformSettings) -> None:
        self._settings = settings
        self._tasks: dict[str, BenchmarkSpec] | None = None
        self._stacks: dict[str, StackSpec] | None = None

    def tasks(self) -> dict[str, BenchmarkSpec]:
        if self._tasks is None:
            self._tasks = {}
            root = self._settings.tasks_dir
            if root.is_dir():
                for task_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                    candidates = (task_dir / n for n in TASK_FILENAMES)
                    file = next((c for c in candidates if c.exists()), None)
                    if file is None:
                        continue
                    spec = load_task(file)
                    if spec.id in self._tasks:
                        raise ConfigurationError(f"duplicate task id '{spec.id}' in {file}")
                    self._tasks[spec.id] = spec
        return self._tasks

    def stacks(self) -> dict[str, StackSpec]:
        if self._stacks is None:
            self._stacks = {}
            root = self._settings.stacks_dir
            if root.is_dir():
                for file in sorted(root.iterdir()):
                    if file.suffix not in _SPEC_SUFFIXES:
                        continue
                    spec = load_stack(file)
                    if spec.id in self._stacks:
                        raise ConfigurationError(f"duplicate stack id '{spec.id}' in {file}")
                    self._stacks[spec.id] = spec
        return self._stacks

    def get_task(self, task_id: str) -> BenchmarkSpec:
        try:
            return self.tasks()[task_id]
        except KeyError:
            known = ", ".join(self.tasks()) or "none"
            raise ConfigurationError(f"unknown task '{task_id}' (known: {known})") from None

    def get_stack(self, stack_id: str) -> StackSpec:
        try:
            return self.stacks()[stack_id]
        except KeyError:
            known = ", ".join(self.stacks()) or "none"
            raise ConfigurationError(f"unknown stack '{stack_id}' (known: {known})") from None
