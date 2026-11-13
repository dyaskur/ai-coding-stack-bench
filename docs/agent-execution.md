# Agent execution and authentication

Decided before Phase 2. The Docker side is implemented (see
[trial-execution.md](trial-execution.md)); concrete agent adapters come next.

## Where the agent runs: inside the stack container

Each trial runs in one container built from:

```
<stack image, e.g. python:3.12.7-slim>   pinned language runtime + framework deps
  + agent layer (AgentImageLayer)         pinned agent CLI version, non-root user "agent"
  = ai-benchmark/<stack>-<agent>:<hash>   built once, cached between trials
```

- The agent CLI, the code it writes, and every build/test it runs use the
  stack's pinned runtimes. Host runtimes are never involved.
- There is no Docker socket in the container and the agent never launches
  containers. Adapters run commands only through the `CommandRunner` they are
  given (`ExecutionEnvironment.exec` under the hood).
- Services (Postgres, MySQL, ...) run as sidecars on a per-trial network.
- The container needs outbound HTTPS so the agent can reach its provider.
- After the agent finishes, the app container is restarted, the app is started
  with the task's start command, and the acceptance suite runs from a separate
  tester container over HTTP. The agent never sees the suite. Then everything
  is destroyed. Details in [trial-execution.md](trial-execution.md).

Interfaces: `AgentAdapter.image_layer()`, `ExecutionEnvironment.prepare_agent_image()`,
`ExecutionEnvironment.create(..., auth=...)`, `CommandRunner`, `ContainerCommandRunner`.

**Trade-off to be aware of:** the agent's credential is inside the same
container as the code it generates, so generated code could read it. This is
inherent to running the agent next to the code. Mitigations: credentials are
injected per trial and never baked into images or logs, the container is
destroyed after every trial, it runs as a non-root user with resource limits,
and you should use a dedicated account/token for benchmarking that you can
revoke.

## Auth modes

Set once per experiment with `auth_mode` (or `benchmark run --auth`). Every
agent in a run uses the same mode, so results stay comparable; run the other
mode as a separate experiment.

| | `subscription` (default) | `api_key` |
| --- | --- | --- |
| Credential | OAuth token or login file from a subscription | Provider API key |
| Cost metric | Always **unavailable** ("subscription auth: no per-run cost is billed"). A cost figure the CLI prints anyway is kept as `provider_specific.agent_reported_cost_usd`. | Recorded when the agent reports it, else unavailable |
| Parallelism | Sequential (usage limits are shared across runs) | Sequential for now |
| Limit hits | Recorded as `rate_limit_events` on the trial; a trial the limit stopped gets status `rate_limited`. Filter with `benchmark results --rate-limited / --not-rate-limited`. | Same |

Config files only ever contain **names** of environment variables and
**paths** of credential files, never secret values (`AgentAuthConfig` rejects
anything that doesn't look like a variable name). Values are read from the
host when the trial container is created.

### Expected defaults per agent

Each adapter supplies these through `default_auth(mode)`. They will be checked
against each CLI's current official documentation when the adapters are built
in Phase 2; you can override any of them per agent in the experiment file.

| Agent | Subscription | API key |
| --- | --- | --- |
| Claude Code | env `CLAUDE_CODE_OAUTH_TOKEN` (create with `claude setup-token`) | env `ANTHROPIC_API_KEY` |
| Codex CLI | file `~/.codex/auth.json` (created by `codex login`) | env `OPENAI_API_KEY` (exact variable to confirm) |
| OpenCode | file `~/.local/share/opencode/auth.json` (created by `opencode auth login`) | provider key env var, depending on the configured provider |

Credential files default to `mode: copy` (a fresh copy per trial). If a CLI
must write refreshed tokens back, use `bind_rw` for that file.

`benchmark agents --check --auth subscription` reports which credentials are
present on this host, and `benchmark run` pre-flight refuses to start when one
is missing.
