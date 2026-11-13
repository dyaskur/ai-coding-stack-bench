# Contributing

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

Before opening a pull request, all of these must pass:

```bash
pytest
ruff check src tests
ruff format --check src tests
mypy src
```

## Commit messages

We follow [Conventional Commits 1.0](https://www.conventionalcommits.org/en/v1.0.0/).

```
<type>(<optional scope>): <summary>

<body: what changed and why>

<optional footer, e.g. BREAKING CHANGE: ... or Refs #12>
```

**Summary line**

- Imperative mood, lower case, no trailing period: `add`, not `added` / `adds`.
- At most 72 characters including the type and scope.

**Types**

| Type | Use for |
| --- | --- |
| `feat` | A new capability for users or extension authors |
| `fix` | A bug fix |
| `docs` | Documentation only |
| `test` | Adding or changing tests only |
| `refactor` | Code change that neither fixes a bug nor adds a feature |
| `perf` | Performance improvement |
| `build` | Packaging, dependencies, Docker images |
| `ci` | CI configuration |
| `chore` | Maintenance that doesn't fit the above |

**Scopes** match the package layout: `models`, `config`, `agents`,
`environment`, `orchestrator`, `storage`, `cli`, plus `architecture` for
ARCHITECTURE.md. A new agent adapter uses its name, e.g. `feat(agents/codex): ...`.

**Body** explains why the change is needed and anything a reviewer can't
see from the diff. Wrap at 72 characters.

**Breaking changes** to a schema, the adapter interface or the CLI add `!`
after the type/scope (`feat(models)!: ...`) and a `BREAKING CHANGE:` footer
describing the migration.

**One logical change per commit.** Each commit should build and pass the
tests on its own.

Do not add co-author or other attribution trailers.

## Branches and pull requests

- Branch from `main` as `<type>/<short-description>`, e.g. `feat/codex-adapter`.
- Pull request titles follow the same Conventional Commits format.
- Never commit secrets. Configs reference credentials by environment
  variable name or file path only (see docs/agent-execution.md).
