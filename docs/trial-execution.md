# Trial execution

What `benchmark run` does for each trial, in order. The code lives in
`orchestrator/orchestrator.py` (`run_trial`) and `environment/docker.py`.

## 1. Images

- **Stack image.** Pulled from the pinned `image`, or built from the stack's
  `dockerfile` and tagged `ai-benchmark/stack-<id>:<hash>`.
- **Agent image.** The agent's `AgentImageLayer` is added on top of the stack
  image and tagged `ai-benchmark/<stack>-<agent>:<hash>`. The hash covers the
  generated Dockerfile and the stack image ID, so a changed stack or agent
  version rebuilds and everything else is cached. The layer installs git,
  curl and CA certificates when they are missing, a pinned Node.js under
  `/opt/agent-node` when the agent needs it (`docker.agent_node_version`),
  and a non-root `agent` user (`docker.agent_uid`, default: your uid, or
  1000 when running as root).

## 2. Containers

- One network per trial, `ai-benchmark-<trial>-net`. The app container is
  reachable on it as `app`, each service by its `name`.
- Services start first and must pass their `healthcheck` before the trial
  continues.
- The app container runs `sleep infinity` as the non-root user with
  `no-new-privileges`, the stack's resource limits, and the fresh workspace
  bind-mounted at the stack's `workdir`.
- Secrets are passed as `--env NAME`, so values never appear in a command
  line or a log. Credential files are copied in with mode 600.
- Every container and network carries `io.ai-benchmark.*` labels, and is
  removed after the trial even when it fails.

## 3. Agent run

1. The stack's `setup` commands run.
2. The workspace is committed and tagged `ai-benchmark-baseline` (git runs
   inside the container; dependency and cache folders are excluded).
3. The agent's version is captured, then the agent runs under the task's
   `agent_seconds` timeout.
4. Whatever the agent left is committed. Lines added, lines deleted and files
   changed are measured against the baseline.

## 4. Acceptance tests

The agent never sees the suite. After the agent finishes:

1. The app container is restarted, so only what the agent wrote to disk counts.
2. The task's `app_start_command` (default `sh start.sh`) is started in the
   background. Its output goes to `/tmp/app.log` and is saved with the logs.
3. A tester container joins the trial network with `BASE_URL=http://app:<app_port>`,
   the suite mounted read-only at `/acceptance` and a results folder at
   `/results`. It runs the suite's `command` (default: pytest writing
   `/results/junit.xml`).
4. The JUnit XML is parsed per test case. A missing or empty report means
   zero passed tests, never a pass.

The tester image is the task's `acceptance_tests.image` if set, otherwise it
is built from the `Dockerfile` in the suite folder.

## 5. Status

Checked in this order: `infra_error` (anything the platform itself failed
at), `timed_out`, `rate_limited`, `agent_error`, `passed` (at least one test
and all of them pass), otherwise `failed`. With `stop_on_infra_error`, the
remaining trials are recorded as `cancelled`. Workspaces of `infra_error`
trials are kept for debugging.

## Watching a run

`benchmark run` logs each phase of a trial (images, containers, setup, agent,
acceptance tests, final status with its duration). While the agent works, it
also logs a heartbeat every 30 seconds with the elapsed time against the
limit, the number of output lines and a summary of the agent's latest event.
Set `BENCHMARK_PROGRESS_INTERVAL_SECONDS` to change the interval, or `0` to
turn it off. The agent's full output is streamed to
`.workspaces/<run>/<trial>/logs/agent.stdout.log`, so `tail -f` on it shows
every event.

## Running the Docker integration test

`tests/integration` runs two real trials with a scripted stand-in agent: a
tiny app with a Redis service, and a reference FastAPI solution of
`task-management-api` on the shipped `fastapi` stack and Postgres, checked by
the task's real acceptance suite. The second one guards the suite itself: a
correct solution must pass every test.

```bash
BENCHMARK_DOCKER_TESTS=1 pytest tests/integration
```

On a host whose containers can't reach package mirrors, point it at prebuilt
images: `BENCHMARK_IT_STACK_IMAGE` (with git, curl and CA certificates, for
example `python:3.12.7-bookworm`), `BENCHMARK_IT_FASTAPI_IMAGE` (an image
equivalent to `benchmarks/stacks/fastapi/Dockerfile`) and
`BENCHMARK_IT_TESTER_IMAGE` (with pytest and httpx).

## Writing an acceptance suite

- Test only the HTTP contract written in the task's requirements. The suite
  runs from its own container and must not depend on the stack.
- The database survives restarts and may hold the agent's own test data, so
  create what each test needs and never assume an empty table.
- Change the suite and the task's requirements together and bump the task's
  `version`, so results from different suites are never compared.
