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

## Key Links

- [README.md](../README.md) — Project overview, quick start, architecture diagram
- [CONTRIBUTING.md](../CONTRIBUTING.md) — Contribution guidelines
- [LICENSE](../LICENSE) — MIT License
- [GitHub Repository](https://github.com/sandboxcom/gludd)
- [Issue Tracker](https://github.com/sandboxcom/gludd/issues)

## Documentation Structure

```
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

- **Version**: `v0.1.0-alpha` (pr`alpha`5)` — prereleases built automatically on every push to master
- **Stability**: Alpha-quality research software — see [README](../README.md#current-stability)
- **CI**: GitHub Actions runs on every push; gate = lint + typecheck + collect + test + smoke
- **Authoritative Status**: Run `make gate` and check `.gate-status` — stale numbers in docs are a bug

---

*Documentation generated from source. Last updated: 2026-06-30*