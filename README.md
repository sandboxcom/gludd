# General Ludd Agent

The black swan agentic coding system — an autonomous, Ansible-driven, multi-model AI agent
that submits coding tasks and produces real, committed, reviewed, and reconciled code changes.

## What Is This?

General Ludd is an **autonomous software engineering agent**. You submit a todo — "add
end-to-end encryption to the API," "fix the race condition in the job queue," "upgrade all
dependencies and run the test suite" — and the system dispatches it to an AI model, runs the
generated code through a validation pipeline (tests, lint, typecheck, quality gates), reviews
the result with a separate model, and lands the change in git. It is not a chatbot or a
copilot. It is a daemon that loops: claim, dispatch, review, reconcile, repeat.

## Who Is This For?

- **Platform and infrastructure teams** who want autonomous agents managing configuration
  drift, dependency updates, and security patches across dozens of repositories.
- **AI/ML researchers and operators** experimenting with multi-model agent architectures,
  adaptive model routing, and benchmark-driven model selection.
- **SREs and DevOps engineers** who already use Ansible and want an agent that can execute
  playbooks, validate results, and open pull requests with evidence trails.
- **Anyone deploying LLM-based coding agents** who needs budget guards, cost tracking,
  per-model benchmarking, and a quality gate that actually blocks bad code.

## Current Stability

This project is **alpha-quality research software**. The test suite contains over 5,600
tests (5,460 passing, 116 known failures as of the latest baseline). The daemon boots, the
event loop ticks, the database layer works, and the model gateway can call real APIs. But
many subsystems are wired but not fully exercised end-to-end. **Do not run this in
production without understanding the failure modes.** Expect rough edges around Ansible
playbook execution, multi-model failover, and project workspace management.

Key numbers:
- **5,654 tests collected**, 5,460 passing, 116 known pre-existing failures
- **21 mypy errors** across 10 files (within baseline of 25)
- **0 lint errors** (ruff, strict)
- **0 `# noqa` / `# type: ignore` / `# nosec` comments** in source — all suppressions
  resolved or moved to `pyproject.toml`
- **10 pre-commit hooks** enforce secrets scanning (detect-secrets), linting (ruff),
  collection-check (pytest --co), trailing whitespace, private key detection, and more

Version: `v0.1.0-alpha` — prereleases are built automatically on every push to master and
published as GitHub Releases with artifacts for Linux (x86_64, aarch64), macOS (arm64), and
Windows (x86_64).

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Git
- An API key for at least one model provider (Z.AI GLM, OpenAI, DeepSeek, or OpenRouter)

### Install and Verify

```bash
git clone https://github.com/sandboxcom/gludd.git
cd gludd
make init        # set up directories and dependencies
make bootstrap   # init + lint + test + healthcheck
make help        # list all available make targets
```

### Start the Daemon

```bash
# Quick start with defaults (SQLite, no model key — will warn)
uv run gludd daemon --port 8000

# With a config directory and model profile
uv run gludd daemon --config-dir ~/.config/general-ludd --port 8000
```

### Submit Your First Todo

```bash
uv run gludd todo add "Write a unit test for the login endpoint" --queue core
uv run gludd todo list --status queued
uv run gludd status
```

### Check Health and Metrics

```bash
uv run gludd health
uv run gludd version
curl http://localhost:8000/healthz
curl http://localhost:8000/admin/metrics/export
```

## Example Configurations

Getting started requires setting up at least one model profile and a basic config. The repo
ships with example files you can copy and customize.

### Minimal Config (`~/.config/general-ludd/general-ludd.yml`)

```yaml
model_routing:
  default_profile: zai_coder

database:
  host: localhost
  port: 5432
  name: gludd
  user: gludd

budget:
  max_usd: 50
  warn_percent: 80
```

### Model Profiles

Copy from the shipped examples and add your API key:

```bash
mkdir -p ~/.config/general-ludd/model_profiles
cp config/model_profiles/zai_example.yml ~/.config/general-ludd/model_profiles/zai_coder.yml
# Edit zai_coder.yml and set your API key as the ZAI_API_KEY env var
```

Available profiles:
- [`config/model_profiles/zai_example.yml`](config/model_profiles/zai_example.yml) — Z.AI GLM-5.1 (primary coder)
- [`config/model_profiles/deepseek_coder.yml`](config/model_profiles/deepseek_coder.yml) — DeepSeek fallback
- [`config/model_profiles/qwen_coder.yml`](config/model_profiles/qwen_coder.yml) — Qwen fallback
- [`config/model_profiles/openai_example.yml`](config/model_profiles/openai_example.yml) — OpenAI GPT-4
- [`config/model_profiles/anthropic_example.yml`](config/model_profiles/anthropic_example.yml) — Claude Sonnet

### Model Routing (`config/model_routing.yml`)

Controls which model is used for each task type. The shipped config routes everything to
`zai_coder` with a fallback chain to `deepseek_coder` and `qwen_coder`:

```yaml
default_profile: zai_coder
fallback_chain:
  - deepseek_coder
  - qwen_coder
role_routing:
  coder: zai_coder
  planner: zai_coder
  reviewer: zai_coder
```

### Secrets (OpenBao)

For secret management, copy the OpenBao config:

```bash
mkdir -p ~/.config/general-ludd/openbao
cp config/openbao/default.yml ~/.config/general-ludd/openbao/default.yml
```

OpenBao supports two modes:
- **external**: Connect to an existing OpenBao or HashiCorp Vault instance
- **auto**: Try external first, fall back to environment variables
- **disabled**: Use environment variables only

On macOS, the daemon automatically prefers Docker over Podman for container-based
OpenBao (Docker Desktop handles port forwarding transparently on macOS).

## Architecture

```
                     ┌─────────────┐
  User ──CLI/TUI──▶  │   Daemon    │  (FastAPI + Gunicorn)
                     │  :8000      │
                     └──────┬──────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
        ┌──────────┐ ┌──────────┐ ┌──────────┐
        │ Event    │ │  Admin   │ │  Todo    │
        │ Loop     │ │  Router  │ │  Router  │
        └────┬─────┘ └──────────┘ └──────────┘
             │
    ┌────────┼────────┬──────────┬──────────┐
    ▼        ▼        ▼          ▼          ▼
  Claim   Dispatch  Review   Reconcile   Self-Improve
    │        │        │          │          │
    │   ┌────▼────┐   │     ┌────▼────┐     │
    │   │ Worker  │   │     │  Git    │     │
    │   │ :8001   │   │     │  Auto   │     │
    │   └────┬────┘   │     └─────────┘     │
    │        │        │                     │
    │   ┌────▼────┐   │                     │
    │   │ Model   │   │                     │
    │   │ Gateway │   │                     │
    │   └────┬────┘   │                     │
    │        │        │                     │
    ▼        ▼        ▼                     ▼
  ┌──────────────────────────────────────────┐
  │            PostgreSQL / SQLite            │
  │     (todos, returns, benchmarks, vars)    │
  └──────────────────────────────────────────┘
```

## Development

### Running Tests

```bash
make test              # full suite with coverage
make test-unit         # unit tests only (fast)
make test-integration  # integration tests
make test-e2e          # end-to-end tests
make test-count        # check collection (0 errors required)
```

### Code Quality

```bash
make lint              # ruff (0 errors required)
make typecheck         # mypy (≤ 25 errors baseline)
make gate              # full gate: lint + typecheck + collect + test
make validate          # gate + ansible syntax + healthcheck
```

### Pre-Commit Hooks

Install once: `make install-hooks`

Every commit runs:
- **trailing-whitespace** — no trailing spaces
- **end-of-file-fixer** — files end with a newline
- **check-yaml / check-json / check-toml** — valid syntax
- **check-added-large-files** — no files over 500 KB
- **detect-private-key** — no SSH/PGP private keys committed
- **no-commit-to-branch** — no direct commits to main
- **detect-secrets** — Yelp detect-secrets scan
- **ruff lint** — Python linting
- **test collection check** — `pytest --co` must succeed

### Git Workflow

```bash
make feature-start MSG='feature/my-feature'   # create branch
# ... work, test, commit ...
make feature-done MSG='feature/my-feature'    # test + merge to master
```

## Configuration Reference

| File | Purpose |
|------|---------|
| [`config/general-ludd.yml`](config/general-ludd.yml) | Main configuration (model routing, database, agents, budget) |
| [`config/model_routing.yml`](config/model_routing.yml) | Model routing with fallback chains |
| [`config/model_profiles/zai_example.yml`](config/model_profiles/zai_example.yml) | Z.AI GLM-5.1 profile |
| [`config/model_profiles/deepseek_coder.yml`](config/model_profiles/deepseek_coder.yml) | DeepSeek profile |
| [`config/model_profiles/qwen_coder.yml`](config/model_profiles/qwen_coder.yml) | Qwen profile |
| [`config/openbao/default.yml`](config/openbao/default.yml) | OpenBao secrets backend |
| [`config/ansible/isolation.yml`](config/ansible/isolation.yml) | Process isolation settings |
| [`config/mcp_servers/example.yml`](config/mcp_servers/example.yml) | MCP server connections |
| [`config/binary_paths.yml`](config/binary_paths.yml) | External binary paths |

## License

MIT
