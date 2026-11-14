# GPT-6 Luna: 20 valid trials per updated stack

Run date: 13 November 2026 (UTC). Task: `task-management-api` version 2. Agent: Codex CLI requesting `gpt-6-luna`, subscription authentication. Platform commit used for the runs: `2559214850aaa344deeef443f318946231e88948`.

## Reproduce and inspect

The main run is `results/20261113T152047Z-3a2559/` (seed `3004873834`). Its manifest and 60 trial JSON files record the full configuration and measurements. One Laravel trial (`t-350283695e7b`) stopped during setup when Packagist timed out; it did not reach the agent or acceptance tests. The replacement trial is `t-b8eb26831b45` in `results/20261113T180853Z-1518c0/`. This replacement was a separate single-trial run with its own generated seed `2617491874`, not a replay of the original trial ID.

```bash
benchmark run --agents codex:gpt-6-luna --task task-management-api \
  --stacks fastapi-latest,laravel13-boost,express-latest \
  --trials 20 --auth subscription --seed 3004873834
```

Results and workspaces are excluded from Git by `.gitignore`; the run IDs above identify the raw evidence on the benchmark server. There were 61 attempts: 60 trials reached acceptance tests and one setup attempt had an infrastructure error. Statistics and token totals below use the 60 valid trials, including the replacement. "Passed" means all 53 HTTP acceptance tests passed. "Failed" means at least one acceptance test failed.

## Outcomes

| Stack | Complete trials | Passed | Failed | Acceptance tests passed |
| --- | ---: | ---: | ---: | ---: |
| FastAPI 0.162.6 / Python 3.15.2 | 20 | 19 | 1 | 1,057 / 1,060 |
| Laravel 13 + Boost / PHP 8.5.12 | 20 | 13 | 7 | 1,049 / 1,060 |
| Express 5.3.0 / Node 26.13.1 | 20 | 20 | 0 | 1,060 / 1,060 |
| **Total** | **60** | **52** | **8** | **3,166 / 3,180** |

All 60 valid trials had token and time measurements. No rate-limit events were recorded.

## Time per trial

All values are seconds. Agent time measures only agent execution; wall-clock time also includes container startup, stack setup, and acceptance tests. The infrastructure-error attempt is excluded from both series.

| Stack | Agent mean | Agent median | Agent min | Agent max | Wall mean | Wall median | Wall min | Wall max |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FastAPI | 122.44 | 120.50 | 104.09 | 179.63 | 135.40 | 133.38 | 116.88 | 192.50 |
| Laravel + Boost | 173.07 | 149.79 | 120.68 | 268.86 | 227.15 | 206.51 | 171.94 | 319.22 |
| Express | 122.66 | 122.21 | 94.98 | 144.49 | 138.53 | 138.19 | 110.09 | 160.58 |

The main run took 2 h 42 m 9 s. Elapsed time from the main run's start through completion of the replacement was 2 h 53 m 25 s, including the gap between runs. The replacement itself took 5 m 19 s.

## Tokens and API-equivalent estimate

Input tokens include cached input. `Total tokens = input + output`. Cost is estimated using the [OpenAI Standard short-context GPT-6 Luna prices](https://developers.openai.com/api/docs/pricing) on 13 November 2026: $0.10 per million non-cached input tokens, $0.01 per million cached input tokens, and $0.50 per million output tokens. Formula: `((input - cached) * 0.10 + cached * 0.01 + output * 0.50) / 1,000,000`. Subscription authentication did not provide a per-run bill; these amounts are API equivalents, not charges.

| Stack | Input | Cached input | Output | Total | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: |
| FastAPI | 2,915,617 | 2,616,064 | 121,636 | 3,037,253 | 0.116934 |
| Laravel + Boost | 13,344,068 | 12,463,872 | 157,072 | 13,501,140 | 0.291194 |
| Express | 3,523,787 | 3,182,464 | 110,763 | 3,634,550 | 0.121338 |
| **Total** | **19,783,472** | **18,262,400** | **389,471** | **20,172,943** | **0.529467** |

## Interpretation limits

This is one REST API task with 20 trials per stack, not a broad measure of framework quality. The starting conditions differ: FastAPI has dependencies preinstalled in its image but no project files; Express starts with an npm package and Express installed; Laravel starts with a full Laravel project, Boost, guidance, skills, and an MCP configuration. Laravel also uses MySQL, while FastAPI and Express use PostgreSQL. Setup time is outside agent time but inside wall-clock time. These differences prevent attributing timing or success-rate gaps to the framework alone.

The CLI requested and resolved `gpt-6-luna`, but its result format does not independently report the model returned by Codex. The reported token usage comes from the agent adapter. The API estimate assumes short-context Standard pricing; actual API charges could differ with request-level context length or pricing mode.
