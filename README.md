# AI Coding Benchmark Platform

An open-source platform that measures and compares how productive AI coding
agents (Codex CLI, Claude Code, OpenCode, and more) are across programming
languages and frameworks.

> **Status: Phase 1 (foundation).** The configuration schemas, agent adapter
> interface, Docker environment and orchestrator skeletons, result storage and
> CLI skeleton are in place. Nothing executes agents yet. See
> [ARCHITECTURE.md](ARCHITECTURE.md) for the full vision and
> [docs/phase-1.md](docs/phase-1.md) for what exists and what comes next.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
benchmark --help
```

Requires Python 3.10+. Docker will be required once execution lands in Phase 2.
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

Platform settings come from `BENCHMARK_*` environment variables (nested with
`__`, e.g. `BENCHMARK_DOCKER__DEFAULT_MEMORY_LIMIT=8g`) or global CLI options
such as `--results-dir`.

## Layout

```
src/ai_benchmark/
  cli/            Typer entry point (`benchmark`)
  config/         Pydantic schemas: tasks, stacks, experiments, platform settings; YAML/JSON loader
  models/         Enums, metric schemas (with explicit availability), result records
  agents/         AgentAdapter interface + registry (entry-point group `ai_benchmark.agents`)
  environment/    ExecutionEnvironment interface, Docker skeleton, per-trial workspaces
  orchestrator/   Experiment planning (randomized, seeded), timing, orchestrator skeleton
  storage/        ResultStore interface + atomic JSON file store
benchmarks/
  tasks/<id>/task.yaml   Benchmark task definitions
  stacks/<id>.yaml       Stack (language + framework + services) definitions
examples/experiment.yaml Example experiment matrix
tests/                   Unit tests (pytest)
```

## Extending

- **New agent:** subclass `ai_benchmark.agents.AgentAdapter` and register it with
  `@register_agent` or an `ai_benchmark.agents` entry point in your package. No core changes.
- **New stack:** add `benchmarks/stacks/<id>.yaml` with a pinned image or Dockerfile.
- **New task:** add `benchmarks/tasks/<id>/task.yaml`.

## Development

```bash
pytest
ruff check src tests
mypy src
```
