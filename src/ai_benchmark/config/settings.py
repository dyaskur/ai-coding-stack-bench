"""Platform-wide settings, loaded from ``BENCHMARK_*`` environment variables.

Example: ``BENCHMARK_RESULTS_DIR=out BENCHMARK_DOCKER__MEMORY_LIMIT=8g``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DockerSettings(BaseModel):
    """Defaults for the Docker execution environment.

    The agent and its generated code must never get access to the Docker socket
    or unrestricted host paths; only the trial workspace is mounted.
    """

    host: str | None = Field(default=None, description="DOCKER_HOST override.")
    pull_policy: Literal["if_not_present", "always", "never"] = "if_not_present"
    default_memory_limit: str = "4g"
    default_cpus: float = 2.0
    default_pids_limit: int = 512
    network_name_prefix: str = "ai-benchmark"
    label_prefix: str = "io.ai-benchmark"
    cleanup: bool = Field(default=True, description="Remove containers/networks after each trial.")
    run_as_user: str | None = Field(default="1000:1000", description="Non-root user in containers.")


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BENCHMARK_", env_nested_delimiter="__", extra="ignore"
    )

    tasks_dir: Path = Path("benchmarks/tasks")
    stacks_dir: Path = Path("benchmarks/stacks")
    results_dir: Path = Path("results")
    workspaces_dir: Path = Path(".workspaces")
    keep_workspaces: bool = Field(default=False, description="Keep trial workspaces after a run.")
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "text"
    docker: DockerSettings = Field(default_factory=DockerSettings)
