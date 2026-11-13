# Phase 1: foundation

## Implemented

| Area | Where | Notes |
| --- | --- | --- |
| Project structure and packaging | `pyproject.toml`, `src/ai_benchmark/` | `benchmark` console script; Python 3.10+ |
| Configuration schema | `config/` | Task (`BenchmarkSpec`), stack (`StackSpec`), experiment (`ExperimentConfig`), platform settings (`PlatformSettings`). Unknown keys are rejected; images must be pinned; MCP servers are only allowed in `best_ecosystem` mode. |
| Metric schema | `models/metrics.py` | Every metric is a `Metric[T]` with `available` / `unavailable` / `not_applicable` and a source (`agent_reported`, `measured`, `derived`). Defaults are *unavailable*, never zero. Token usage carries an `accounting_scheme` so counts are not compared across providers. |
| Result records | `models/results.py` | `TrialResult` and `RunManifest` capture agent/model/version, stack versions, image digest, tooling mode, enabled tools/MCP servers, seed, logs and raw provider data. |
| Agent adapter interface | `agents/base.py`, `agents/registry.py` | `AgentAdapter` ABC (capabilities, `image_layer`, `default_auth`, version/availability checks, `build_command`, `run`, `parse_usage`, `detect_rate_limits`). Adapters execute only through a `CommandRunner` bound to the trial container. Registry with entry-point discovery. |
| Auth | `config/auth.py`, `orchestrator/policy.py` | `AuthMode` (`subscription`/`api_key`), `AgentAuthConfig` (env var names and credential file paths, never values), credential pre-flight, and the policy that makes subscription cost unavailable. |
| Execution environment | `environment/` | `ExecutionEnvironment` ABC with lifecycle and a `session()` context manager that always cleans up; `DockerExecutionEnvironment` skeleton (methods raise `NotImplementedError`); `WorkspaceManager` creates a fresh workspace per trial. |
| Orchestrator | `orchestrator/` | Planning is implemented: matrix expansion, seeded randomized ordering, stable trial ids, validation against the task's supported stacks. Pre-flight checks agent registration. Execution methods are signatures only. |
| Basic result storage | `storage/` | `ResultStore` ABC, filters (agent, model, stack, language, framework, task, mode, status) and an atomic JSON file store. |
| CLI | `cli/app.py` | `tasks`, `stacks`, `agents`, `validate`, `run` (plan + `--dry-run`), `results`, `report` (stub), `version`. |

## Deliberately not implemented

- Concrete agent adapters (Codex, Claude Code, OpenCode): Phase 2.
- Docker API calls, image builds, service start-up: Phase 2.
- Trial execution and acceptance test running/parsing: Phase 2.
- Aggregation, reports and charts: Phase 3.

## Decisions for Phase 2 (interfaces already updated)

1. **The agent CLI runs inside the stack container**, as a cached per-agent
   layer on top of each stack image, with no Docker socket access. See
   [agent-execution.md](agent-execution.md).
2. **Two auth modes, `subscription` (default) and `api_key`.** Subscription
   runs record cost as unavailable, run sequentially, and flag trials that hit
   usage limits. See [agent-execution.md](agent-execution.md#auth-modes).
3. **Target host is a Linux VPS.** See [vps-setup.md](vps-setup.md).
4. **Two comparison views over one pivot:** stack comparison (agent + model
   fixed) and model comparison (stack fixed), defined in ARCHITECTURE.md
   under "Comparison Views". Trial results already carry `resolved_model`,
   `ComparisonDimension` and `TrialResult.dimension_value()` /
   `comparability_key()`; the aggregation query and `benchmark compare`
   come in Phase 3.

## Open question

- **Acceptance result format.** `AcceptanceTests.result_format` defaults to
  JUnit XML; confirm all stacks' test runners can emit it.
