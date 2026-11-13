# Running benchmarks on a Linux VPS

Tested target: Ubuntu 24.04 LTS (x86_64). Other systemd-based distributions
work with equivalent package commands.

## 1. Size the machine

Trials run one at a time. Each trial gets a stack container plus its database
sidecar, by default 2 CPUs and 4 GB for the stack container.

- Minimum: 4 vCPU, 8 GB RAM, 60 GB SSD.
- Docker images (one per stack x agent) take most of the disk. Budget about
  2 to 4 GB per stack.

## 2. Create a dedicated user

Membership in the `docker` group is equivalent to root, so keep benchmarks on
their own account.

```bash
sudo adduser --disabled-password --gecos "" bench
sudo usermod -aG sudo bench        # optional, for setup only
```

Basic hardening: SSH keys only, and a firewall that allows SSH and nothing else
inbound (the platform needs no inbound ports).

```bash
sudo ufw allow OpenSSH && sudo ufw enable
```

## 3. Install Docker Engine

Use Docker's official apt repository (see docs.docker.com/engine/install/ubuntu):

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
sudo usermod -aG docker bench
```

Log out and back in as `bench`, then check: `docker run --rm hello-world`.

## 4. Install the platform

Only Python 3.10+ and git are needed on the host. Language runtimes and agent
CLIs live in the Docker images.

```bash
sudo apt-get install -y git python3 python3-venv
git clone <your repo url> ai-benchmark && cd ai-benchmark
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
benchmark validate
```

## 5. Provide credentials (subscription mode)

Credentials stay on the host; the platform injects them into each trial
container. Keep them in one file only `bench` can read:

```bash
install -m 600 /dev/null ~/.config/ai-benchmark.env
```

**Claude Code.** On any machine where you are logged in, run
`claude setup-token`, then add the token to the env file:

```bash
echo 'CLAUDE_CODE_OAUTH_TOKEN=<token>' >> ~/.config/ai-benchmark.env
```

**Codex CLI.** Log in where you have a browser (`codex login`), then copy the
login file to the VPS. If your Codex version supports device-code login
(`codex login --help`), you can log in on the VPS directly instead.

```bash
# from your laptop
scp ~/.codex/auth.json bench@<vps>:~/.codex/auth.json
ssh bench@<vps> chmod 600 ~/.codex/auth.json
```

**OpenCode.** Run `opencode auth login` locally and copy
`~/.local/share/opencode/auth.json` the same way.

You don't need the agent CLIs installed on the VPS for benchmarking; the
copied files are enough.

Load the env file before running and confirm everything is found:

```bash
set -a; . ~/.config/ai-benchmark.env; set +a
benchmark agents --check --auth subscription
```

Exact file locations and variable names are confirmed per agent in Phase 2;
see [agent-execution.md](agent-execution.md) for the current defaults and how
to override them.

> Check each provider's terms for automated use of a subscription account. Use
> an account or token dedicated to benchmarking so it can be revoked without
> affecting your own work.

## 6. Run

Runs take hours, so use `tmux` so they survive SSH disconnects:

```bash
sudo apt-get install -y tmux && tmux new -s bench
set -a; . ~/.config/ai-benchmark.env; set +a
benchmark run -c examples/experiment.yaml --dry-run   # check the plan
benchmark run -c examples/experiment.yaml
```

Subscription usage limits are shared across trials. Trials that hit one are
flagged and can be excluded: `benchmark results --not-rate-limited`.

## 7. Housekeeping

- Results are written to `results/` (override with `BENCHMARK_RESULTS_DIR`). Back them up.
- Reclaim disk from old images: `docker image prune` (keeps images still in use).
- Keep the host patched: `sudo apt-get upgrade` between runs, not during one.
