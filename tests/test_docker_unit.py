"""DockerExecutionEnvironment command construction, with the docker CLI faked."""

import subprocess
from pathlib import Path
from typing import Any

import pytest

from ai_benchmark.config import AgentAuthConfig, CredentialFile, MountMode, StackSpec
from ai_benchmark.config.settings import DockerSettings
from ai_benchmark.environment import AgentImageLayer, ImageInfo
from ai_benchmark.environment.docker import (
    DockerExecutionEnvironment,
    _package_install_script,
    agent_layer_dockerfile,
)


class FakeCLI:
    def __init__(self, missing_images: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.missing_images = missing_images

    def run(self, args: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        args = list(args)
        self.calls.append(args)
        code, out = 0, ""
        if args[:2] == ["image", "inspect"]:
            if "--format" in args:
                out = "sha256:deadbeef\n"
            elif self.missing_images:
                code = 1
        for stream in ("stdout", "stderr"):
            if kw.get(stream) is not None:
                kw[stream].write("")
        return subprocess.CompletedProcess(args, code, out, "")


STACK = StackSpec.model_validate(
    {
        "id": "fastapi",
        "language": "python",
        "language_version": "3.12",
        "framework": "fastapi",
        "framework_version": "0.115",
        "image": "python:3.12.7-slim-bookworm",
        "env": {"DATABASE_URL": "postgresql://x"},
        "services": [{"name": "postgres", "image": "postgres:16.4", "healthcheck": ["true"]}],
    }
)
LAYER = AgentImageLayer(
    agent="fake", cli_version="1.0.0", install_commands=["echo install"], requires=["node"]
)


def env(cli: FakeCLI) -> DockerExecutionEnvironment:
    return DockerExecutionEnvironment(
        DockerSettings(agent_uid=1000),
        run_id="r1",
        cli=cli,  # type: ignore[arg-type]
    )


def test_agent_layer_dockerfile() -> None:
    text = agent_layer_dockerfile(
        "base@sha256:1", LAYER, uid=1234, node_version="22.11.0", workdir="/workspace"
    )
    assert text.startswith("FROM base@sha256:1\nUSER root\n")
    assert "git ca-certificates curl xz-utils" in text
    assert "node-v22.11.0-linux-$a.tar.xz" in text
    assert "ENV PATH=$PATH:/opt/agent-node/bin" in text
    assert "RUN echo install" in text
    assert text.rstrip().endswith("USER 1234\nWORKDIR /workspace")


def test_package_install_skips_when_everything_is_present() -> None:
    # 'sh' is always there, so the script must exit before touching a package manager.
    script = _package_install_script(["sh"])
    res = subprocess.run(["sh", "-c", script], env={"PATH": "/usr/bin:/bin"}, capture_output=True)
    assert res.returncode == 0
    assert "test -s /etc/ssl/certs/ca-certificates.crt" in _package_install_script(
        ["ca-certificates"]
    )


def test_images_are_pulled_and_layer_built_once() -> None:
    cli = FakeCLI()
    e = env(cli)
    stack_image = e.prepare_image(STACK)
    assert ["pull", "-q", "python:3.12.7-slim-bookworm"] in cli.calls
    agent = e.prepare_agent_image(stack_image, LAYER)
    assert agent.reference.startswith("ai-benchmark/fastapi-fake:")
    assert any(c[0] == "build" for c in cli.calls)
    cli.missing_images = False
    cli.calls.clear()
    assert e.prepare_agent_image(stack_image, LAYER).reference == agent.reference
    assert not any(c[0] == "build" for c in cli.calls)


def test_create_passes_secrets_by_name_only(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("TOKEN", "super-secret")
    cred = tmp_path / "auth.json"
    cred.write_text("{}")
    auth = AgentAuthConfig(
        env=["TOKEN"],
        files=[
            CredentialFile(source=cred, target="/home/agent/.x/auth.json"),
            CredentialFile(
                source=cred, target="/home/agent/.y/a.json", mode=MountMode.BIND_READ_ONLY
            ),
        ],
    )
    cli = FakeCLI()
    e = env(cli)
    image = ImageInfo(stack_id="fastapi", reference="agent-img")
    handle = e.create(STACK, image, tmp_path, "t1", auth)
    flat = " ".join(" ".join(c) for c in cli.calls)
    assert "super-secret" not in flat
    create = next(c for c in cli.calls if c[0] == "create")
    assert create[create.index("TOKEN") - 1 : create.index("TOKEN") + 1] == ["--env", "TOKEN"]
    assert f"{cred}:/home/agent/.y/a.json:ro" in create
    assert "--network-alias" in create and "app" in create
    assert "/var/run/docker.sock" not in flat
    assert create[create.index("--user") : create.index("--user") + 2] == ["--user", "1000"]
    assert any(c[:2] == ["run", "-d"] and "postgres:16.4" in c for c in cli.calls)

    e.start(handle, 60)
    assert ["cp", str(cred), "ai-benchmark-t1-app:/home/agent/.x/auth.json"] in cli.calls
    assert any(c[:2] == ["exec", "ai-benchmark-t1-postgres"] for c in cli.calls)

    e.destroy(handle)
    e.destroy(handle)  # idempotent
    assert ["network", "rm", "ai-benchmark-t1-net"] in cli.calls


def test_exec_marks_timeout(tmp_path: Path) -> None:
    class TimeoutCLI(FakeCLI):
        def run(self, args: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            if list(args)[0] == "exec" and "sleep" in list(args):
                self.calls.append(list(args))
                return subprocess.CompletedProcess(list(args), 124, "", "")
            return super().run(args, **kw)

    cli = TimeoutCLI()
    e = env(cli)
    handle = e.create(STACK, ImageInfo(stack_id="fastapi", reference="i"), tmp_path, "t2")
    e.start(handle, 60)  # FakeCLI says `command -v timeout` succeeds
    res = e.exec(handle, ["sleep", "100"], timeout_seconds=1, stdout_path=tmp_path / "o.log")
    assert res.timed_out and not res.ok
    assert cli.calls[-1][-6:] == ["timeout", "-k", "10", "1", "sleep", "100"]
    assert "-i" not in cli.calls[-1], "the agent must never get the terminal's stdin"
    assert (tmp_path / "o.log").exists()


@pytest.mark.parametrize("policy", ["never"])
def test_pull_policy_never(policy: str) -> None:
    from ai_benchmark.errors import ExecutionEnvironmentError

    e = DockerExecutionEnvironment(DockerSettings(pull_policy=policy), cli=FakeCLI())  # type: ignore[arg-type]
    with pytest.raises(ExecutionEnvironmentError, match="pull_policy=never"):
        e.prepare_image(STACK)


def test_docker_cli_reads_environment_at_call_time(monkeypatch: Any) -> None:
    from ai_benchmark.environment.docker import DockerCLI

    cli = DockerCLI("docker", host="unix:///tmp/d.sock")
    monkeypatch.setenv("LATE_SECRET", "s3cret")
    env = cli._env()
    assert env["LATE_SECRET"] == "s3cret"
    assert env["DOCKER_HOST"] == "unix:///tmp/d.sock"


def test_docker_cli_never_inherits_stdin(monkeypatch: Any) -> None:
    from ai_benchmark.environment.docker import DockerCLI

    seen: list[dict[str, Any]] = []

    def fake_run(cmd: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
        seen.append(kw)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cli = DockerCLI("docker")
    cli.run(["exec", "c", "codex", "exec"])
    cli.run(["build", "-"], input="FROM x\n")
    assert seen[0]["stdin"] is subprocess.DEVNULL
    assert seen[1]["stdin"] is None and seen[1]["input"] == "FROM x\n"
