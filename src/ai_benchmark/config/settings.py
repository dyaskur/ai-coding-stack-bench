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
    agent_uid: int | None = Field(
        default=None,
        description="UID of the non-root user inside trial containers. None = the host "
        "user's UID (so workspace files stay owned by you), or 1000 when running as root.",
    )
    agent_node_version: str = Field(
        default="22.11.0", description="Pinned Node.js added to agent images that need it."
    )
    docker_binary: str = "docker"


class PlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BENCHMARK_", env_nested_delimiter="__", extra="ignore"
    )

    tasks_dir: Path = Path("benchmarks/tasks")
    stacks_dir: Path = Path("benchmarks/stacks")
    results_dir: Path = Path("results")
    workspaces_dir: Path = Path(".workspaces")
    keep_workspaces: bool = Field(default=False, description="Keep trial workspaces after a run.")
    progress_interval_seconds: float = Field(
        default=30,
        ge=0,
        description="How often a running agent's progress is logged; 0 turns it off.",
    )
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "text"
    docker: DockerSettings = Field(default_factory=DockerSettings)
