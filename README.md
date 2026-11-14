# AI Coding Benchmark Platform

An open-source platform that measures and compares how productive AI coding
agents (Codex CLI, Claude Code, OpenCode, and more) are across programming
languages and frameworks.

> **Status: Phase 2 (execution).** Trials run in Docker end to end with the
> built-in Claude Code, Codex CLI and OpenCode adapters
> ([docs/agents.md](docs/agents.md)). Reports and the dashboard come in later
> phases. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full vision.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
benchmark --help
```

Requires Python 3.10+ and Docker (the `docker` CLI talking to a local daemon) to run trials.
To run on a server, follow [docs/vps-setup.md](docs/vps-setup.md).

Agents run inside each stack's container and authenticate with either a
subscription (default) or an API key. See
[docs/agent-execution.md](docs/agent-execution.md).

## CLI

```bash
benchmark tasks                      # list benchmark tasks
benchmark stacks                     # list language/framework stacks
benchmark agents [--check --auth subscription]  # list adapters, check credentials
benchmark validate [FILES...]        # validate task/stack/experiment files
benchmark run --agents codex,claude:<model> --task task-management-api \
              --stacks fastapi,laravel --trials 5 --auth subscription --dry-run
benchmark run --config examples/experiment.yaml --dry-run
benchmark results [--agent X --stack Y --mode standard --not-rate-limited ...]
benchmark report                     # Phase 3
```

The `fastapi-latest`, `express-latest`, and `laravel13-boost` stacks pin the
newer runtime and framework versions used for the updated benchmark. The
`laravel13-boost` stack provides a Laravel 13 starter project and installs
Laravel Boost before the agent starts. Boost guidelines, skills, and MCP tools
are available to supported agents. Setup time is outside the agent timer, and
this stack starts from a scaffold; compare it separately from the empty-folder
`laravel` (Laravel 11) baseline. For example:

```bash
benchmark run --agents codex:gpt-6-luna --task task-management-api \
              --stacks fastapi-latest,laravel13-boost,express-latest \
              --trials 20 --auth subscription
```

The [20-trial Luna results](docs/luna-latest-20-trials.md) record the run IDs,
test outcomes, timing, token use, cost estimate, and differences in each
stack's starting state. Laravel setup retries transient Composer failures
before the agent starts.

Platform settings come from `BENCHMARK_*` environment variables (nested with
`__`, e.g. `BENCHMARK_DOCKER__DEFAULT_MEMORY_LIMIT=8g`) or global CLI options
such as `--results-dir`.

## Layout

```
src/ai_benchmark/
  cli/            Typer entry point (`benchmark`)
  config/         Pydantic schemas: tasks, stacks, experiments, platform settings; YAML/JSON loader
  models/         Enums, metric schemas (with explicit availability), result records
  agents/         AgentAdapter interface, registry, built-in claude/codex/opencode adapters
  environment/    ExecutionEnvironment interface, Docker implementation, per-trial workspaces
  orchestrator/   Planning (randomized, seeded), trial execution, git metrics, JUnit parsing
  storage/        ResultStore interface + atomic JSON file store
benchmarks/
  tasks/<id>/task.yaml   Benchmark task definitions
  stacks/<id>.yaml       Stack (language + framework + services) definitions
examples/experiment.yaml Example experiment matrix
tests/                   Unit tests (pytest); tests/integration needs Docker
```

## Extending

- **New agent:** subclass `ai_benchmark.agents.AgentAdapter` and register it with
  `@register_agent` or an `ai_benchmark.agents` entry point in your package. No core changes.
- **New stack:** add `benchmarks/stacks/<id>.yaml` with a pinned image or Dockerfile, and
  `environment_notes` telling the agent where its database is.
- **New task:** add `benchmarks/tasks/<id>/task.yaml` and an HTTP acceptance suite in
  `acceptance/` (see [docs/trial-execution.md](docs/trial-execution.md#writing-an-acceptance-suite)).

## Development

```bash
pytest
ruff check src tests benchmarks
mypy src
BENCHMARK_DOCKER_TESTS=1 pytest tests/integration   # one real trial in Docker
```

How a trial runs, step by step: [docs/trial-execution.md](docs/trial-execution.md).
