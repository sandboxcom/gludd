# Architecture Overview

## How It Works

General Ludd Agent is a task-driven autonomous coding system. Here's the flow:

```
User adds task → Daemon queue → Event Loop → Agent → AI Model → Ansible → Result
```

## Components

### Daemon (FastAPI)

The daemon is a FastAPI application that exposes:
- **REST API** for task management (`/api/todos`, `/api/status`)
- **Admin API** for runtime configuration (`/admin/*`)
- **Event loop** running as an async background task

The daemon runs as a single process — no external worker processes or message brokers.

### Event Loop

The event loop is the core orchestrator. Every tick (default: 1 second), it:

1. **Claims** runnable tasks from the queue
2. **Dispatches** them to agents with the appropriate model profile
3. **Monitors** running tasks for completion
4. **Reviews** completed task returns
5. **Reconciles** decisions (approve/retry/reject)

### Agents

Agents are AI-powered workers with specific roles:

| Agent | Role | Permissions |
|-------|------|-------------|
| `build` | Primary coder | Full read/write/execute |
| `plan` | Planning and analysis | Read-only, can dispatch explore |
| `explore` | Codebase search | Read-only |
| `general` | Multi-purpose | Full read/write/execute |

### Model Router

The model router selects which AI model to use based on:
- **Role** — coder tasks use one model, reviewer tasks another
- **Quality** — high-quality tasks get the best model
- **Latency** — fast tasks get the quickest model
- **Pattern** — specific work patterns (code generation, review, etc.)

### Ansible Runner

Task execution happens through Ansible playbooks. The system includes:
- Core Ansible runner using `ansible-core` as a library
- Process isolation via containers (optional)
- Variable templating and Jinja2 support

### Project Isolation

Multiple projects can run simultaneously with full isolation:

| Layer | Isolation Mechanism |
|-------|-------------------|
| Database | `project_id` FK on all models, scoped queries |
| Filesystem | Per-project workspace directories |
| Secrets | Scoped OpenBao paths (`projects/{id}/{path}`) |
| Logging | `[project-id]` prefix on all log messages |
| Metrics | Per-project cost and usage tracking |

### Hot Reload

Configuration changes are picked up without restarting:
- **Event bus** for inter-component notifications
- **Hook system** for extensible event handling
- **Worker broadcaster** to propagate changes to agents

## Data Model

### Task States

```
pending → in_progress → completed → reviewed
                                        ↓
                                   reconciled
```

| State | Meaning |
|-------|---------|
| `pending` | In the queue, waiting for an agent |
| `in_progress` | An agent is working on it |
| `completed` | Agent finished, needs review |
| `reviewed` | Reviewed and ready for reconciliation |
| `reconciled` | Final state — approved, retried, or rejected |

### Database

PostgreSQL with Alembic migrations. Key tables:
- `projects` — Project definitions
- `todos` — Task queue
- `task_returns` — Task results
- `variable_namespaces` — Per-project Ansible variable scopes

## Configuration Layers

```
Environment Variables (highest priority)
    ↓
~/.config/general-ludd/user.yml
    ↓
.general-ludd/agent_config.yml (per-project)
    ↓
/etc/general-ludd/general-ludd.yml
    ↓
Built-in defaults (lowest priority)
```

## Security

- Systemd hardening: `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`
- Dedicated service user (not root)
- Secrets via OpenBao (HashiCorp Vault compatible)
- Process isolation for Ansible execution
- OPA policy engine for configuration validation
- SAST scanning (Bandit) and SBOM generation

## Deployment Modes

| Mode | Description |
|------|-------------|
| Native (uv) | Run directly with `uv` package manager |
| Native (pip) | Traditional pip + venv |
| Container | Podman/Docker with systemd |
| Tarball | PyInstaller binary + systemd unit |

## Monitoring

- Health endpoint: `GET /healthz`
- Status endpoint: `GET /api/status`
- Metrics: Agent counts, cost tracking, per-project breakdowns
- Log level: Adjustable at runtime via `POST /admin/log-level`
