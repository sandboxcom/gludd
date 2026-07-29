# Architecture Overview

## How It Works

General Ludd is an autonomous agentic SDLC daemon. The core flow:

```
User adds task → Daemon queue → Event Loop → Ansible Runner → general_ludd.agent collection → AI Model → Result
```

## Components

### Daemon (FastAPI)

The daemon is a FastAPI application that exposes:
- **REST API** for task management (`/api/todos`, `/api/status`)
- **Facts API** (`GET /api/facts`) — live structured snapshot for playbook logic
- **Message queue API** (`/api/messages`) — inter-agent coordination
- **Observability APIs** (`/api/metrics`, `/api/traces`) — metrics and execution traces
- **Admin API** for runtime configuration (`/admin/*`)
- **Event loop** running as an async background task

PSK (pre-shared key) authentication is applied by middleware to all non-public paths.

The daemon runs as a single process, single gunicorn worker. SQLite-only: any non-SQLite
database URL is refused at startup. Multiple workers would race on the same SQLite file and
double-dispatch todos.

### Event Loop

The event loop is the core orchestrator. Every tick (default: 1 second), it:

1. **Claims** runnable tasks from the queue
2. **Dispatches** them to the Ansible runner with the appropriate model profile
3. **Monitors** running tasks for completion
4. **Reviews** completed task returns (optionally with a different model)
5. **Reconciles** decisions — approve, retry, or reject

### Facts and Message-Queue Backbone

Two endpoints form the backbone for Ansible coordination:

**`GET /api/facts`** aggregates the full daemon state into a single structured snapshot:
- `gludd.work` — in-flight/claimed task status by state
- `gludd.todos` — queue counts, oldest age, backlog size
- `gludd.models` — configured model routing + per-model usage/health
- `gludd.history` — task return success/failure rates
- `gludd.messages` — unread message counts per recipient
- `gludd.metrics` — agent-level metrics, global model usage, per-project cost, benchmark rankings
- `gludd.traces` — recent execution traces with per-phase aggregates

The `gludd_facts` module injects this snapshot as `ansible_facts.gludd` so roles can branch
on live data in `when:` and `vars:` without coupling to the HTTP layer.

Database-backed facts are best-effort during degraded dependency startup. If a configured
session factory cannot connect, `/api/facts` still returns HTTP 200 JSON: `work`, `todos`,
`history`, and `messages` are empty mappings while independent in-process facets remain
available. `GET` and `POST /api/todos` use the daemon's bounded in-memory queue for the
same connectivity failures. Those degraded-mode entries are process-local and ephemeral;
they are not reconciled into the database automatically, so callers requiring durability
must retry after database health is restored.
This avoids the opaque 500 behavior reported by FastAPI users in
[issue #775](https://github.com/fastapi/fastapi/issues/775) and addresses the long-lived
operator concern around database health and lifespan state discussed in
[Starlette discussion #2067](https://github.com/encode/starlette/discussions/2067).

**`/api/messages`** is the inter-agent coordination queue:
- `POST /api/messages` — send a message to a recipient or to `broadcast`
- `GET /api/messages?recipient=X` — inbox (includes broadcast)
- `POST /api/messages/{id}/ack` — mark a message read

The `gludd_message` module wraps this queue for use inside playbooks.

### Observability

- **Metrics** (`GET /api/metrics`): agent-level report, global model usage, per-project cost,
  benchmark rankings via `MetricsCollector` and `BenchmarkRepository`. Also exposed as
  `gludd.metrics` in the facts snapshot and via the `gludd_metrics` module.
- **Traces** (`GET /api/traces`): genuinely-captured in-process execution traces via
  `RecentTracesBuffer`; OTel exporter status (OTLP bridge if configured, otherwise disabled).
  Also exposed as `gludd.traces` in the facts snapshot and via the `gludd_traces` module.

### Ansible Collection: `general_ludd.agent`

All task execution goes through the `general_ludd.agent` Ansible collection
(`collections/ansible_collections/general_ludd/agent/`).

#### Modules

| Module | Purpose |
|---|---|
| `gludd_ping` | Connectivity check |
| `gludd_facts` | Inject `GET /api/facts` as `ansible_facts.gludd` |
| `gludd_message` | Inter-agent message queue (send/receive/ack) |
| `gludd_skill` | Invoke a named skill |
| `gludd_mcp_tool` | Call an MCP tool |
| `gludd_git` | Git operations |
| `gludd_worktree` | Git worktree management |
| `gludd_db` | Direct SQLite record access |
| `gludd_model_call` | Raw model call with token/cost accounting |
| `gludd_agent_run` | Spawn a sub-agent run |
| `gludd_metrics` | Focused read from `GET /api/metrics` |
| `gludd_traces` | Focused read from `GET /api/traces` |

The real module count: `make collection-modules`

#### Roles by Family

Roles compose modules into complete agent task runs. Grouped by family:

**Code-task roles** — core SDLC actions:
`agent_task`, `debug_failure`, `dependency_update`, `document_change`, `implement_change`,
`refactor_code`, `triage_issue`, `write_tests`

**Audit/report roles** — quality and visibility:
`audit_dependencies`, `audit_security`, `report_audit`, `report_metrics`, `report_status`

**Workflow-pipeline roles** — CI/CD orchestration:
`gate_triage`, `ci_pipeline_repair`, `flaky_quarantine`, `release_build`, `validate_and_push`

**Secure-SDLC roles** — supply-chain and security assurance:
`threat_model`, `security_review`, `secret_scan`, `sbom_generate`, `supply_chain_verify`,
`security_requirements`, `security_gate`

**Agile/sprint roles** — backlog and sprint lifecycle:
`story_create`, `estimate_story`, `backlog_groom`, `sprint_plan`, `standup_report`,
`sprint_board_report`, `velocity_report`, `sprint_review`, `retrospective`

The real role count: `make collection-roles`

### Model Router

The model router selects which AI model to use based on:
- **Role** — coder tasks use one model, reviewer tasks another
- **Quality** — high-quality tasks get the best model
- **Latency** — fast tasks get the quickest model
- **Pattern** — specific work patterns (code generation, review, planning, etc.)

Supported providers: Z.AI (GLM), OpenAI, Anthropic Claude, OpenRouter, vLLM (local),
llama.cpp (local). All use the OpenAI-compatible API surface. API keys are resolved from
OpenBao or environment variables — never stored in profile YAML files.

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

SQLite with Alembic migrations. Key tables:
- `projects` — Project definitions
- `todos` — Task queue
- `task_returns` — Task results
- `variable_namespaces` — Per-project Ansible variable scopes
- `agent_messages` — Inter-agent message queue

## Testing: Molecule Mock-Daemon Harness

Every collection module and role has a molecule scenario under `molecule/playbooks/`. Each
scenario uses the `default` (localhost) driver: `prepare.yml` starts a lightweight stdlib
mock daemon that serves the same JSON structure as the real API. No container runtime is
required; the harness runs on plain GitHub runners.

```bash
make molecule-test SCENARIO=<name>   # single scenario
make molecule-test-all               # all scenarios (CI-equivalent)
make molecule-scenarios              # list all scenarios
```

A floor (`MIN_MOLECULE_SCENARIOS` in `preflight.py`) ensures scenarios only grow. The gate
will fail if scenarios are removed.

## Configuration Layers

### Config directory discovery

The daemon locates its config directory in this order:

```
$GLUDD_CONFIG_DIR  →  ~/.config/general-ludd  →  /etc/general-ludd
```

**The repo's own `config/` directory is NOT on that path** — it holds examples to copy.

If no config directory is found, `load_model_profiles()` returns `[]`, `model_gateway`
stays `None`, and the dispatcher **silently falls back to a no-op executor**: dispatched
agents return `status="completed"` with empty output and no warning logged, while
`/healthz` and `/readyz` still report 200/ready. This is the most common cause of "the
daemon runs but agents do nothing" — set `GLUDD_CONFIG_DIR` when running from a checkout.

### Value layering (within the discovered directory)

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

- PSK-authenticated API (all `/api/*` and `/admin/*` paths)
- Systemd hardening: `NoNewPrivileges`, `ProtectSystem`, `PrivateTmp`
- Dedicated service user (not root)
- Secrets via OpenBao (HashiCorp Vault compatible); API keys never in YAML files
- Process isolation for Ansible execution (optional container runtime)
- OPA policy engine for configuration validation
- SAST scanning (Bandit), SBOM generation (CycloneDX), dependency audit (`pip-audit`)
- `detect-secrets` pre-commit hook; `make scan-secrets` at any time

## CI Pipeline

GitHub Actions (`.github/workflows/build.yml`):

1. **gate** — lint + typecheck + collect + test + smoke, Python 3.11 and 3.12 (`fail-fast: false`)
2. **molecule** — all molecule scenarios after gate passes
3. **linux / macos / windows** — PyInstaller binary + tarball, timestamped alpha version on push;
   stable version on tag (`v*`)
4. **release** (tag builds only) — publishes the GitHub Release, then runs
   `scripts/verify_release_completeness.py` as a **blocking** final step: 12 artifact
   categories, the prerelease-flag-vs-tag-shape rule, version-stamped asset names, and no
   zero-size assets. An incomplete release fails the workflow.

A cold tag-triggered matrix build takes **30–60 minutes**. See
[RELEASE_RUNBOOK.md](RELEASE_RUNBOOK.md) for the operator procedure.

CI is currently being stabilized — consult the Actions tab for real current status.

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
- Facts snapshot: `GET /api/facts` (work/todos/models/history/messages/metrics/traces)
- Metrics: `GET /api/metrics` (agent counts, cost tracking, benchmark rankings)
- Traces: `GET /api/traces` (recent execution traces, phase aggregates)
- Log level: Adjustable at runtime via `POST /admin/log-level`
