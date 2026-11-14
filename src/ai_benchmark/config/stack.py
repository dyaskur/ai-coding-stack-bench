"""Stack specification: one language + framework environment.

Stacks are pure data (``benchmarks/stacks/<id>.yaml``). Adding a stack never
requires changing platform code.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator, model_validator

from ai_benchmark.config._base import SLUG_PATTERN, StrictModel


def _is_pinned(image: str) -> bool:
    if "@sha256:" in image:
        return True
    name, _, tag = image.rpartition(":")
    # ``rpartition`` would split a registry port (host:5000/img) - require the
    # tag to come after the last path segment.
    if not name or "/" in tag:
        return False
    return tag != "latest"


class ServiceSpec(StrictModel):
    """A sidecar service (database, cache, queue) started next to the stack container."""

    name: str = Field(pattern=SLUG_PATTERN)
    image: str
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[int] = Field(default_factory=list)
    healthcheck: list[str] | None = Field(
        default=None, description="Command run inside the service until it exits 0."
    )

    @field_validator("image")
    @classmethod
    def _image_pinned(cls, v: str) -> str:
        if not _is_pinned(v):
            raise ValueError(f"service image '{v}' must be pinned to a version tag or digest")
        return v


class ResourceLimits(StrictModel):
    cpus: float | None = Field(default=None, gt=0)
    memory: str | None = Field(default=None, examples=["4g", "512m"])
    pids: int | None = Field(default=None, gt=0)


class StackSpec(StrictModel):
    """A reproducible language/framework environment."""

    id: str = Field(pattern=SLUG_PATTERN, description="Short id used on the CLI, e.g. 'fastapi'.")
    language: str
    language_version: str
    framework: str
    framework_version: str
    description: str | None = None

    image: str | None = Field(default=None, description="Pinned prebuilt image reference.")
    dockerfile: Path | None = Field(
        default=None, description="Dockerfile to build the image from, relative to the stack file."
    )
    build_context: Path | None = Field(
        default=None,
        description="Build context relative to the stack file (default: the "
        "Dockerfile's directory).",
    )

    workdir: str = "/workspace"
    environment_notes: str = Field(
        default="",
        description="Facts about the runtime the agent needs, such as which database is "
        "reachable where and which env vars hold its settings. Shown in the prompt as "
        "$environment_notes; keep the wording equivalent across stacks.",
    )
    env: dict[str, str] = Field(default_factory=dict)
    setup_commands: list[list[str]] = Field(
        default_factory=list, description="Run inside the container before the agent starts."
    )
    services: list[ServiceSpec] = Field(default_factory=list)
    resources: ResourceLimits = Field(default_factory=ResourceLimits)
    source_dir: Path | None = Field(
        default=None, exclude=True, description="Set by the loader; not part of the file."
    )

    @model_validator(mode="after")
    def _image_source(self) -> StackSpec:
        if self.image is None and self.dockerfile is None:
            raise ValueError("a stack needs either 'image' or 'dockerfile'")
        if self.image is not None and self.dockerfile is None and not _is_pinned(self.image):
            raise ValueError(
                f"image '{self.image}' must be pinned to a version tag or digest (not 'latest')"
            )
        return self

    def resolve(self, path: Path) -> Path:
        """Resolve a path from the stack file against the file's directory."""
        if path.is_absolute() or self.source_dir is None:
            return path
        return self.source_dir / path

    @property
    def label(self) -> str:
        lang = f"{self.language} {self.language_version}"
        return f"{lang} / {self.framework} {self.framework_version}"
