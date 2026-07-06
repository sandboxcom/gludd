# General Ludd Agent

The black swan agentic coding system — an autonomous, Ansible-driven, multi-model AI agent
that submits coding tasks and produces real, committed, reviewed, and reconciled code changes.

## 📊 [Interactive Presentation](https://sandboxcom.github.io/gludd/)

## What Is This?

General Ludd (`gludd`) is an **autonomous agentic SDLC daemon** (FastAPI). You submit a
todo — "add end-to-end encryption to the API," "fix the race condition in the job queue,"
"upgrade all dependencies and run the test suite" — and the system dispatches it to an AI
model, runs the generated code through a validation pipeline (tests, lint, typecheck, quality
gates), reviews the result with a separate model, and lands the change in git.

It is not a chatbot or a copilot. It is a daemon with an event loop:
**claim → dispatch → review → reconcile → repeat**.

The execution layer is **Ansible**: every task the daemon runs is an Ansible playbook that
composes modules from the `general_ludd.agent` collection. This means tasks are auditable,
idempotent, and can fan out to subagents via the same API.

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

This project is **alpha-quality research software**. The daemon boots, the event loop
ticks, the database layer works, and the model gateway can call real APIs. But many
subsystems are wired but not fully exercised end-to-end. **Do not run this in production
without understanding the failure modes.** Expect rough edges around Ansible playbook
execution, multi-model failover, and project workspace management.

**CI note:** GitHub Actions runs on every push to master. The gate (`lint`, `typecheck`,
`collect`, `test`, `smoke`) runs against Python 3.11 and 3.12 with `fail-fast: false` so
both matrix legs report. The molecule scenario suite runs as a separate job after gate.
CI is being stabilized — consult the Actions tab for the real current status rather than
relying on any static claim here.

### Measured status (single source of truth)

This README intentionally does **not** hardcode test counts, mypy error totals, or
coverage percentages — stale numbers in docs were a recurring source of false "done"
claims. The live, authoritative status is the gate:

```bash
make gate            # lint + typecheck + collect + test + smoke; writes .gate-status
cat .gate-status     # the single source of truth for current counts
make test-count      # collected-test count, 0 collection errors required
make typecheck       # current mypy error count (gate enforces ≤ MYPY_MAX, see Makefile)
```

Known-failing tests are tracked as strict xfail entries in `config/ratchet.yml` (the file
may only shrink). The gate passes only when `make test` exits 0.

Version: `v0.1.0-beta.1` — prereleases are built automatically on every push to master and
published as GitHub Releases with timestamped artifacts for Linux (x86_64), macOS (arm64),
and Windows (x86_64).

---

## Feature & Task Completion Status

**Backlog items only — completed features are in the CHANGELOG**

The table below is **code-generated** from [`docs/features.yml`](docs/features.yml) by
[`scripts/gen_status_table.py`](scripts/gen_status_table.py): every row's verified status is
derived by running each feature's evidence references through the fail-closed
`FeatureVerifier` (test/file/role/module/molecule refs). It refreshes on every build/deploy
and can be regenerated on demand:

```bash
make gen-status-table     # regenerate the table from docs/features.yml (writes between markers)
make check-status-table   # CI/release gate: fail if the on-disk table is stale
```

The table is regenerated/verified on every release cut (enforced by `make release-cut`) and
in CI (the `gate` job runs `make check-status-table`). Do NOT hand-edit the rows between the
`STATUS-TABLE` markers — edit `docs/features.yml` and regenerate.

Honesty note: this project has a documented history of false "done" claims (see `BUGS.md`).
The status line above states only machine-verified facts (test counts, gate results, CI
verdicts) — no percentage-of-completion claims. The ✓/~/✗ badge in the "Verified %" column
below is the **machine** verdict from `FeatureVerifier` (✓ all evidence met, ~ partial,
✗ none met / no evidence — fail-closed). "Local-only" means the gate passes on macOS arm64
but CI (ubuntu) is unverified. No row claims "100% complete" — completeness is only
established by passing CI + verified artifact.

Evidence key: `[commit]` = 7-char SHA in `TASKS.md`, `[test]` = named test file or class,
`[audit]` = `docs/audit/` findings.

<!-- STATUS-TABLE:START -->
*(auto-generated with `--fast`; `test:` refs checked by file existence only — run `make gen-status-table` locally to verify tests pass)*


### Security Hardening

| Feature / Task | Verified % | Evidence |
|---|---|---|
| D-04/D-05/D-06/D-29/D-30/D-31 security items (batch-4 branch) | ✓ 0% | **PASS** *(file-refs only)*: ABANDONED: branch feature/security-batch4 superseded; all items independently implemented in master |

### Orchestration / Agents

| Feature / Task | Verified % | Evidence |
|---|---|---|
| Watchdog/stall detection improvements (mt-6-watchdog branch) | ✗ 0% | **PENDING**: Abandoned branch; code rescoped into master. Original branch deleted. Feature reclassified to reflect actual implementation status. |

<!-- STATUS-TABLE:END -->
## Presentation

> **Status: not yet implemented.** The `make deck`, `make deck-data`, and
> `make deck-serve` targets referenced below are specced in
> `docs/presentation/BUILD_TASK_LIST.md` and `DESIGN_revealjs_deck.md` but are
> NOT defined in the `Makefile`. The `docs/presentation/deck/` source tree and
> `scripts/build_deck.py` are also not yet committed. Do not invoke these
> targets; they will fail with "No rule to make target".

A self-describing reveal.js deck — "gludd, honestly" — is planned to be
generated from live E2E artifacts and committed design templates. Every
maturity claim on a slide is intended to carry the same evidence token the
README table carries; missing data would render an honest "NO DATA — run
`make deck-data`" placeholder rather than a fabricated screenshot.

**Planned URL:** https://sandboxcom.github.io/gludd/

> Once implemented, the link goes live when:
> 1. GitHub Pages is enabled in repo settings (Source: GitHub Actions)
> 2. The deck source (`docs/presentation/deck/`) is committed to `main`
> 3. The `.github/workflows/pages.yml` workflow has run successfully
>
> Until the targets exist and the above conditions are met, there is no local
> or published deck to preview.

Design: `docs/presentation/DESIGN_revealjs_deck.md` | Build task list: `docs/presentation/BUILD_TASK_LIST.md`

---

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

### Dogfood the Repo

The daemon can run on its own codebase:

```bash
make dogfood     # runs the event loop on the gludd repo itself
```

## Architecture

```
                     ┌─────────────┐
  User ──CLI/TUI──▶  │   Daemon    │  (FastAPI + Gunicorn, single worker)
                     │  :8000      │  PSK-authenticated API
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
  Claim   Dispatch  Review   Reconcile  Self-Improve
             │
        ┌────▼────┐
        │ Ansible │  (general_ludd.agent collection)
        │ Runner  │
        └────┬────┘
             │
    ┌────────┼────────────────────┐
    ▼        ▼                    ▼
  gludd_*  Roles               Model
  modules  (~34)               Gateway
             │                    │
             ▼                    ▼
      ┌─────────────────────────────────┐
      │     SQLite (single-worker)      │
      │  todos · returns · benchmarks   │
      │  messages · metrics · traces    │
      └─────────────────────────────────┘
```

### Event Loop

Every tick (default: 1 second), the event loop:

1. **Claims** runnable tasks from the queue
2. **Dispatches** them via the Ansible runner with the appropriate model profile
3. **Reviews** completed task returns with a (potentially different) model
4. **Reconciles** decisions — approve, retry, or reject

### API

The daemon exposes a PSK-authenticated REST API. Key endpoints:

| Endpoint | Description |
|---|---|
| `GET /api/facts` | Live daemon snapshot: work/todos/models/history/messages/metrics/traces as Ansible dynamic facts |
| `GET /api/metrics` | Agent-level metrics, global model usage, per-project cost, benchmark rankings |
| `GET /api/traces` | Recent execution traces with per-phase aggregates |
| `POST /api/messages` | Inter-agent message queue: send a message |
| `GET /api/messages` | Inbox for a recipient (supports broadcast) |
| `POST /api/messages/{id}/ack` | Acknowledge a message as read |
| `GET /api/todos` | Task queue management |
| `GET /healthz` | Health check |
| `GET /admin/metrics/export` | Metrics export |

`GET /api/facts` is the backbone endpoint: it aggregates the full daemon state into a single
structured dict that `gludd_facts` injects as `ansible_facts.gludd` so playbook `when:` and
`vars:` conditions can branch on live data without coupling roles to the HTTP layer.

### Database & Concurrency (SQLite Only)

gludd is **SQLite only**. Schema creation and Alembic migrations are SQLite-specific; any
non-SQLite database URL is refused at startup rather than booting into a half-broken state.

Because there is no cross-process claim coordination over a single SQLite file, the daemon
runs a **single gunicorn worker**. `--workers` defaults to 1, and any `--workers N` with
`N > 1` is clamped to 1 with a warning.

### Multi-Model Routing

The model router selects which AI model to use based on role, quality requirement, latency
budget, or work pattern. The shipped config routes to `zai_coder` (Z.AI GLM) with a fallback
chain to `deepseek_coder` and `qwen_coder`. Supported providers: Z.AI, OpenAI, Anthropic
Claude, OpenRouter, vLLM (local), llama.cpp (local). API keys are resolved from OpenBao or
environment variables — never stored in profile YAML files.

## The `general_ludd.agent` Ansible Collection

All task execution happens through the `general_ludd.agent` Ansible collection. Install it
via the collection path (`collections/ansible_collections/general_ludd/agent/`).

### Modules

| Module | Purpose |
|---|---|
| `gludd_ping` | Connectivity check against the daemon |
| `gludd_facts` | Inject `GET /api/facts` as `ansible_facts.gludd` (work/todos/models/history/messages/metrics/traces) |
| `gludd_message` | Inter-agent message queue — send, receive, ack |
| `gludd_skill` | Invoke a named skill on the daemon |
| `gludd_mcp_tool` | Call an MCP (Model Context Protocol) tool |
| `gludd_git` | Git operations (commit, branch, push, diff) |
| `gludd_worktree` | Git worktree management |
| `gludd_db` | Direct SQLite record access |
| `gludd_model_call` | Raw model call with token/cost accounting |
| `gludd_agent_run` | Spawn a sub-agent run |
| `gludd_metrics` | Focused read from `GET /api/metrics` |
| `gludd_traces` | Focused read from `GET /api/traces` |

`gludd_facts` and `gludd_message` form the backbone: facts feeds live daemon state into
playbook logic; message provides the inter-agent coordination queue. `gludd_metrics` and
`gludd_traces` expose observability data as Ansible dynamic facts for playbooks that need
to branch on telemetry.

### Roles

Roles compose modules into full agent task runs. They are grouped by family:

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

The actual count can be verified with: `make collection-roles`

## Testing

### Unit and Integration Tests

```bash
make test              # full suite with coverage
make test-unit         # unit tests only (fast)
make test-integration  # integration tests
make test-e2e          # end-to-end tests
make test-count        # check collection (0 errors required)
```

### Molecule Harness

Every collection module and role has a molecule scenario under `molecule/playbooks/`. Each
scenario spins up a lightweight stdlib mock daemon (`prepare.yml`), runs the role/module
against it, then verifies results — no real daemon or container runtime required.

```bash
make molecule-test SCENARIO=role_implement_change   # run one scenario
make molecule-test-all                              # run all scenarios (CI-equivalent)
make molecule-scenarios                             # list all scenarios
```

The minimum scenario count is enforced by `preflight.py` (`MIN_MOLECULE_SCENARIOS`);
the gate will fail if scenarios are removed. The real count: `make molecule-scenarios | wc -l`.

### Gate and Preflight

```bash
make gate              # lint + typecheck + collect + test + smoke; writes .gate-status
make preflight         # preflight quality gate (coverage, lint, mypy, templates, molecule, etc.)
make validate          # gate + ansible syntax + healthcheck
```

## Development

### Code Quality

```bash
make lint              # ruff (0 errors required)
make typecheck         # mypy (gate enforces ≤ MYPY_MAX; see Makefile)
make gate              # full gate
make validate          # full validation including ansible syntax
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

## Example Configurations

### Minimal Config (`~/.config/general-ludd/general-ludd.yml`)

```yaml
model_routing:
  default_profile: zai_coder

# Database defaults to SQLite (~/.local/share/general-ludd/gludd.db).
# If you set a url it MUST be a sqlite+aiosqlite:/// URL — postgres is refused.

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
- [`config/model_profiles/zai_example.yml`](config/model_profiles/zai_example.yml) — Z.AI GLM (primary coder)
- [`config/model_profiles/deepseek_coder.yml`](config/model_profiles/deepseek_coder.yml) — DeepSeek fallback
- [`config/model_profiles/qwen_coder.yml`](config/model_profiles/qwen_coder.yml) — Qwen fallback
- [`config/model_profiles/openai_example.yml`](config/model_profiles/openai_example.yml) — OpenAI GPT-4
- [`config/model_profiles/anthropic_example.yml`](config/model_profiles/anthropic_example.yml) — Claude

### Model Routing (`config/model_routing.yml`)

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

```bash
mkdir -p ~/.config/general-ludd/openbao
cp config/openbao/default.yml ~/.config/general-ludd/openbao/default.yml
```

OpenBao supports three modes:
- **external**: Connect to an existing OpenBao or HashiCorp Vault instance
- **auto**: Try external first, fall back to environment variables
- **disabled**: Use environment variables only

On macOS, the daemon automatically prefers Docker over Podman for container-based
OpenBao (Docker Desktop handles port forwarding transparently on macOS).

## Service Login

`gludd login <service>` opens your browser for XDG-compliant OAuth2 / API-key login.

### Quick Start

```bash
gludd login --list                    # show available services
gludd login openai                   # paste your API key
gludd login github                   # OAuth2 + PKCE flow (requires OAuth app)
```

### Supported Services

| Service    | Command              | Auth method   | Credential env var      |
|------------|---------------------|---------------|--------------------------|
| OpenAI     | `gludd login openai`    | API key       | `OPENAI_API_KEY`          |
| DeepSeek   | `gludd login deepseek`  | API key       | `DEEPSEEK_API_KEY`        |
| Z.AI       | `gludd login zai`       | API key       | `ZAI_API_KEY`             |
| Anthropic  | `gludd login anthropic` | API key       | `ANTHROPIC_API_KEY`       |
| OpenRouter | `gludd login openrouter`| API key       | `OPENROUTER_API_KEY`      |
| GitHub     | `gludd login github`    | OAuth2 + PKCE | `GITHUB_TOKEN`            |
| Google     | `gludd login gemini`    | OAuth2 + PKCE | `GOOGLE_API_KEY`          |

Credentials are stored in `~/.config/gludd/credentials.env` (permissions 600).
Use `--store openbao` to store in OpenBao instead.

### Non-automatable Setup (OAuth2 services only)

OAuth2 services (GitHub, Google Gemini) require a one-time **OAuth application registration**
before the browser flow can complete. The daemon cannot create these for you:

#### GitHub

1. Go to [GitHub → Settings → Developer settings → OAuth Apps](https://github.com/settings/developers)
2. Click **New OAuth App**
3. Set **Authorization callback URL** to `http://localhost:<random-port>/callback`
   (the port is random — this is fine; the client constructs it at login time)
4. Export the client ID and secret:
   ```bash
   export GITHUB_OAUTH_CLIENT_ID="Iv23li..."
   export GITHUB_OAUTH_CLIENT_SECRET="secret..."
   ```
5. Run `gludd login github`

#### Google Gemini

1. Go to [Google Cloud Console → APIs & Services → Credentials](https://console.cloud.google.com/apis/credentials)
2. Create an **OAuth 2.0 Client ID** (Web application type)
3. Add `http://localhost` to **Authorized redirect URIs**
4. Export:
   ```bash
   export GOOGLE_OAUTH_CLIENT_ID="..."
   export GOOGLE_OAUTH_CLIENT_SECRET="..."
   ```
5. Run `gludd login gemini`

### Ansible Role

The `general_ludd.agent.service_login` role automates login in playbooks:

```yaml
- hosts: localhost
  roles:
    - role: general_ludd.agent.service_login
      vars:
        login_service: github
        login_store: env
```

Non-interactive mode (CI / headless):
```yaml
login_non_interactive: true
login_api_key: "{{ lookup('env', 'GITHUB_TOKEN') }}"
```

### Architecture

1. Generates PKCE `code_verifier` + `code_challenge` (S256)
2. Opens default browser via `xdg-open` (Linux) or `open` (macOS)
3. Starts local HTTP server on `127.0.0.1:<random-port>` for OAuth callback
4. Exchanges authorization code for tokens at the service's token endpoint
5. Stores credential: `env` → `~/.config/gludd/credentials.env`; `openbao` → `secret/gludd/auth/<service>`

The redirect server binds **only** on loopback (127.0.0.1) — it is never reachable off-host.

## Contributing

Pull requests are welcome. Please follow these guidelines:

### PR Requirements

1. **Branch from master** — create a feature branch for your work.
   ```bash
   make feature-start MSG='feature/my-change'
   ```

2. **Keep your commits** — do not squash or flatten your PR branch. Each commit
   should represent one logical change. The merge to master will use `--no-ff` to
   preserve the branch topology.

3. **Include your prompts** — every PR description must include the full prompt(s)
   used to generate or guide the code change. If you used an AI coding agent
   (General Ludd itself, opencode, Copilot, etc.), paste the exact prompts you
   gave it in the PR body under a `## Prompts Used` heading.

4. **Gate must be green** — run `make gate` before opening the PR. The `.gate-status`
   file is the single source of truth.

5. **TDD** — new behavior must have a failing test committed before the implementation.
   The test file and the implementation should be separate commits on the branch.

### Commit Style

- One logical change per commit (one test file, one feature, one fix)
- Messages are imperative: `Add`, `Fix`, `Remove`, `Update`
- Reference issue numbers when applicable

### Before Opening a PR

```bash
make gate           # must be green
make validate       # full validation including ansible syntax
make lint           # 0 errors
make test-count     # 0 collection errors
```

## Configuration Reference

| File | Purpose |
|------|---------|
| [`config/general-ludd.yml`](config/general-ludd.yml) | Main configuration (model routing, database, agents, budget) |
| [`config/model_routing.yml`](config/model_routing.yml) | Model routing with fallback chains |
| [`config/model_profiles/zai_example.yml`](config/model_profiles/zai_example.yml) | Z.AI GLM profile |
| [`config/model_profiles/deepseek_coder.yml`](config/model_profiles/deepseek_coder.yml) | DeepSeek profile |
| [`config/model_profiles/qwen_coder.yml`](config/model_profiles/qwen_coder.yml) | Qwen profile |
| [`config/openbao/default.yml`](config/openbao/default.yml) | OpenBao secrets backend |
| [`config/ansible/isolation.yml`](config/ansible/isolation.yml) | Process isolation settings |
| [`config/mcp_servers/example.yml`](config/mcp_servers/example.yml) | MCP server connections |
| [`config/binary_paths.yml`](config/binary_paths.yml) | External binary paths |

## License

MIT
