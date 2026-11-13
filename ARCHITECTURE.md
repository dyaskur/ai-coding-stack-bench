Build an AI Coding Benchmark Platform

Act as a senior software architect and engineer. Build a production-quality, extensible, open-source platform for benchmarking AI coding agents across programming languages and frameworks.

1\. Project Objective

Build a platform that measures and compares the software development productivity of different AI coding agents.

The platform must support multiple agents, including:

* OpenAI Codex CLI

* Claude Code CLI

* OpenCode

The architecture must allow additional coding agents to be integrated in the future without modifying the benchmark core.

It must also support multiple programming languages and frameworks, such as:

* PHP / Laravel

* Python / FastAPI

* JavaScript / Express

* Go / Gin

* Ruby / On Rails

* Other stacks through extensible configuration.

The main objective is to answer:

"Which AI coding agent, model, programming language, and framework combination delivers the best development productivity?"

2\. Core Architecture

Use a modular architecture with these main components:

A. Benchmark Orchestrator

Responsible for:

* Managing benchmark experiments.

* Selecting coding agents, models, languages, and frameworks.

* Managing experiment configurations.

* Creating isolated workspaces.

* Starting and stopping timers.

* Running multiple trials.

* Collecting execution logs and metrics.

* Running automated acceptance tests.

* Generating benchmark reports.

B. AI Agent Adapters

Create a provider-independent adapter interface.

Each adapter must implement a common contract:

* Check whether the agent CLI is installed.

* Check authentication and availability.

* Retrieve agent version.

* Execute a coding task.

* Pass the prompt and working directory.

* Capture stdout and stderr.

* Capture exit status.

* Capture token usage when available.

* Capture model information.

* Capture cost when available.

* Preserve raw agent output and logs.

Implement adapters for:

1. Codex CLI

2. Claude Code CLI

3. OpenCode CLI

IMPORTANT:

Do not assume all agents have identical CLI commands, JSON output formats, token accounting, or cost reporting.

Inspect the current official documentation and actual CLI capabilities before implementing each adapter.

Normalize available metrics into a common schema while preserving provider-specific raw data.

If an agent does not expose a metric, report it as unavailable rather than inventing or estimating it silently.

C. Execution Environment

Use Docker to isolate programming language and framework environments.

Users should NOT need to install individual programming languages, compilers, databases, or framework dependencies on their host machine.

Only the platform's basic prerequisites should be required.

Requirements:

* Create reusable Docker images for each supported stack.

* Pin runtime and dependency versions.

* Provide isolated containers and workspaces per experiment.

* Ensure one experiment cannot affect another.

* Mount source code and test files into the appropriate environment.

* Support database services when required.

* Automatically build or pull images when needed.

* Cache Docker images between experiments.

* Clean up temporary resources after each run.

The AI agent must be able to work on the project while application commands, builds, and tests execute in the isolated environment.

Do not allow benchmark commands to accidentally use host-installed language runtimes.

Design the execution and permission model carefully. Avoid exposing unrestricted host access or the Docker socket to untrusted agent-generated code.

D. Benchmark Specifications

Benchmark tasks must be independent from the agent and language implementations.

Use a standardized benchmark specification format, preferably YAML or JSON.

A benchmark definition should include:

* Task name and description.

* Functional requirements.

* Technical requirements.

* Initial project state.

* Agent prompt template.

* Supported language/framework combinations.

* Docker environment configuration.

* Acceptance test commands.

* Timeouts.

* Trial count.

* Optional extension phases.

It must be possible to add a new benchmark task without modifying the orchestrator.

3\. Benchmark Workflow

Implement the following workflow:

1. User selects agents, models, languages, and frameworks.

2. User selects a benchmark task.

3. The orchestrator creates a clean workspace.

4. The appropriate Docker environment is prepared.

5. The selected AI agent receives the benchmark prompt.

6. The timer starts immediately before the agent begins working.

7. The agent implements the application.

8. The agent execution ends.

9. The acceptance test suite runs independently.

10. The orchestrator records the result.

11. The next experiment starts with a fresh environment.

12. The system aggregates results across trials.

Support both:

* Build from scratch.

* Extend an existing implementation.

For the first implementation, prioritize sequential execution. Design the architecture so parallel execution can be added later.

4\. Fair Benchmarking Rules

Fairness is critical.

The platform must ensure:

* Identical functional requirements.

* Identical acceptance tests.

* Identical initial project state.

* Identical model configuration where applicable.

* Identical reasoning/effort settings where supported.

* Identical timeout limits.

* Identical database and infrastructure specifications.

* No manual intervention during a trial.

* Randomized experiment ordering to reduce ordering bias.

* Independent workspaces for every trial.

Support two tooling modes:

Standard Mode

All agents receive equivalent basic tool access.

Best Ecosystem Mode

Agents can use ecosystem-specific tools, MCP servers, and framework integrations.

Record all enabled tools and MCP configurations as part of the experiment metadata.

Do not mix results from these two modes without clearly labeling them.

5\. Metrics

Capture the following metrics for every experiment:

Performance

* Agent execution time.

* Time to first implementation.

* Time until all acceptance tests pass, if repair iterations are enabled.

* Total wall-clock time.

AI Usage

* Input tokens.

* Output tokens.

* Cached input tokens.

* Reasoning tokens, if available.

* Total reported tokens.

* API/model cost.

* Number of agent turns or interactions, if available.

Quality

* Acceptance tests passed.

* Acceptance tests failed.

* Success/failure status.

* Retry and repair count.

* Manual intervention count.

Additional Information

* Lines of code.

* Agent version.

* Model identifier.

* Language and framework versions.

* Docker image identifier.

* Git commit hash.

* Full execution logs.

Clearly distinguish unavailable metrics from zero values.

Do not compare token counts across different providers as if their tokenization and accounting were necessarily equivalent.

6\. Results and Reporting

Generate:

* JSON results for machine-readable data.

* CSV exports.

* Markdown reports.

* Visual comparison charts.

* Per-agent rankings.

* Per-language/framework rankings.

* Cost versus completion-time comparisons.

* Success-rate comparisons.

* Trial averages and standard deviations.

* Create PR to add benchmark data to the repo and the website

Support filtering results by:

* Agent.

* Model.

* Language.

* Framework.

* Benchmark task.

* Tooling mode.

* Auth mode.

* Rate-limited or not.

The results must be reproducible. Store all relevant experiment metadata.

Comparison Views

The platform must answer two questions equally well:

* Stack comparison: for one agent + model, which language/framework stack is fastest?

* Model comparison: for one stack, which agent + model is fastest?

Both are special cases of one pivot: the user picks a "compare by" dimension
(agent, model, agent + model, stack, language, framework, or task) and fixes
the others with filters. The CLI and the dashboard use the same comparison
query, so the dashboard only adds a selector on top of it.

The unit for model comparison is agent + model, because the same model can
behave differently through different agents. Comparing a model across agents
is allowed as an explicit pivot ("compare by agent, model fixed").

The model of a trial is its resolved model: the model the agent reported, or,
if it did not report one, the model requested in the experiment. Experiments
used for model comparison should always set the model explicitly; a run that
relies on an agent's default model gets a pre-flight warning.

Rules for a valid comparison:

* All compared groups share the same task and task version, tooling mode and auth mode.

* "Fastest" means the lowest mean agent execution time over trials that
  passed every acceptance test, always shown together with the success rate
  and the number of trials. A group with no passing trials is ranked last,
  not dropped.

* Rate-limited trials are excluded from timing by default and counted separately.

* Means are shown with standard deviations; groups with fewer than the
  configured minimum number of trials are marked as low confidence.

7\. User Interface

Start with a functional CLI.

Example commands:

```
benchmark agents
benchmark stacks
benchmark tasks
benchmark run
benchmark results
benchmark report
```

Example experiment:

```
benchmark run \
  --agents codex,claude,opencode \
  --task task-management-api \
  --stacks laravel,fastapi,express \
  --trials 5
```

The exact CLI syntax can be refined during implementation.

A web dashboard can be implemented as a later phase. Do not let dashboard development delay the core benchmarking engine.

8\. Project Quality

Requirements:

* Clean and modular architecture.

* Strong separation of concerns.

* Automated unit and integration tests.

* Structured logging.

* Clear error handling.

* Configuration validation.

* Documentation for installation and usage.

* Example benchmark definitions.

* Example Docker environments.

* Easy agent and framework extension.

Do not hardcode agent-specific logic into the orchestrator.

Do not silently skip failed experiments.

Do not fabricate token usage or cost.

9\. Implementation Strategy

Work incrementally.

Phase 1:

* Project structure.

* Configuration schema.

* Agent adapter interface.

* Docker execution environment.

* Benchmark orchestrator.

* Basic result storage.

Phase 2:

* Codex, Claude Code, and OpenCode adapters.

* One representative benchmark task.

* One PHP/Laravel environment.

* One Python/FastAPI environment.

* Automated acceptance tests.

Phase 3:

* More language/framework environments.

* Multiple trials.

* Metrics aggregation, including the comparison query (compare by one
  dimension, filter the rest) and a `benchmark compare` command.

* Flat per-trial exports (CSV/JSONL) that a dashboard can pivot.

* Reports and charts.

Phase 4:

* Repair iterations.

* Parallel execution.

* Web dashboard with a "compare by" selector (stack comparison, model comparison, or any other dimension).

* Additional agents and benchmark tasks.

Start by inspecting the environment and proposing the architecture and implementation plan. Then implement the project incrementally.

Do not stop after writing a design document. Build a working MVP, execute its tests, fix implementation problems, and document what works and what remains incomplete.

Prioritize a reliable and reproducible benchmarking engine over an elaborate user interface.
