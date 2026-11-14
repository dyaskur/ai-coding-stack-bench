"""The ``benchmark`` command-line entry point."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Column, Table

from ai_benchmark import __version__
from ai_benchmark.agents.registry import AgentRegistry, default_registry
from ai_benchmark.comparison import compare_trials
from ai_benchmark.config.experiment import AgentSelection, ExperimentConfig, ToolingConfig
from ai_benchmark.config.loader import (
    SpecCatalog,
    format_validation_error,
    load_experiment,
    load_stack,
    load_task,
    read_document,
)
from ai_benchmark.config.settings import PlatformSettings
from ai_benchmark.environment.docker import DockerExecutionEnvironment
from ai_benchmark.errors import BenchmarkError, ConfigurationError
from ai_benchmark.logging_config import configure_logging
from ai_benchmark.models.enums import AuthMode, ComparisonDimension, ToolingMode, TrialStatus
from ai_benchmark.orchestrator.orchestrator import BenchmarkOrchestrator
from ai_benchmark.storage.base import ResultFilter
from ai_benchmark.storage.json_store import JsonResultStore

app = typer.Typer(
    name="benchmark",
    help="Benchmark AI coding agents across programming languages and frameworks.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)


@dataclass
class AppContext:
    settings: PlatformSettings
    registry: AgentRegistry

    @property
    def catalog(self) -> SpecCatalog:
        return SpecCatalog(self.settings)

    def orchestrator(self) -> BenchmarkOrchestrator:
        return BenchmarkOrchestrator(
            settings=self.settings,
            catalog=self.catalog,
            registry=self.registry,
            environment=DockerExecutionEnvironment(self.settings.docker),
            store=JsonResultStore(self.settings.results_dir),
        )


def _ctx(ctx: typer.Context) -> AppContext:
    obj = ctx.obj
    assert isinstance(obj, AppContext)
    return obj


def _split(value: str | None) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _fail(message: str, code: int = 1) -> typer.Exit:
    err_console.print(f"[red]error:[/red] {message}")
    return typer.Exit(code)


@app.callback()
def main_callback(
    ctx: typer.Context,
    tasks_dir: Annotated[Optional[Path], typer.Option(help="Benchmark task directory.")] = None,
    stacks_dir: Annotated[Optional[Path], typer.Option(help="Stack definition directory.")] = None,
    results_dir: Annotated[Optional[Path], typer.Option(help="Where results are stored.")] = None,
    log_level: Annotated[Optional[str], typer.Option(help="DEBUG, INFO, WARNING, ...")] = None,
    log_format: Annotated[Optional[str], typer.Option(help="'text' or 'json'.")] = None,
) -> None:
    overrides: dict[str, Any] = {
        k: v
        for k, v in {
            "tasks_dir": tasks_dir,
            "stacks_dir": stacks_dir,
            "results_dir": results_dir,
            "log_level": log_level,
            "log_format": log_format,
        }.items()
        if v is not None
    }
    try:
        settings = PlatformSettings(**overrides)
    except ValidationError as exc:
        raise _fail(f"invalid settings:\n{format_validation_error(exc)}") from None
    configure_logging(settings.log_level, settings.log_format)
    ctx.obj = AppContext(settings=settings, registry=default_registry)


@app.command()
def version() -> None:
    """Show the platform version."""
    console.print(f"ai-benchmark {__version__}")


@app.command()
def agents(
    ctx: typer.Context,
    check: Annotated[
        bool, typer.Option("--check", help="Check this host has each agent's credentials.")
    ] = False,
    auth: Annotated[
        AuthMode, typer.Option("--auth", help="Auth mode used by --check.")
    ] = AuthMode.SUBSCRIPTION,
) -> None:
    """List registered coding-agent adapters."""
    registry = _ctx(ctx).registry
    names = registry.names()
    if not names:
        console.print(
            "No agent adapters are registered yet. Adapters (Codex, Claude Code, OpenCode) "
            "arrive in Phase 2; third parties can register via the 'ai_benchmark.agents' "
            "entry point."
        )
        return
    table = Table("name", "display name", "auth modes", "tokens", "cost", "credentials")
    for name in names:
        adapter = registry.create(name)
        caps = adapter.capabilities()
        status = "-"
        if check:
            if auth not in caps.supported_auth_modes:
                status = f"{auth.value} not supported"
            else:
                missing = adapter.default_auth(auth).missing()
                status = "found" if not missing else "; ".join(missing)
        table.add_row(
            name,
            adapter.display_name,
            ", ".join(m.value for m in caps.supported_auth_modes),
            "yes" if caps.reports_token_usage else "no",
            "yes" if caps.reports_cost else "no",
            status,
        )
    console.print(table)


@app.command()
def stacks(ctx: typer.Context) -> None:
    """List available language/framework stacks."""
    try:
        catalog = _ctx(ctx).catalog.stacks()
    except ConfigurationError as exc:
        raise _fail(str(exc)) from None
    if not catalog:
        console.print(f"No stacks found in {_ctx(ctx).settings.stacks_dir}")
        return
    table = Table(Column("id", no_wrap=True), "language", "framework", "image", "services")
    for s in catalog.values():
        table.add_row(
            s.id,
            f"{s.language} {s.language_version}",
            f"{s.framework} {s.framework_version}",
            s.image or f"build: {s.dockerfile}",
            ", ".join(svc.name for svc in s.services) or "-",
        )
    console.print(table)


@app.command()
def tasks(ctx: typer.Context) -> None:
    """List available benchmark tasks."""
    try:
        catalog = _ctx(ctx).catalog.tasks()
    except ConfigurationError as exc:
        raise _fail(str(exc)) from None
    if not catalog:
        console.print(f"No tasks found in {_ctx(ctx).settings.tasks_dir}")
        return
    table = Table(Column("id", no_wrap=True), "name", "mode", "stacks", "trials")
    for t in catalog.values():
        table.add_row(
            t.id,
            t.name,
            t.initial_state.mode.value,
            ", ".join(t.supported_stacks),
            str(t.default_trials),
        )
    console.print(table)


@app.command()
def validate(
    ctx: typer.Context,
    files: Annotated[
        Optional[list[Path]],
        typer.Argument(help="Task, stack or experiment files. Default: the whole catalog."),
    ] = None,
) -> None:
    """Validate task, stack and experiment files."""
    if not files:
        try:
            catalog = _ctx(ctx).catalog
            n_tasks, n_stacks = len(catalog.tasks()), len(catalog.stacks())
        except ConfigurationError as exc:
            raise _fail(str(exc)) from None
        console.print(f"[green]ok[/green] {n_tasks} task(s), {n_stacks} stack(s)")
        return
    failed = False
    for path in files:
        try:
            doc = read_document(path)
            if path.stem == "task":
                load_task(path)
            elif "functional_requirements" not in doc and "agents" in doc:
                load_experiment(path)
            else:
                load_stack(path)
            console.print(f"[green]ok[/green]   {path}")
        except ConfigurationError as exc:
            failed = True
            err_console.print(f"[red]fail[/red] {exc}")
    if failed:
        raise typer.Exit(1)


@app.command()
def run(
    ctx: typer.Context,
    config: Annotated[
        Optional[Path], typer.Option("--config", "-c", help="Experiment YAML/JSON file.")
    ] = None,
    agents_opt: Annotated[
        Optional[str],
        typer.Option("--agents", help="Comma-separated 'agent' or 'agent:model' entries."),
    ] = None,
    task: Annotated[Optional[str], typer.Option("--task", help="Benchmark task id.")] = None,
    stacks_opt: Annotated[
        Optional[str], typer.Option("--stacks", help="Comma-separated stack ids.")
    ] = None,
    trials: Annotated[Optional[int], typer.Option("--trials", min=1)] = None,
    mode: Annotated[
        Optional[ToolingMode], typer.Option("--mode", help="Tooling mode (default: standard).")
    ] = None,
    auth: Annotated[
        Optional[AuthMode],
        typer.Option("--auth", help="Agent auth mode for every agent (default: subscription)."),
    ] = None,
    seed: Annotated[Optional[int], typer.Option("--seed", help="Ordering seed.")] = None,
    no_shuffle: Annotated[
        bool, typer.Option("--no-shuffle", help="Keep the matrix order (not recommended).")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show the trial plan without executing.")
    ] = False,
) -> None:
    """Run a benchmark experiment (use --dry-run to preview the plan)."""
    try:
        if config is not None:
            exp = load_experiment(config)
            updates: dict[str, object] = {}
            if agents_opt:
                updates["agents"] = [AgentSelection.parse(a) for a in _split(agents_opt)]
            if task:
                updates["task"] = task
            if stacks_opt:
                updates["stacks"] = _split(stacks_opt)
            if trials:
                updates["trials"] = trials
            if seed is not None:
                updates["seed"] = seed
            if no_shuffle:
                updates["randomize_order"] = False
            if mode is not None:
                updates["tooling"] = {**exp.tooling.model_dump(), "mode": mode}
            if auth is not None:
                updates["auth_mode"] = auth
            exp = ExperimentConfig.model_validate({**exp.model_dump(), **updates})
        else:
            if not (agents_opt and task and stacks_opt):
                raise _fail("provide --config, or all of --agents, --task and --stacks", 2)
            exp = ExperimentConfig(
                task=task,
                agents=[AgentSelection.parse(a) for a in _split(agents_opt)],
                stacks=_split(stacks_opt),
                trials=trials,
                tooling=ToolingConfig(mode=mode or ToolingMode.STANDARD),
                auth_mode=auth or AuthMode.SUBSCRIPTION,
                seed=seed,
                randomize_order=not no_shuffle,
            )
    except ValidationError as exc:
        raise _fail(f"invalid experiment:\n{format_validation_error(exc)}") from None
    except ConfigurationError as exc:
        raise _fail(str(exc)) from None

    orchestrator = _ctx(ctx).orchestrator()
    try:
        plan = orchestrator.plan(exp)
    except BenchmarkError as exc:
        raise _fail(str(exc)) from None

    console.print(
        f"Run [bold]{plan.run_id}[/bold]: task [bold]{plan.task_id}[/bold], "
        f"{len(plan.trials)} trial(s), {plan.trials_per_combination} per combination, "
        f"tooling mode {exp.tooling.mode.value}, auth {plan.auth_mode.value}, seed {plan.seed}"
    )
    table = Table("#", "trial id", "agent", "model", "stack", "repetition")
    for t in plan.trials:
        table.add_row(
            str(t.execution_order + 1),
            t.trial_id,
            t.agent.agent,
            t.agent.model or "(default)",
            t.stack_id,
            str(t.trial_index + 1),
        )
    console.print(table)

    issues = orchestrator.preflight(plan)
    for issue in issues:
        colour = "red" if issue.severity == "error" else "yellow"
        err_console.print(f"[{colour}]{issue.severity}[/{colour}] {issue.subject}: {issue.message}")
    if dry_run:
        return
    if any(i.severity == "error" for i in issues):
        raise _fail("pre-flight checks failed; nothing was executed")
    try:
        orchestrator.run(exp)
    except NotImplementedError as exc:
        raise _fail(str(exc), 3) from None


@app.command()
def results(
    ctx: typer.Context,
    run_id: Annotated[Optional[str], typer.Option("--run", help="Only this run.")] = None,
    agent: Annotated[Optional[str], typer.Option()] = None,
    model: Annotated[Optional[str], typer.Option()] = None,
    stack: Annotated[Optional[str], typer.Option()] = None,
    language: Annotated[Optional[str], typer.Option()] = None,
    framework: Annotated[Optional[str], typer.Option()] = None,
    task: Annotated[Optional[str], typer.Option()] = None,
    mode: Annotated[Optional[ToolingMode], typer.Option("--mode")] = None,
    status: Annotated[Optional[TrialStatus], typer.Option()] = None,
    auth: Annotated[Optional[AuthMode], typer.Option("--auth")] = None,
    rate_limited: Annotated[
        Optional[bool],
        typer.Option("--rate-limited/--not-rate-limited", help="Only (or exclude) flagged trials."),
    ] = None,
) -> None:
    """List stored trial results, with optional filters."""
    store = JsonResultStore(_ctx(ctx).settings.results_dir)
    filters = ResultFilter(
        run_id=run_id,
        agent=agent,
        model=model,
        stack=stack,
        language=language,
        framework=framework,
        task=task,
        tooling_mode=mode,
        status=status,
        auth_mode=auth,
        rate_limited=rate_limited,
    )
    try:
        rows = list(store.iter_trials(filters))
    except BenchmarkError as exc:
        raise _fail(str(exc)) from None
    if not rows:
        console.print("No results match.")
        return
    table = Table(
        "run", "trial", "task", "agent", "model", "stack", "mode", "auth", "status", "agent time"
    )
    for r in rows:
        secs = r.metrics.performance.agent_execution_seconds
        table.add_row(
            r.run_id,
            r.trial_id,
            r.task_id,
            r.agent.name,
            r.agent.model_label,
            r.environment.stack,
            r.tooling.mode.value,
            r.agent.auth_mode.value,
            r.status.value + (" (rate limited)" if r.rate_limited else ""),
            f"{secs.value:.1f}s" if secs.value is not None else "n/a",
        )
    console.print(table)


@app.command()
def compare(
    ctx: typer.Context,
    by: Annotated[
        ComparisonDimension, typer.Option("--by", help="Dimension to compare.")
    ] = ComparisonDimension.STACK,
    run_id: Annotated[Optional[str], typer.Option("--run")] = None,
    agent: Annotated[Optional[str], typer.Option()] = None,
    model: Annotated[Optional[str], typer.Option()] = None,
    stack: Annotated[Optional[str], typer.Option()] = None,
    task: Annotated[Optional[str], typer.Option()] = None,
    mode: Annotated[Optional[ToolingMode], typer.Option("--mode")] = None,
    auth: Annotated[Optional[AuthMode], typer.Option("--auth")] = None,
    min_trials: Annotated[int, typer.Option("--min-trials", min=1)] = 3,
) -> None:
    """Compare success rates and passing agent times from stored trials."""
    store = JsonResultStore(_ctx(ctx).settings.results_dir)
    filters = ResultFilter(
        run_id=run_id, agent=agent, model=model, stack=stack, task=task,
        tooling_mode=mode, auth_mode=auth,
    )
    try:
        rows = compare_trials(list(store.iter_trials(filters)), by, min_trials)
    except (BenchmarkError, ValueError) as exc:
        raise _fail(str(exc)) from None
    if not rows:
        console.print("No results match.")
        return
    table = Table(
        by.value, "passed/trials", "rate limited", "timed", "mean agent time", "std dev",
        "confidence",
    )
    for row in rows:
        table.add_row(
            row.value,
            f"{row.passed}/{row.trials}",
            str(row.rate_limited),
            str(row.timed),
            f"{row.mean_seconds:.1f}s" if row.mean_seconds is not None else "n/a",
            f"{row.stdev_seconds:.1f}s" if row.stdev_seconds is not None else "n/a",
            "low" if row.low_confidence else "ok",
        )
    console.print(table)


@app.command()
def report(
    ctx: typer.Context,
    run_id: Annotated[Optional[str], typer.Option("--run")] = None,
    fmt: Annotated[str, typer.Option("--format", help="markdown, json or csv.")] = "markdown",
) -> None:
    """Generate an aggregated report (Phase 3)."""
    raise _fail("reporting is not implemented yet (planned for Phase 3)", 3)


def main() -> None:
    app()
