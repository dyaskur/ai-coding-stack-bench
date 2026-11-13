"""File-based result store: one directory per run, one JSON file per trial.

Layout::

    <root>/<run_id>/manifest.json
    <root>/<run_id>/trials/<trial_id>.json

Writes are atomic (write to a temp file, then rename) so a crash never leaves a
half-written result.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ai_benchmark.errors import ResultStoreError
from ai_benchmark.models.results import RunManifest, TrialResult
from ai_benchmark.storage.base import ResultFilter, ResultStore

MANIFEST = "manifest.json"


def _atomic_write(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(model.model_dump_json(indent=2))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class JsonResultStore(ResultStore):
    def __init__(self, root: Path) -> None:
        self.root = root

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or "/" in run_id or run_id.startswith("."):
            raise ResultStoreError(f"invalid run id: {run_id!r}")
        return self.root / run_id

    def save_manifest(self, manifest: RunManifest) -> None:
        _atomic_write(self._run_dir(manifest.run_id) / MANIFEST, manifest)

    def load_manifest(self, run_id: str) -> RunManifest:
        path = self._run_dir(run_id) / MANIFEST
        if not path.exists():
            raise ResultStoreError(f"run '{run_id}' not found in {self.root}")
        try:
            return RunManifest.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError as exc:
            raise ResultStoreError(f"corrupt manifest {path}: {exc}") from exc

    def list_runs(self) -> list[RunManifest]:
        if not self.root.is_dir():
            return []
        runs = [self.load_manifest(p.name) for p in self.root.iterdir() if (p / MANIFEST).exists()]
        return sorted(runs, key=lambda m: m.created_at, reverse=True)

    def save_trial(self, result: TrialResult) -> None:
        run_dir = self._run_dir(result.run_id)
        if not (run_dir / MANIFEST).exists():
            raise ResultStoreError(f"save the manifest for run '{result.run_id}' first")
        _atomic_write(run_dir / "trials" / f"{result.trial_id}.json", result)

    def iter_trials(self, filters: ResultFilter | None = None) -> Iterator[TrialResult]:
        filters = filters or ResultFilter()
        if filters.run_id:
            run_dirs = [self._run_dir(filters.run_id)]
        elif self.root.is_dir():
            run_dirs = sorted(p for p in self.root.iterdir() if p.is_dir())
        else:
            run_dirs = []
        for run_dir in run_dirs:
            for path in sorted((run_dir / "trials").glob("*.json")):
                try:
                    result = TrialResult.model_validate_json(path.read_text(encoding="utf-8"))
                except ValidationError as exc:
                    raise ResultStoreError(f"corrupt trial result {path}: {exc}") from exc
                if filters.matches(result):
                    yield result
