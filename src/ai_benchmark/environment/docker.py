"""Docker-backed execution environment.

Talks to Docker through the ``docker`` CLI (the only host prerequisite besides
Python), so no Docker SDK is needed and every call can be reproduced by hand.

Security model:

* the agent CLI runs inside the stack container, built as a cached
  ``<stack image> + agent layer`` image; there is no separate agent container;
* containers run as a non-root user with resource limits (CPU, memory, PIDs)
  and ``no-new-privileges``;
* each trial gets its own user-defined network shared only with its services
  and the acceptance tester;
* only the trial workspace (and credential files in ``bind_*`` mode) is
  mounted; the Docker socket is never mounted;
* credentials are injected at ``create``/``start`` time only: environment
  variables are passed by name (``-e NAME``) so values never appear in a
  command line, and files are copied in or bind-mounted. Nothing secret is
  baked into an image or written to logs;
* every container, network and image carries ``<label_prefix>.*`` labels so
  a crashed run can be cleaned up with :meth:`cleanup_stale`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import posixpath
import shlex
import subprocess
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

from ai_benchmark.config.auth import AgentAuthConfig, MountMode
from ai_benchmark.config.settings import DockerSettings
from ai_benchmark.config.stack import StackSpec
from ai_benchmark.environment.base import (
    AgentImageLayer,
    CommandResult,
    EnvironmentHandle,
    ExecutionEnvironment,
    ImageInfo,
    Mount,
)
from ai_benchmark.errors import ExecutionEnvironmentError
from ai_benchmark.progress import BuildHeartbeat

log = logging.getLogger(__name__)

APP_ALIAS = "app"
AGENT_HOME = "/home/agent"
AGENT_NODE_DIR = "/opt/agent-node"
BASE_PACKAGES = ("git", "ca-certificates", "curl")
_OUTPUT_LIMIT = 1_000_000  # characters of stdout/stderr returned in CommandResult
_TIMEOUT_GRACE_SECONDS = 30


class DockerCLI:
    """Thin wrapper around the ``docker`` binary. Replaced by a fake in unit tests."""

    def __init__(self, binary: str = "docker", host: str | None = None) -> None:
        self.binary = binary
        self.host = host

    def _env(self) -> dict[str, str]:
        # Read at call time: `--env NAME` forwards secrets from this process's
        # environment, which may change after construction.
        env = dict(os.environ)
        if self.host:
            env["DOCKER_HOST"] = self.host
        return env

    def run(
        self,
        args: Sequence[str],
        *,
        input: str | None = None,
        timeout: float | None = None,
        check: bool = True,
        stdout: IO[str] | None = None,
        stderr: IO[str] | None = None,
        extra_env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [self.binary, *args]
        try:
            proc = subprocess.run(
                cmd,
                input=input,
                # Never inherit the terminal's stdin: a command that reads it would
                # block on the person running the benchmark.
                stdin=subprocess.DEVNULL if input is None else None,
                text=True,
                timeout=timeout,
                env={**self._env(), **(extra_env or {})},
                stdout=stdout if stdout is not None else subprocess.PIPE,
                stderr=stderr if stderr is not None else subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError as exc:
            raise ExecutionEnvironmentError(f"docker CLI not found: {self.binary}") from exc
        if check and proc.returncode != 0:
            err = (proc.stderr or "").strip()[-2000:]
            raise ExecutionEnvironmentError(f"docker {args[0]} failed ({proc.returncode}): {err}")
        return proc


def _short_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()[:16]


def _hash_dir(path: Path) -> str:
    h = hashlib.sha256()
    for file in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(file.relative_to(path)).encode())
        h.update(file.read_bytes())
    return h.hexdigest()


# How to tell that a package is already present, so prebuilt or offline bases skip
# the package manager entirely. Unknown packages fall back to ``command -v <name>``.
_PACKAGE_PROBES = {
    "ca-certificates": "test -s /etc/ssl/certs/ca-certificates.crt",
    "xz-utils": "command -v xz >/dev/null 2>&1",
}


def _package_probe(package: str) -> str:
    return _PACKAGE_PROBES.get(package, f"command -v {package} >/dev/null 2>&1")


def _package_install_script(packages: Sequence[str]) -> str:
    """Install the missing ones of ``packages`` with whatever package manager the base has."""
    probes = " && ".join(_package_probe(p) for p in packages) or "true"
    apt = " ".join(packages)
    apk = " ".join("xz" if p == "xz-utils" else p for p in packages)
    return (
        "set -e; "
        f"if {probes}; then exit 0; fi; "
        "if command -v apt-get >/dev/null 2>&1; then "
        "apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "
        f"--no-install-recommends {apt} >/dev/null && rm -rf /var/lib/apt/lists/*; "
        f"elif command -v apk >/dev/null 2>&1; then apk add --no-cache -q {apk}; "
        f"elif command -v dnf >/dev/null 2>&1; then dnf install -y -q {apt}; "
        "else echo 'no supported package manager' >&2; exit 1; fi"
    )


def _node_install_script(version: str) -> str:
    return (
        "set -e; "
        "if command -v apk >/dev/null 2>&1; then "
        # Official Node builds are glibc-only; Alpine gets its packaged Node.
        f"apk add --no-cache -q nodejs npm && mkdir -p {AGENT_NODE_DIR}/bin && "
        f"ln -sf $(command -v node) {AGENT_NODE_DIR}/bin/node && "
        f"ln -sf $(command -v npm) {AGENT_NODE_DIR}/bin/npm; "
        "else "
        'case "$(uname -m)" in x86_64) a=x64;; aarch64|arm64) a=arm64;; '
        '*) echo "unsupported arch $(uname -m)" >&2; exit 1;; esac; '
        "curl -fsSL --retry 3 --connect-timeout 20 --max-time 600 "
        f"https://nodejs.org/dist/v{version}/node-v{version}-linux-$a.tar.xz "
        f"| tar -xJ -C /opt && mv /opt/node-v{version}-linux-$a {AGENT_NODE_DIR}; fi"
    )


def _user_script(uid: int) -> str:
    return (
        "set -e; "
        f"if ! awk -F: '$3=={uid}{{f=1}} END{{exit !f}}' /etc/passwd; then "
        f"(useradd -m -u {uid} -d {AGENT_HOME} -s /bin/sh agent 2>/dev/null "
        f"|| adduser -D -u {uid} -h {AGENT_HOME} agent); fi; "
        f"mkdir -p {AGENT_HOME} && chown -R {uid} {AGENT_HOME}"
    )


def agent_layer_dockerfile(
    base_ref: str, layer: AgentImageLayer, *, uid: int, node_version: str, workdir: str
) -> str:
    """The Dockerfile that adds ``layer`` on top of a stack image."""
    packages = list(BASE_PACKAGES)
    wants_node = "node" in layer.requires
    if wants_node:
        packages.append("xz-utils")
    packages += [p for p in layer.requires if p != "node" and p not in packages]
    lines = [
        f"FROM {base_ref}",
        "USER root",
        f"RUN {_package_install_script(packages)}",
    ]
    if wants_node:
        lines.append(f"RUN {_node_install_script(node_version)}")
        # Appended, not prepended: the stack's own runtime keeps priority for app commands.
        lines.append(f"ENV PATH=$PATH:{AGENT_NODE_DIR}/bin")
    lines.append(f"RUN {_user_script(uid)}")
    lines += [f"RUN {cmd}" for cmd in layer.install_commands]
    env = {"HOME": AGENT_HOME, **layer.env}
    lines.append("ENV " + " ".join(f"{k}={json.dumps(v)}" for k, v in env.items()))
    lines += [f"USER {uid}", f"WORKDIR {workdir}"]
    return "\n".join(lines) + "\n"


class DockerExecutionEnvironment(ExecutionEnvironment):
    def __init__(
        self, settings: DockerSettings, run_id: str | None = None, cli: DockerCLI | None = None
    ) -> None:
        self.settings = settings
        self.run_id = run_id
        self.cli = cli or DockerCLI(settings.docker_binary, settings.host)
        self.agent_uid = settings.agent_uid if settings.agent_uid is not None else _default_uid()
        self._pending_files: dict[str, list[tuple[Path, str]]] = {}
        self._has_timeout: dict[str, bool] = {}
        self._service_checks: dict[str, dict[str, list[str]]] = {}
        self._jobs = 0

    # -- helpers -----------------------------------------------------------

    def labels_for(self, trial_id: str | None = None) -> dict[str, str]:
        """Labels applied to every container, network and image this environment makes."""
        prefix = self.settings.label_prefix
        labels = {f"{prefix}.managed": "true"}
        if trial_id:
            labels[f"{prefix}.trial_id"] = trial_id
        if self.run_id:
            labels[f"{prefix}.run_id"] = self.run_id
        return labels

    def _label_args(self, trial_id: str | None = None) -> list[str]:
        args: list[str] = []
        for k, v in self.labels_for(trial_id).items():
            args += ["--label", f"{k}={v}"]
        return args

    def _name(self, trial_id: str, role: str) -> str:
        return f"{self.settings.network_name_prefix}-{trial_id}-{role}"

    def _image_exists(self, ref: str) -> bool:
        return self.cli.run(["image", "inspect", ref], check=False).returncode == 0

    def _image_id(self, ref: str) -> str:
        out = self.cli.run(["image", "inspect", "--format", "{{.Id}}", ref]).stdout
        return out.strip()

    def _build(self, tag: str, dockerfile_text: str, context: Path) -> None:
        # Builds can take minutes (toolchains, agent CLIs), so stream the output
        # to a log file, report the current step while it runs, and bound it.
        build_log = _build_log_path(tag)
        build_log.parent.mkdir(parents=True, exist_ok=True)
        limit = self.settings.build_timeout_seconds
        log.info("building image %s (limit %ds), log: %s", tag, limit, build_log)
        args = ["build", "-t", tag, *self._label_args(), "-f", "-", str(context)]
        with build_log.open("w", encoding="utf-8") as out, BuildHeartbeat(tag, build_log):
            try:
                proc = self.cli.run(
                    args,
                    input=dockerfile_text,
                    timeout=limit,
                    check=False,
                    stdout=out,
                    stderr=out,
                    # Plain step-by-step output from BuildKit; the classic builder
                    # ignores it and prints its steps anyway.
                    extra_env={"BUILDKIT_PROGRESS": "plain"},
                )
            except subprocess.TimeoutExpired as exc:
                raise ExecutionEnvironmentError(
                    f"building image {tag} took longer than {limit}s; last output:\n"
                    f"{_tail(build_log)[-2000:]}\nfull log: {build_log}"
                ) from exc
        if proc.returncode != 0:
            raise ExecutionEnvironmentError(
                f"building image {tag} failed ({proc.returncode}); last output:\n"
                f"{_tail(build_log)[-2000:]}\nfull log: {build_log}"
            )

    # -- images ------------------------------------------------------------

    def check(self) -> str:
        return self.check_daemon()

    def check_daemon(self) -> str:
        """Verify the Docker daemon is reachable; return its version."""
        out = self.cli.run(["version", "--format", "{{.Server.Version}}"], check=False)
        if out.returncode != 0:
            raise ExecutionEnvironmentError(
                f"Docker daemon is not reachable: {(out.stderr or '').strip()[-500:]}"
            )
        return out.stdout.strip()

    def prepare_image(self, stack: StackSpec) -> ImageInfo:
        if stack.dockerfile is not None:
            dockerfile = stack.resolve(stack.dockerfile)
            context = (
                stack.resolve(stack.build_context) if stack.build_context else dockerfile.parent
            )
            if not dockerfile.is_file():
                raise ExecutionEnvironmentError(f"stack Dockerfile not found: {dockerfile}")
            text = dockerfile.read_text(encoding="utf-8")
            tag = f"ai-benchmark/stack-{stack.id}:{_short_hash(text, _hash_dir(context))}"
            built = False
            if not self._image_exists(tag):
                self._build(tag, text, context)
                built = True
            return ImageInfo(
                stack_id=stack.id, reference=tag, digest=self._image_id(tag), built_locally=built
            )

        assert stack.image is not None  # guaranteed by StackSpec validation
        ref = stack.image
        policy = self.settings.pull_policy
        if policy == "always" or (policy == "if_not_present" and not self._image_exists(ref)):
            log.info("pulling image %s", ref)
            self.cli.run(["pull", "-q", ref])
        elif policy == "never" and not self._image_exists(ref):
            raise ExecutionEnvironmentError(f"image {ref} is not present and pull_policy=never")
        return ImageInfo(stack_id=stack.id, reference=ref, digest=self._image_id(ref))

    def prepare_agent_image(self, stack_image: ImageInfo, layer: AgentImageLayer) -> ImageInfo:
        # BuildKit cannot FROM a local image ID, so build FROM the tag and key the
        # cache on the ID: a re-pulled or rebuilt stack image gets a new layer.
        text = agent_layer_dockerfile(
            stack_image.reference,
            layer,
            uid=self.agent_uid,
            node_version=self.settings.agent_node_version,
            workdir="/workspace",
        )
        key = _short_hash(text, stack_image.digest or "")
        tag = f"ai-benchmark/{stack_image.stack_id}-{layer.agent}:{key}"
        built = False
        if not self._image_exists(tag):
            with tempfile.TemporaryDirectory(prefix="ai-benchmark-ctx-") as ctx:
                self._build(tag, text, Path(ctx))
            built = True
        return ImageInfo(
            stack_id=stack_image.stack_id,
            reference=tag,
            digest=self._image_id(tag),
            built_locally=built,
        )

    def build_image(self, name: str, context: Path, dockerfile: Path | None = None) -> ImageInfo:
        dockerfile = dockerfile or context / "Dockerfile"
        text = dockerfile.read_text(encoding="utf-8")
        tag = f"ai-benchmark/{name}:{_short_hash(text, _hash_dir(context))}"
        built = False
        if not self._image_exists(tag):
            self._build(tag, text, context)
            built = True
        return ImageInfo(
            stack_id=name, reference=tag, digest=self._image_id(tag), built_locally=built
        )

    # -- lifecycle ---------------------------------------------------------

    def create(
        self,
        stack: StackSpec,
        image: ImageInfo,
        workspace: Path,
        trial_id: str,
        auth: AgentAuthConfig | None = None,
    ) -> EnvironmentHandle:
        workspace = workspace.resolve()
        if os.getuid() == 0 and self.agent_uid != 0:
            # Running as root on the host: hand the workspace to the container user.
            for p in [workspace, *workspace.rglob("*")]:
                os.chown(p, self.agent_uid, self.agent_uid, follow_symlinks=False)

        handle = EnvironmentHandle(
            environment_id=uuid.uuid4().hex[:12],
            trial_id=trial_id,
            stack_id=stack.id,
            image=image,
            workspace=workspace,
        )
        network = self._name(trial_id, "net")
        self.cli.run(["network", "create", *self._label_args(trial_id), network])
        handle.network_id = network
        try:
            for svc in stack.services:
                name = self._name(trial_id, svc.name)
                args = ["run", "-d", "--name", name, "--network", network]
                args += ["--network-alias", svc.name, *self._label_args(trial_id)]
                for k, v in svc.env.items():
                    args += ["-e", f"{k}={v}"]
                self.cli.run([*args, svc.image])
                handle.service_container_ids[svc.name] = name

            res = stack.resources
            name = self._name(trial_id, "app")
            args = [
                "create", "--name", name, "--network", network, "--network-alias", APP_ALIAS,
                *self._label_args(trial_id),
                "--user", str(self.agent_uid),
                "--workdir", stack.workdir,
                "--volume", f"{workspace}:{stack.workdir}",
                "--memory", res.memory or self.settings.default_memory_limit,
                "--cpus", str(res.cpus or self.settings.default_cpus),
                "--pids-limit", str(res.pids or self.settings.default_pids_limit),
                "--security-opt", "no-new-privileges",
                "--env", f"HOME={AGENT_HOME}",
            ]  # fmt: skip
            for k, v in stack.env.items():
                args += ["--env", f"{k}={v}"]
            pending: list[tuple[Path, str]] = []
            if auth is not None:
                for var in auth.env:
                    args += ["--env", var]  # value comes from this process's env, not argv
                for f in auth.files:
                    if f.mode is MountMode.COPY:
                        pending.append((f.host_path, f.target))
                    else:
                        ro = ":ro" if f.mode is MountMode.BIND_READ_ONLY else ""
                        args += ["--volume", f"{f.host_path}:{f.target}{ro}"]
            args += ["--entrypoint", "sleep", image.reference, "infinity"]
            self.cli.run(args)
            handle.container_id = name
            self._pending_files[handle.environment_id] = pending
            self._service_checks[handle.environment_id] = {
                s.name: s.healthcheck for s in stack.services if s.healthcheck
            }
        except BaseException:
            self.destroy(handle)
            raise
        return handle

    def start(self, handle: EnvironmentHandle, timeout_seconds: int) -> None:
        assert handle.container_id
        deadline = time.monotonic() + timeout_seconds
        self.cli.run(["start", handle.container_id])
        for source, target in self._pending_files.pop(handle.environment_id, []):
            parent = posixpath.dirname(target)
            self._root_exec(handle, f"mkdir -p {shlex.quote(parent)}")
            self.cli.run(["cp", str(source), f"{handle.container_id}:{target}"])
            # Every directory created on the way must belong to the agent, or a CLI
            # can't add siblings later (e.g. ~/.local/state next to ~/.local/share).
            owned = AGENT_HOME if target.startswith(AGENT_HOME + "/") else parent
            self._root_exec(
                handle,
                f"chown -R {self.agent_uid} {shlex.quote(owned)} "
                f"&& chmod 600 {shlex.quote(target)}",
            )
        self._root_exec(handle, f"mkdir -p {AGENT_HOME} && chown {self.agent_uid} {AGENT_HOME}")
        self._has_timeout[handle.environment_id] = (
            self.cli.run(
                ["exec", handle.container_id, "sh", "-c", "command -v timeout"], check=False
            ).returncode
            == 0
        )
        self._wait_for_services(handle, deadline)

    def _root_exec(self, handle: EnvironmentHandle, script: str) -> None:
        assert handle.container_id
        self.cli.run(["exec", "-u", "0", handle.container_id, "sh", "-c", script])

    def _wait_for_services(self, handle: EnvironmentHandle, deadline: float) -> None:
        # Services without a health check are assumed ready.
        stack_checks = self._service_checks.get(handle.environment_id, {})
        for svc, check in stack_checks.items():
            container = handle.service_container_ids[svc]
            while True:
                ok = self.cli.run(["exec", container, *check], check=False).returncode == 0
                if ok:
                    break
                if time.monotonic() > deadline:
                    raise ExecutionEnvironmentError(f"service '{svc}' did not become healthy")
                time.sleep(2)

    def exec(
        self,
        handle: EnvironmentHandle,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> CommandResult:
        assert handle.container_id
        # No -i: nothing in a trial reads stdin, and an attached stdin would hand
        # the agent the terminal running the benchmark (codex exec, for one,
        # reads stdin to EOF before it starts and would wait forever).
        args = ["exec"]
        if workdir:
            args += ["-w", workdir]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        args.append(handle.container_id)
        inner = list(command)
        if self._has_timeout.get(handle.environment_id):
            inner = ["timeout", "-k", "10", str(timeout_seconds), *inner]
        return self._run_streaming(
            [*args, *inner],
            timeout_seconds=timeout_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            in_container_timeout=self._has_timeout.get(handle.environment_id, False),
        )

    def _run_streaming(
        self,
        args: list[str],
        *,
        timeout_seconds: int,
        stdout_path: Path | None,
        stderr_path: Path | None,
        in_container_timeout: bool = False,
        on_timeout: Sequence[str] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ai-benchmark-out-") as tmp:
            out_path = stdout_path or Path(tmp) / "stdout"
            err_path = stderr_path or Path(tmp) / "stderr"
            out_path.parent.mkdir(parents=True, exist_ok=True)
            err_path.parent.mkdir(parents=True, exist_ok=True)
            timed_out = False
            with (
                out_path.open("w", encoding="utf-8") as out,
                err_path.open("w", encoding="utf-8") as err,
            ):
                try:
                    proc = self.cli.run(
                        args,
                        timeout=timeout_seconds + _TIMEOUT_GRACE_SECONDS,
                        check=False,
                        stdout=out,
                        stderr=err,
                    )
                    exit_code: int | None = proc.returncode
                except subprocess.TimeoutExpired:
                    timed_out, exit_code = True, None
                    if on_timeout:
                        self.cli.run(list(on_timeout), check=False)
            if in_container_timeout and exit_code in (124, 137):
                timed_out = True
            stdout = _tail(out_path)
            stderr = _tail(err_path)
        return CommandResult(
            command=args,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            timed_out=timed_out,
        )

    def exec_detached(
        self,
        handle: EnvironmentHandle,
        command: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        workdir: str | None = None,
    ) -> None:
        assert handle.container_id
        args = ["exec", "-d"]
        if workdir:
            args += ["-w", workdir]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        self.cli.run([*args, handle.container_id, *command])

    def restart(self, handle: EnvironmentHandle, timeout_seconds: int) -> None:
        assert handle.container_id
        self.cli.run(["restart", "-t", "5", handle.container_id], timeout=timeout_seconds)

    def run_job(
        self,
        handle: EnvironmentHandle,
        image: ImageInfo,
        command: Sequence[str],
        *,
        timeout_seconds: int,
        mounts: Sequence[Mount] = (),
        env: Mapping[str, str] | None = None,
        stdout_path: Path | None = None,
        stderr_path: Path | None = None,
    ) -> CommandResult:
        assert handle.network_id
        self._jobs += 1
        name = self._name(handle.trial_id, f"job{self._jobs}")
        args = ["run", "--rm", "--name", name, "--network", handle.network_id]
        args += [*self._label_args(handle.trial_id), "--user", str(self.agent_uid)]
        args += ["--security-opt", "no-new-privileges"]
        for m in mounts:
            args += ["--volume", f"{m.source.resolve()}:{m.target}{':ro' if m.read_only else ''}"]
        for k, v in (env or {}).items():
            args += ["--env", f"{k}={v}"]
        return self._run_streaming(
            [*args, image.reference, *command],
            timeout_seconds=timeout_seconds,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            on_timeout=["rm", "-f", name],
        )

    def copy_into(self, handle: EnvironmentHandle, source: Path, destination: str) -> None:
        assert handle.container_id
        self._root_exec(handle, f"mkdir -p {shlex.quote(destination)}")
        self.cli.run(["cp", f"{source}/.", f"{handle.container_id}:{destination}"])
        self._root_exec(
            handle,
            f"chown -R 0:0 {shlex.quote(destination)} && chmod -R a+rX,go-w "
            f"{shlex.quote(destination)}",
        )

    def collect_logs(self, handle: EnvironmentHandle, destination: Path) -> dict[str, Path]:
        destination.mkdir(parents=True, exist_ok=True)
        files: dict[str, Path] = {}
        containers = dict(handle.service_container_ids)
        if handle.container_id:
            containers[APP_ALIAS] = handle.container_id
        for role, container in containers.items():
            path = destination / f"container-{role}.log"
            with path.open("w", encoding="utf-8") as fh:
                self.cli.run(["logs", container], check=False, stdout=fh, stderr=fh)
            files[f"container-{role}"] = path
        return files

    def destroy(self, handle: EnvironmentHandle) -> None:
        self._pending_files.pop(handle.environment_id, None)
        self._has_timeout.pop(handle.environment_id, None)
        self._service_checks.pop(handle.environment_id, None)
        containers = list(handle.service_container_ids.values())
        if handle.container_id:
            containers.append(handle.container_id)
        if not self.settings.cleanup:
            if containers:
                self.cli.run(["stop", "-t", "2", *containers], check=False)
            return
        label = f"{self.settings.label_prefix}.trial_id={handle.trial_id}"
        jobs = self.cli.run(["ps", "-aq", "--filter", f"label={label}"], check=False).stdout
        containers += [c for c in (jobs or "").split() if c]
        if containers:
            self.cli.run(["rm", "-f", "-v", *dict.fromkeys(containers)], check=False)
        if handle.network_id:
            self.cli.run(["network", "rm", handle.network_id], check=False)

    def cleanup_stale(self) -> int:
        """Remove containers and networks left behind by crashed runs. Returns the count."""
        label = f"label={self.settings.label_prefix}.managed=true"
        ids = self.cli.run(["ps", "-aq", "--filter", label], check=False).stdout.split()
        if ids:
            self.cli.run(["rm", "-f", "-v", *ids], check=False)
        nets = self.cli.run(["network", "ls", "-q", "--filter", label], check=False).stdout.split()
        if nets:
            self.cli.run(["network", "rm", *nets], check=False)
        return len(ids) + len(nets)


def _default_uid() -> int:
    uid = os.getuid() if hasattr(os, "getuid") else 1000
    return uid if uid != 0 else 1000


def _tail(path: Path) -> str:
    size = path.stat().st_size
    with path.open("rb") as fh:
        if size > _OUTPUT_LIMIT:
            fh.seek(size - _OUTPUT_LIMIT)
        return fh.read().decode("utf-8", errors="replace")


def _build_log_path(tag: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in tag)
    return Path(tempfile.gettempdir()) / "ai-benchmark-builds" / f"{safe}.log"
