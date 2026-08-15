# Development Guides

Guides for developing, testing, and contributing to General Ludd.

## Contents

This directory does not yet hold split-out development pages. Setup,
testing, and contribution workflow are in the root
[CONTRIBUTING.md](../../CONTRIBUTING.md); guardrail policy is in
[AGENTS.md](../../AGENTS.md). The quick start and make-target reference below
cover day-to-day workflow.

## Quick Start

```bash
# Clone and setup
git clone https://github.com/sandboxcom/gludd.git
cd gludd
make init
make bootstrap

# Run tests
make test              # Full suite with coverage
make test-unit         # Unit tests only (fast)
make test-integration  # Integration tests
make test-e2e          # End-to-end tests

# Quality gates
make lint              # Ruff (0 errors required)
make typecheck         # MyPy (gate enforces ≤ MYPY_MAX)
make gate              # Full gate: lint + typecheck + collect + test + smoke
make validate          # Gate + ansible syntax + healthcheck
```

## TDD Workflow

**Required for every change:**

1. Write a failing test FIRST
2. Run `make test-unit` — confirm it fails
3. Write minimal implementation to make it pass
4. Run `make test-unit` — confirm it passes
5. Refactor if needed, keeping tests green

## Commit Policy

- One logical change per commit
- Messages are imperative: `Add`, `Fix`, `Remove`, `Update`
- Run `make gate` before committing
- Use `make test-and-commit MSG="message"` for atomic test-then-commit

## Feature Branch Workflow

```bash
make feature-start MSG='feature/my-feature'  # Create branch
# ... work, test, commit ...
make feature-done MSG='feature/my-feature'   # Test + merge to master with --no-ff
```

## Key Make Targets

| Target | Description |
|--------|-------------|
| `make init` | Set up directories and dependencies |
| `make sync` | Sync uv dependencies |
| `make bootstrap` | init + lint + test + healthcheck |
| `make test` | Full test suite with coverage |
| `make test-unit` | Unit tests only |
| `make test-integration` | Integration tests |
| `make test-e2e` | End-to-end tests |
| `make test-molecule SCENARIO=x` | Run one Molecule scenario |
| `make molecule-test-all` | All Molecule scenarios |
| `make lint` | Ruff linter |
| `make lint-fix` | Ruff with auto-fix |
| `make typecheck` | MyPy type checker |
| `make gate` | Full gate (writes `.gate-status`) |
| `make preflight` | Preflight quality gate |
| `make validate` | Full validation including ansible |
| `make healthcheck` | Verify imports work |
| `make collect-check` | Fast collection-error gate |
| `make smoke` | Real daemon boot health check |
| `make clean` | Remove build artifacts |

## Project Structure

```text
src/general_ludd/
├── cli.py                    # CLI entry point
├── daemon.py                 # FastAPI daemon
├── db/                       # Database (SQLAlchemy + Alembic)
├── event_loop/               # Event loop + phases
├── models/                   # Model gateway + routing
├── routers/                  # FastAPI routers
├── worker/                   # Worker process
├── ansible/                  # Ansible runner adapter
├── security/                 # Permissions, sandboxes, STS
├── execution/                # Execution engine
├── remediation/              # Blocker detection + remediation
├── collections/              # Collection importer
├── observe/                  # Observability connectors
├── receiver/                 # Receiver (OTLP, webhook, GELF)
├── issue_sources/            # Issue source connectors
├── pipeline/                 # Pipeline controller
├── self_update/              # Self-update mechanism
├── ornith/                   # Ornith integration
├── scheduling/               # Scheduler
├── dispatch/                 # Dynamic dispatcher
├── infra/                    # Terraform, Slurm, local inference
├── pricing_intel/            # Pricing catalog
├── observability/            # Metrics, traces, recorder
└── skills/                   # Skill system
```

## Related Docs

- [Architecture](../architecture/) — System architecture
- [API Reference](../api/) — REST API
- [Design Documents](../design/) — Design specs
- [Operations](../operations/) — Monitoring, budget, troubleshooting

---

[Back to Documentation Index](../index.md)
