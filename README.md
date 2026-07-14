# General Ludd Agent

The black swan agentic coding system — an autonomous, Ansible-driven, multi-model AI agent
that submits coding tasks and produces real, committed, reviewed, and reconciled code changes.

## [Interactive Presentation](https://sandboxcom.github.io/gludd/) &middot; [local fallback](docs/presentation/deck/index.html)

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

**Status as of v0.1.0-beta.4 — 2026-07-14**

Version: `v0.1.0-beta.4` — release binaries (Linux x86_64, macOS arm64, Windows x86_64, and
more) are built as CI artifacts on every push to master, but a GitHub Release is only cut
when a `v*` tag is pushed (the `release` job in `.github/workflows/build.yml` is gated on
`startsWith(github.ref, 'refs/tags/v')`).

---

## Feature & Task Completion Status

<!-- STATUS-TABLE:START -->

### Overall
261 items | 99.6% complete | 1 pending (L.3 SearX gateway wiring)

### Core Infrastructure

| Subsystem | Status | Detail |
|---|---|---|
| Enforcement plugins | 13/13 BLOCKING | 100+ hook-runtime tests pass |
| Terraform | 18 stacks complete | HTTP state backend, workload-aware deployment profiles |
| Ansible collections | 4 core collections | agent, security, business, networking |
| Model deployment | SearX-based model search | WorkloadType profiles, ansible infra deploy |

### Testing & Quality

| Metric | Value |
|---|---|
| Tests collected | 38,207 |
| Collection errors | 0 |
| Secrets e2e tests | 108 |
| No secrets leaked outside Vault | Confirmed |

### Development

| Metric | Value |
|---|---|
| Commits ahead of master | 224+ |

<!-- STATUS-TABLE:END -->

## Backlog

Completed features are documented in CHANGELOG.md. Only in-progress items are tracked here.

| Item | Status |
|---|---|
| CI pipeline green | 0% — all recent runs failed |
| 6 game mechanics checks | 50% — 6/12 games fully verified |
| Type annotations (no Any) | 76% — 420/1770 return types still missing |
| Reveal.js presentation | 100% — deck built, deployed to GitHub Pages |
| Account lifecycle | 90% — ephemeral accounts implemented, needs e2e test |
## Presentation

**Status: built, source tracked, Pages workflow wired.** The interactive reveal.js
deck lives at [`docs/presentation/deck/index.html`](docs/presentation/deck/index.html).
The tracked file is a **template**: it carries `{{VERSION}}`/`{{TEST_COUNT}}`/
`{{ROLE_COUNT}}`/`{{GIT_SHA}}`/`{{GENERATED_AT}}` placeholders, so opening it
directly in a browser (or on GitHub) shows those literal `{{TOKEN}}` strings
instead of live numbers. Run `make deck-serve` to see the deck with real,
current metrics: it resolves the tokens into a throwaway scratch copy and
serves that, leaving the tracked template byte-for-byte untouched. The
published Pages URL below is built the same way (via `make deck-build` in CI),
so it always shows resolved numbers.

**Live URL (GitHub Pages):** [https://sandboxcom.github.io/gludd/](https://sandboxcom.github.io/gludd/)
&mdash; deployed by [`.github/workflows/pages.yml`](.github/workflows/pages.yml) on every push to
`master` that touches the deck source. Pages was just enabled for this repo; the
URL goes live once that workflow completes its next successful run &mdash; check
the Actions tab for current deploy status rather than assuming this link resolves.

The deck is a 28-slide presentation that opens with a plain-English introduction
(with analogies) and then goes deep. It covers:

- What gludd is, in plain English, and the problem it solves
- The flagship flow &mdash; todo submitted → AI implements → reviewed → committed
  &mdash; as 9 stages, each citing the exact function and file that implements it
  (`routers/todos.py`, `event_loop/loop.py`, `models/gateway.py`,
  `review/reviewer.py`, `git_automation/repo.py`, and more)
- A sequence diagram of the adversarial review loop (JSON-fence-tolerant
  parsing, fail-closed on unparseable output)
- The database: all 27 tables in `db/models.py`, with an ER-style diagram and
  reference tables giving the exact line number and purpose of each
- Security (three-layer model)
- Current stats with citations (tests, source files, Ansible roles/modules, DB tables, model providers, enforcement plugins)
- What gludd can do today, and what it can't do yet (honest gaps)
- How to try it, roadmap, and honest metrics

Every stat on every slide carries a citation (e.g., `make test-count`,
`make collection-roles`) so the audience can verify the numbers themselves.
Inline Mermaid diagrams (rendered via the reveal.js mermaid plugin) illustrate
the architecture, work cycle, and security layers — no binary image artifacts.

To preview locally:

```bash
# Resolved preview (recommended) — real numbers, tracked template untouched
make deck-serve
# Raw template — opens directly, shows literal {{TOKEN}} placeholders
open docs/presentation/deck/index.html
```

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
  modules  (109)               Gateway
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
budget, or work pattern. The shipped config (`config/model_routing.yml`) routes to
`deepseek_coder` by default, with a fallback chain to `qwen_coder` then `zai_coder`.

Supported providers (alphabetical) — each is configured via its environment variable.
Keys are resolved from OpenBao or the environment; they are never stored in profile YAML.

| Provider | Env var |
|---|---|
| AI21 | `AI21_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Azure AI Foundry | `AZURE_AI_API_KEY` |
| Baseten | `BASETEN_API_KEY` |
| Cloudflare | `CLOUDFLARE_API_TOKEN` |
| Cohere | `CO_API_KEY` |
| CoreWeave | `COREWEAVE_API_KEY` |
| Databricks | `DATABRICKS_TOKEN` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Fireworks AI | `FIREWORKS_API_KEY` |
| Google | `GOOGLE_API_KEY` |
| Groq | `GROQ_API_KEY` |
| Hugging Face | `HF_TOKEN` |
| Lambda Labs | `LAMBDALABS_API_KEY` |
| Mistral AI | `MISTRAL_API_KEY` |
| Modal | `MODAL_API_TOKEN` |
| NVIDIA NIM | `NVIDIA_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Perplexity | `PERPLEXITY_API_KEY` |
| Replicate | `REPLICATE_API_TOKEN` |
| RunPod | `RUNPOD_API_KEY` |
| Together AI | `TOGETHER_API_KEY` |
| Z.AI / GLM | `ZAI_API_KEY` |

Most providers expose an OpenAI-compatible `/v1/chat/completions` endpoint and use the
`langchain-openai` adapter; Anthropic uses `langchain-anthropic` and Hugging Face uses
`langchain-huggingface`. Local backends (vLLM, llama.cpp) are also supported. Every
provider drops into the same model-routing pipeline.

#### Adding a New Provider

To add support for a new model or compute provider:

1. **Edit `src/general_ludd/models/provider_presets.py`** — add an entry to the
   `PROVIDER_PRESETS` dict with the provider's `api_base_url`, `provider_package`
   (typically `langchain-openai` for OpenAI-compatible endpoints), `provider_class`
   (typically `ChatOpenAI`), `credential_env_var`, and `display_name`.
2. **Set the env var** — export the credential (e.g. `export BASETEN_API_KEY=...`) or
   store it via `gludd login <provider>` / OpenBao so it is never committed to config YAML.
3. **Restart the daemon** — the new provider is loaded on boot from the presets file and
   becomes selectable in model routing and profile configuration.

## Ansible Collections

All task execution happens through the `general_ludd.*` Ansible collections. Collections
are split by domain: `agent` (core modules + general roles), `security` (offensive/defensive
security), `business` (entity intelligence), and `networking` (packet analysis + network ops).

### `general_ludd.agent` — Core Collection

Install via `collections/ansible_collections/general_ludd/agent/`. This is the base
collection that all others build on — modules, general-purpose roles, and the daemon
integration layer.

#### Modules (36 total — 12 core modules shown below)

The collection ships 36 modules total (`make collection-modules`); the table below covers
the 12 core ones — the full set lives in
`collections/ansible_collections/general_ludd/agent/plugins/modules/`.

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

#### Roles

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

### `general_ludd.security` — Security Collection

Install via `collections/ansible_collections/general_ludd/security/`. Six roles covering
the full security lifecycle: certificate management, hardware-backed key operations,
compliance auditing, and injection attack detection/remediation.

**FQCN prefix:** `general_ludd.security.`

| Role | Description |
|---|---|
| `ssl_cert` | SSL/TLS certificate lifecycle (mint, research, verify, compliance) |
| `hsm_operations` | HSM and smartcard operations (PKCS#11 sign, keygen, attest) |
| `audit_framework` | Compliance auditing against PCI-DSS, SOC2, NIST, FIPS |
| `sql_injection` | SQL injection detection and remediation (Python, Go, JS) |
| `command_injection` | Command injection detection in source, CI configs, IaC |
| `prompt_injection` | LLM prompt injection detection and mitigation strategies |

See `docs/SECURITY_ROLES.md` for the full reference with interoperability matrix,
SearX integration, tool awareness table, and sample audit flow.

### `general_ludd.business` — Business Intelligence Collection

Install via `collections/ansible_collections/general_ludd/business/`. Business-domain
roles for entity research and corporate intelligence.

**FQCN prefix:** `general_ludd.business.`

| Role | Description |
|---|---|
| `entity_research` | Full entity intelligence: discovery, associations, assets, exposure, risks, demographics |

All data collection requires explicit opt-in via category enable flags. Integrates with
OpenCorporates, SEC EDGAR, Crunchbase, Wikipedia, Shodan, Censys, and SearX. Supports
entity graph visualization via DOT export.

See `docs/BUSINESS_RESEARCH_SYSTEM.md` for the full reference.

### `general_ludd.networking` — Networking Collection

Install via `collections/ansible_collections/general_ludd/networking/`. Network
operations role with packet analysis, traffic inspection, and dissector development.

**FQCN prefix:** `general_ludd.networking.`

| Role | Description |
|---|---|
| `networking` | 7-mode networking: pcap read, packet craft, network scan, traffic analyze, dissector create, tool recommend, packet dissect |

Integrates with `ScapyAdapter` for packet-level operations, nmap for discovery,
and Wireshark Lua dissector templates for protocol analysis.

See `docs/NETWORKING_SYSTEM.md` for the full reference.

### `general_ludd.xml` — XML Collection

Install via `collections/ansible_collections/general_ludd/xml/`. Nine roles covering the
full XML document lifecycle: parsing, XPath querying, namespace handling, XSD schema
generation, XSLT transformations, and format-specific manipulation (HTML, SOAP, SAML,
DocBook, DITA, Gradle, plist).

**FQCN prefix:** `general_ludd.xml.`

| Role | Description |
|---|---|
| `xml_core` | XML parsing, XPath querying, namespace handling |
| `xsd_generator` | XSD schema generation from XML samples |
| `xslt_transformer` | XSLT transformations |
| `html_processor` | HTML parsing and manipulation |
| `soap_handler` | SOAP and XML-RPC messaging |
| `saml_processor` | SAML 2.0 assertion handling |
| `docbook_converter` | DocBook/DITA conversion |
| `gradle_parser` | Gradle build file parsing |
| `plist_parser` | Apple property list (plist) handling |

Includes a shared Python module `xml_utils.py` (16 functions) for common XML operations.
See `docs/XML_COLLECTION.md` for the full reference.

The actual role count can be verified with: `make collection-roles`

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
default_profile: deepseek_coder
weak_model_profile: deepseek_coder

fallback_chain:
  - qwen_coder
  - zai_coder

role_routing:
  coder: deepseek_coder
  planner: deepseek_coder
  reviewer: deepseek_coder
  fast: deepseek_coder

quality_routing:
  high: deepseek_coder
  medium: deepseek_coder

latency_routing:
  fast: deepseek_coder

pattern_routing:
  return_review: reviewer
  commit_message: weak
  gap_analysis: fast
  code_generation: coder
  planning: planner
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

`gludd login` currently wires 7 services (`SERVICE_PRESETS` in
`src/general_ludd/auth/browser_login.py`):

| Service     | Command               | Auth method   | Credential env var      |
|-------------|-----------------------|---------------|--------------------------|
| Anthropic   | `gludd login anthropic`  | API key       | `ANTHROPIC_API_KEY`       |
| DeepSeek    | `gludd login deepseek`   | API key       | `DEEPSEEK_API_KEY`        |
| GitHub      | `gludd login github`     | OAuth2 + PKCE | `GITHUB_TOKEN`            |
| Google      | `gludd login gemini`     | OAuth2 + PKCE | `GOOGLE_API_KEY`          |
| OpenAI      | `gludd login openai`     | API key       | `OPENAI_API_KEY`          |
| OpenRouter  | `gludd login openrouter` | API key       | `OPENROUTER_API_KEY`      |
| Z.AI        | `gludd login zai`        | API key       | `ZAI_API_KEY`             |

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

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

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

See [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) for the full reference. Quick index:

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
