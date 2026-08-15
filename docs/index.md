# General Ludd Agent — Documentation

Welcome to the General Ludd (`gludd`) documentation. This is the black swan agentic coding system — an autonomous, Ansible-driven, multi-model AI agent that submits coding tasks and produces real, committed, reviewed, and reconciled code changes.

## Quick Navigation

| Section | Description |
|---------|-------------|
| [Architecture](architecture/) | System architecture, daemon, event loop, worker, Ansible integration |
| [API Reference](api/) | REST endpoints, message queue, model gateway |
| [CLI Reference](cli/) | Commands, configuration |
| [Development](development/) | Getting started, testing, guardrails, patterns |
| [Deployment](deployment/) | Terraform, Slurm, release process |
| [Design Documents](design/) | Symbiotic agent integration, collection structure, queue lease audit, remediation, and more |
| [Operations](operations/) | Monitoring, budget management, troubleshooting |
| [E2E Harness](e2e_harness/) | Native dogfood and multi-provider E2E test designs |
| [Integration](integration/) | Integration plans, ship mechanics, review findings |
| [Presentation](presentation/) | Reveal.js deck design and build tasks |
| [GLUDD Porting](gludd_porting/) | Porting harness guardrails to gludd |
| [Audit](audit/) | Audit findings, security reviews, backlog reconciliation |
| [Privileges](privileges/) | Cloud provider privilege requirements |
| [Research](research/) | Model routing recommendations, enumeration models, compaction research |
| [Internal](internal/) | Sprint planning, feature decisions, parity matrix |
| [Profiles](profiles.md) | Model profiles reference |
| [Configuration](configuration.md) | Configuration reference |
| [Config Reference](CONFIG_REFERENCE.md) | **Authoritative** config reference — env vars, config discovery, experimental flags |
| [Quickstart](quickstart.md) | Fast path: install → configure → first todo |
| [Release Runbook](RELEASE_RUNBOOK.md) | How to cut a release and prove it actually shipped |

## Core Concepts

**General Ludd (`gludd`)** is an **autonomous agentic SDLC daemon** (FastAPI). You submit a todo — "add end-to-end encryption to the API," "fix the race condition in the job queue," "upgrade all dependencies and run the test suite" — and the system dispatches it to an AI model, runs the generated code through a validation pipeline (tests, lint, typecheck, quality gates), reviews the result with a separate model, and lands the change in git.

It is not a chatbot or a copilot. It is a daemon with an event loop: **claim → dispatch → review → reconcile → repeat**.

The execution layer is **Ansible**: every task the daemon runs is an Ansible playbook that composes modules from the `general_ludd.agent` collection. This means tasks are auditable, idempotent, and can fan out to subagents via the same API.

## Quick Start

```bash
git clone https://github.com/sandboxcom/gludd.git
cd gludd
make init        # set up directories and dependencies
make bootstrap   # init + lint + test + healthcheck
make help        # list all available make targets
```

> **⚠ Before you start the daemon from a checkout, set `GLUDD_CONFIG_DIR`.** Config
> discovery is `$GLUDD_CONFIG_DIR` → `~/.config/general-ludd` → `/etc/general-ludd`, and
> **the repo's own `config/` directory is NOT on that path.** Without it, no model
> profiles load, the model gateway stays `None`, and the dispatcher silently falls back
> to a **no-op executor**: every agent returns `status="completed"` with empty output and
> no warning, while `/healthz` and `/readyz` still report 200/ready.
>
> ```bash
> export GLUDD_CONFIG_DIR="$PWD/config"
> ```
>
> See [quickstart.md](quickstart.md) and [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) §2.0.

## Experimental Flags — Do Not Enable

| Flag | Status |
|---|---|
| `GLUDD_WRITER_MODE=subprocess` | **Structurally non-functional** — breaks every write endpoint. `inline` is the only working mode. |
| `pipeline.enabled` (feature #77) | **Experimental** — its gate is hardcoded `return True`; its anti-clobber merge can never detect a conflict. |

See [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) §5 for the details.

## Releases

**[RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md) is the authoritative procedure — read it before
touching any release target.** The three facts that get this wrong most often:

- `make release-cut TAG=... MSG='...'` is the **only sanctioned path** to publish a release.
- `make verify-release-completeness TAG=...` is the **real gate** (12 artifact categories,
  prerelease-flag-vs-tag, version-stamped asset names, no zero-size assets).
  `make verify-release-artifact` is **not** the gate — it only proves "non-draft + ≥1 asset".
- `make release-create` **cannot publish a public release.** It is a CI-green-gated,
  **draft-only** single-binary fallback.

## Key Links

- [README.md](../README.md) — Project overview, quick start, architecture diagram
- [WORKFLOWS.md](WORKFLOWS.md) — Current use patterns, feature intake, custom business logic, diagrams, Terraform, and smoke-test handoff
- [SMOKE_TESTS.md](SMOKE_TESTS.md) — Provider, model, compute, and multi-platform smoke tests with report handoff
- [design/PROJECT_COLLECTIONS.md](design/PROJECT_COLLECTIONS.md) — Project-local collection precedence and overrides
- [design/COLLECTION_STRUCTURE.md](design/COLLECTION_STRUCTURE.md) — Bundled collection layout and module conventions
- [design/TERRAFORM_INFRA_STRUCTURE.md](design/TERRAFORM_INFRA_STRUCTURE.md) — Terraform modules, stacks, providers, and validation
- [design/MODEL_SERVING_DEPLOYMENT.md](design/MODEL_SERVING_DEPLOYMENT.md) — Slurm, Terraform, and local model-serving paths
- [CONTRIBUTING.md](../CONTRIBUTING.md) — Contribution guidelines
- [LICENSE](../LICENSE) — MIT License
- [GitHub Repository](https://github.com/sandboxcom/gludd)
- [Issue Tracker](https://github.com/sandboxcom/gludd/issues)
## Documentation Structure

```text
docs/
├── index.md                    # This page
├── architecture/               # System architecture
├── api/                        # API reference
├── cli/                        # CLI reference
├── development/                # Development guides
├── deployment/                 # Deployment guides
├── design/                     # Design documents
├── operations/                 # Operations guides
├── e2e_harness/                # E2E test harness designs
├── integration/                # Integration plans
├── presentation/               # Presentation deck
├── gludd_porting/              # Porting docs
├── audit/                      # Audit findings
├── privileges/                 # Cloud privileges
├── research/                   # Research docs
├── internal/                   # Internal planning
├── profiles.md                 # Model profiles
├── configuration.md            # Configuration reference
```

## Status & Version

- **Version**: `0.1.0-beta.1` (`pyproject.toml`) — prereleases built automatically on every push to master
- **Stability**: Alpha-quality research software — see [README](../README.md#current-stability)
- **CI**: GitHub Actions runs on every push; gate = lint + typecheck + collect + test + smoke
- **Authoritative Status**: Run `make gate` and check `.gate-status` — stale numbers in docs are a bug

---

*Documentation generated from source. Last updated: 2026-06-30*
