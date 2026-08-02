# Configuration Reference (WP-F1)

**Project:** general-ludd-agent
**Version:** 0.1.0-beta.1 (`src/general_ludd/__init__.py`)
**Audience:** weaker-model AI executors and human operators. This doc is
self-sufficient — no other file is required to understand how to configure and
run the daemon end-to-end.

> This is the canonical single-source reference. `docs/quickstart.md` (fast path)
> and `docs/configuration.md` (narrative) cover the same ground from other
> angles; when they disagree, **this file is authoritative**.

---

## TL;DR — minimal path (3 commands)

```bash
make init                          # 1. install deps (uv sync) + create dirs
export ZAI_API_KEY=sk-...          # 2. configure ONE model provider
export GLUDD_CONFIG_DIR="$PWD/config"  # 3. REQUIRED from a repo checkout — see §2.0
gludd daemon                       # 4. start server (127.0.0.1:8000)
# in another shell:
gludd models router-status         # MUST list a profile — empty means step 3 was skipped
gludd add "Write a hello-world test" --work-type code
gludd status                       # watch the todo move to completed
```

That is the whole product spine. Everything below is detail.

> **Do not skip step 3.** The repo's `config/` directory is not on the config
> discovery path. Without `GLUDD_CONFIG_DIR`, no model profiles load and every
> agent silently "completes" with empty output. See §2.0.

---

## 1. Environment Variables

Env vars are the **highest-priority** config layer (override all YAML files).
All are optional unless marked **required**. Defaults are read from
`os.environ.get(...)` at the call sites cited.

### 1.1 `GLUDD_*` — daemon / runtime

| Name | Purpose | Default | Required | Source |
|---|---|---|---|---|
| `GLUDD_PSK` | Pre-shared key (Bearer token) for daemon↔CLI/worker auth. Auto-generated and printed when binding a non-loopback interface. | `""` (auth disabled) | optional¹ | `daemon.py:2369`, `cli.py:1151` |
| `GLUDD_REQUIRE_AUTH` | Force auth on. Truthy values: `1`,`true`,`yes`,`on`. When set without `GLUDD_PSK`, worker surface fails CLOSED (503). | `""` | optional | `daemon.py:2378` |
| `GLUDD_ALLOW_NO_AUTH` | Explicitly bypass auth (dev only). Truthy set as above. | `""` | optional | `daemon.py:2375` |
| `GLUDD_CONFIG_DIR` | Override the config directory. **Set this when running from a repo checkout** — the repo's own `config/` tree is NOT on the discovery path. See §2.0. | builtin | optional | `daemon.py:2313` |
| `GLUDD_TEMPLATES_DIR` | Override prompt-templates directory. | builtin | optional | `daemon.py:2315` |
| `GLUDD_PLAYBOOKS_DIR` | Override ansible playbooks directory. | builtin | optional | `daemon.py:2317` |
| `GLUDD_TICK_INTERVAL` | Event-loop tick interval in seconds. | `1.0` | optional | `daemon.py:2307` |
| `GLUDD_LOG_LEVEL` | Daemon log level: `debug`\|`info`\|`warning`\|`error`. | `info` | optional | `daemon.py:2309` |
| `GLUDD_WRITER_MODE` | DB writer path. **`inline` is the only working mode — do NOT set `subprocess`** (see §5, Experimental flags). | `inline` | optional | `daemon.py:898` |
| `GLUDD_DB_PATH` | SQLite database file path. | `$XDG_DATA_HOME/general-ludd/general-ludd.db` (→ `~/.local/share/general-ludd/general-ludd.db`) | optional | `db/session.py:25` |
| `GLUDD_DAEMON_URL` | Base URL the CLI / renderer use to reach the daemon. | `http://localhost:8000` | optional | `renderers/runner.py:230` |
| `GLUDD_WORKER_ID` | Worker identifier (multi-worker disambiguation; clamped to 1 on SQLite). | `worker` | optional | `worker/app.py:306` |
| `GLUDD_JOB_TIMEOUT_MAX` | Maximum job wall-clock seconds a worker will accept. | `600` | optional | `worker/app.py:465` |
| `GLUDD_PLAYBOOK_TIMEOUT` | Per-playbook ansible-runner timeout seconds. | none (runner default) | optional | `ansible/core_runner.py:64` |
| `GLUDD_RENDER_MAX_BYTES` | Cap on rendered prompt/output byte count. | builtin cap | optional | `renderers/runner.py:116` |
| `GLUDD_WORKER_ALLOWLIST` | Comma-separated worker IDs allowed to receive broadcasts. | all | optional | `reload/worker_broadcast.py:76` |
| `GLUDD_PERMITTED_MOUNTS` | Comma-separated OpenBao mount paths the secrets manager may read. | `secret,kv` | optional | `secrets/manager.py:25` |
| `GLUDD_PAUSE_DIR` | Override the pause-store directory. | builtin | optional | `controllers/pause_store.py:64` |
| `GLUDD_HIBERNATION_DIR` | Override the agent hibernation-store directory. | builtin | optional | `agents/hibernation.py:64` |
| `GLUDD_BACKUP_DIR` | Override the account-backup destination directory. | system temp | optional | `account/backup.py:324` |
| `GLUDD_PROJECT_DIR` | Override the active project working directory. | builtin | optional | `config/project_dir.py:31` |
| `GLUDD_PROJECT_ROOT` | Trusted explicit root for MCP builtin execution and enforcement-ledger discovery. It must name an existing directory; when unset or invalid, enforcement searches only `cwd` and its ancestors, then stays at `cwd`. | builtin | optional | `mcp/builtins.py:127`, `.opencode/lib/shared.ts:490` |
| `GLUDD_WORKSPACE` | Workspace root (issue sources, model router, integrity router). | `""` | optional | `issue_sources/csv_excel.py:91` |
| `GLUDD_REPO_ROOT` | Repo root for maintenance router operations. | `.` | optional | `routers/maintenance.py:27` |
| `GLUDD_SELF_REPO_URL` | Override the git URL used for self-update. | builtin | optional | `projects/manager.py:70` |
| `GLUDD_SELF_UPDATE_APPROVAL_SECRET` | Secret required to approve a self-update. | `""` | optional | `self_update/apply.py:216` |
| `GLUDD_MCP_ALLOW_ANY_EXEC` | Allow MCP servers to exec arbitrary commands (UNSAFE). Truthy set. | `""` (off) | optional | `mcp/transport.py:117` |
| `GLUDD_WEB_FETCH_ALLOWED_DOMAINS` | Comma-separated domains the web-fetch tool may reach. | `""` (none) | optional | `retrieval/web.py:55` |
| `GLUDD_TERRAFORM_STACKS_DIR` | Directory holding terraform stack definitions. | builtin | optional | `daemon.py:1020` |

¹ `GLUDD_PSK` becomes **required** the moment you bind the daemon to a
non-loopback interface (`--host` not `127.0.0.1`/`localhost`/`::1`): the CLI
auto-generates a 32-byte token, prints it once, and all clients must send
`Authorization: Bearer <psk>`.

### 1.2 Model-provider credentials

Set **exactly one** provider's key to get a working model. The key name a
profile expects is its `credential_alias` (see §2.2); the canonical names:

| Env var | Provider | Profile example | Default base URL |
|---|---|---|---|
| `ZAI_API_KEY` | Z.AI (GLM) — project default | `config/model_profiles/zai_example.yml` (`zai_coder`) | `https://open.bigmodel.cn/api/paas/v4` (via `ZAI_BASE_URL`) |
| `OPENAI_API_KEY` | OpenAI | `openai_example.yml` (`openai_gpt4`) | `https://api.openai.com/v1` (via `OPENAI_BASE_URL`) |
| `ANTHROPIC_API_KEY` | Anthropic (Claude) | — (use via OpenRouter or custom profile) | `https://api.anthropic.com` (via `ANTHROPIC_BASE_URL`) |
| `OPENROUTER_API_KEY` | OpenRouter (multi-provider) | `openrouter_example.yml` (`openrouter_coder`) | `https://openrouter.ai/api/v1` (via `OPENROUTER_BASE_URL`) |
| `DEEPSEEK_API_KEY` | DeepSeek | `deepseek_coder.yml` (`deepseek_coder`) | DeepSeek endpoint (via `deepseek_api_base` alias) |
| `GROQ_API_KEY` | Groq | — | Groq endpoint |
| `MISTRAL_API_KEY` | Mistral | — | Mistral endpoint |
| `FIREWORKS_API_KEY` | Fireworks AI | — | Fireworks endpoint |

Optional companion vars (provider-specific base URL / default model overrides):
`ZAI_BASE_URL`, `ZAI_MODEL`, `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`,
`OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`. The secrets layer
(`secrets/env.py`) lowercases these when injecting into the model gateway
(e.g. `ZAI_BASE_URL` → `zai_api_base`).

Any `*_API_KEY` env var present at daemon boot is also auto-registered as a
metered service for budget tracking (`daemon.py:1123`, `worker/app.py:74`) —
services without an explicit budget entry still get observed.

### 1.3 Database & persistence

| Name | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL override. **SQLite only is supported** (`sqlite+aiosqlite://...`); a non-SQLite URL is refused by `init_engine_from_config`. | `sqlite+aiosqlite:///$XDG_DATA_HOME/general-ludd/general-ludd.db` |
| `XDG_DATA_HOME` | Data-directory root (holds the SQLite file). | `~/.local/share` |

> Postgres is **not** supported in this release. `general-ludd.yml` ships a
> `database:` block for forward compatibility, but the daemon clamps gunicorn
> workers to 1 and refuses non-SQLite URLs. See `cli.py:_clamp_workers_for_sqlite`.

### 1.4 Observability, infra connectors, CI (all optional)

| Name | Purpose | Default |
|---|---|---|
| `LANGSMITH_API_KEY` + `LANGSMITH_PROJECT` | Enable LangSmith tracing. Both required to activate. | off |
| `SLURM_API_URL` + `SLURM_AUTH_TOKEN` | Slurm compute integration. | off |
| `AWS_ACCESS_KEY_ID` (+ `AWS_SECRET_ACCESS_KEY`) | AWS pricing/live onboarding. | off |
| `GITHUB_TOKEN` | GitHub Actions connector + issue sources. | off |
| `PROMETHEUS_TOKEN` | Bearer token for Prometheus connector. | off |
| `DATADOG_API_KEY` + `DATADOG_APP_KEY` | Datadog logs connector. | off |
| `POSTGRES_AVAILABLE=1` | Opt-in flag enabling Postgres-dependent tests. | skipped |
| `SLURM_AVAILABLE=1` | Opt-in flag enabling Slurm-dependent tests. | skipped |

---

## 2. Config Files

### 2.0 Config discovery — read this first

The daemon searches for its config directory in exactly this order:

1. `$GLUDD_CONFIG_DIR` (if set)
2. `~/.config/general-ludd`
3. `/etc/general-ludd`

**The repo's own `config/` directory is NOT on that path.** It is a set of
*examples to copy*, not a location the daemon reads.

> **The #1 "why does nothing happen?" trap.** If you start the daemon from a
> repo checkout **without `GLUDD_CONFIG_DIR` set**, no `model_profiles/` are
> found, so no model profiles load, the model gateway stays `None`, and the
> dispatcher silently falls back to a **no-op executor**. Every dispatched agent
> then returns `status="completed"` with **empty output** and **no warning is
> logged** — while `/healthz` and `/readyz` keep returning 200/ready. Agents
> appear to succeed instantly and do nothing.
>
> Do one of these before starting the daemon:
>
> ```bash
> export GLUDD_CONFIG_DIR="$PWD/config"        # point at the repo's config tree
> # ...or install the config into the real discovery path:
> mkdir -p ~/.config/general-ludd
> cp -r config/model_profiles ~/.config/general-ludd/
> cp config/general-ludd.yml ~/.config/general-ludd/
> ```
>
> Confirm it worked: `gludd models router-status` must list an active profile.
> An empty profile list means you are in the no-op-executor failure mode.

### 2.0.1 Layering

Operators override by copying into `~/.config/general-ludd/` (user) or
`/etc/general-ludd/` (system); env vars win over all file layers. Load priority
(high → low):

1. Environment variables
2. `~/.config/general-ludd/user.yml` — per-user overrides
3. `.general-ludd/agent_config.yml` — per-project agent settings
4. `/etc/general-ludd/general-ludd.yml` — system defaults
5. Built-in defaults compiled into the package (**not** the repo's `config/` tree)

### 2.1 Top-level files

| Path | Format | Purpose |
|---|---|---|
| `config/general-ludd.yml` | YAML | **Main config.** Holds `model_routing`, `database`, `agents`, `process_isolation`, `budget`. The default profile is `deepseek_coder` (fallback chain `qwen_coder` → `zai_coder`). |
| `config/model_routing.yml` | YAML | Standalone routing table (alternative to the `model_routing:` block in `general-ludd.yml`). Defines `default_profile`, `fallback_chain`, role/quality/latency/pattern routing. |
| `config/binary_paths.yml` | YAML | Overrides paths to external binaries (terraform, opentofu, vault, openbao, podman, docker, ansible-playbook, git, uv, opa, conftest). Defaults to `shutil.which()` PATH lookup. |
| `config/ratchet.yml` | YAML | Known-failing test tracker (`node_id: reason`). Read by `tests/conftest.py`; the suite stays red until a passing test's marker is lifted. **Not a runtime config** — operators do not edit it. |

### 2.2 `config/model_profiles/*.yml` — per-model definitions

Each file defines one profile referenced by ID from routing tables. Shipped
examples (copy the relevant one, set its `credential_alias` env var, and point
routing at its `model_profile_id`):

| File | `model_profile_id` | Provider | Credential alias |
|---|---|---|---|
| `zai_example.yml` | `zai_coder` | Z.AI (GLM-4.6) | `ZAI_API_KEY` |
| `openai_example.yml` | `openai_gpt4` | OpenAI (GPT-4) | `openai_api_key` |
| `openrouter_example.yml` | `openrouter_coder` | OpenRouter | `openrouter_api_key` |
| `deepseek_coder.yml` | `deepseek_coder` | DeepSeek | `deepseek_api_key` |
| `qwen_coder.yml` | `qwen_coder` | Qwen | (see file) |
| `anthropic_example.yml` | — | Anthropic | `ANTHROPIC_API_KEY` |
| `vllm_example.yml` | — | Local vLLM | (none — local) |
| `llamacpp_example.yml` | — | Local llama.cpp | (none — local) |
| `compactor.yml` | — | Compaction model | (see file) |

Minimal profile shape (from `zai_example.yml`):

```yaml
model_profile_id: zai_coder
provider: openai                     # langchain provider key
provider_package: langchain_openai   # pip-importable package
provider_class_hint: ChatOpenAI      # chat class
model_name: glm-4.6
credential_alias: ZAI_API_KEY        # env var read at call time
api_base_alias: ZAI_BASE_URL         # optional endpoint override
context_window: 64000
max_input_tokens: 60000
max_output_tokens: 16384
cost_per_input_token: 0.001
cost_per_output_token: 0.003
run_budget_usd: 1.0
enabled: true
roles: [coder, planner]
latency_class: fast
quality_class: high
fallback_profiles: []                # profile IDs to try on failure
probe_enabled: false                 # health-probe the profile at boot
```

### 2.3 `config/permissions/*.yml` — PermissionSpec per subject

Capability/deny lists for each agent type and human role. Selected by
`default_human_role` (default `human-operator`) for human users; agents get
their `agent_type` spec. The **intersection rule** applies on subagent
dispatch (effective spec = lowest-common-subset of human ∩ agent ∩ requested).

| File | Subject | Notable scope |
|---|---|---|
| `build.yml` | default build agent | repo `/repo/`, tmp `/tmp/gludd/`, LLM egress to anthropic/openai/z.ai, OpenBao `secret/data/gludd/build/*`, ornith solve/improve |
| `primary.yml` | primary orchestrator agent | widest agent scope |
| `subagent.yml` | dispatched subagent | narrowed from parent |
| `task_implement_change.yml` | task-implement role | change-implementation scoped |
| `agent-ornith.yml` | ornith self-improve agent | training-loop scoped |
| `human-admin.yml` | human admin | full file/net/secret access |
| `human-operator.yml` | human operator (default) | repo + any net + OpenBao read |
| `human-viewer.yml` | human viewer | read-only |

### 2.4 Other `config/` subdirectories

| Path | Purpose |
|---|---|
| `config/agents/default_agents.yml` | Agent definitions (default agent `build`, max_concurrent `4`). |
| `config/tasks/example_tasks.yml` | Seed todos for first-boot / demos. |
| `config/examples/` | Copy-and-edit templates: `user_config_example.yml` (user overrides), `agent_config_example.yml` (per-project), `connectors_example.yml` (observability connectors — Prometheus, Datadog, GH Actions, journald). |
| `config/prompt_profiles/` | Prompt-template definitions. |
| `config/skills/` | Skill catalog definitions. |
| `config/mcp_servers/` | MCP server configs (loaded by `daemon.py` lifespan; external servers registered alongside builtins). |
| `config/ansible/` | Ansible runtime config (ansible.cfg, collection paths). |
| `config/infra/` | Infrastructure integration configs. |
| `config/opa/` | Open Policy Agent rego policies. |
| `config/openbao/` | OpenBao (secrets) connection config. |

### 2.5 Example: minimal `general-ludd.yml`

```yaml
model_routing:
  default_profile: deepseek_coder   # must match a model_profile_id
  weak_model_profile: deepseek_coder
  role_routing:    {coder: deepseek_coder, planner: deepseek_coder, reviewer: deepseek_coder}
  quality_routing: {high: deepseek_coder, medium: deepseek_coder}
  latency_routing: {fast: deepseek_coder}
  pattern_routing: {code_generation: coder, commit_message: weak}
database:                           # SQLite-only; block is forward-compat
  host: localhost
  port: 5432
  name: gludd
  user: gludd
agents:
  default_agent: build
  max_concurrent: 4
process_isolation:
  enabled: false                    # set true + install podman/bwrap for sandboxing
  container_runtime: podman
budget:
  max_usd: 50                       # hard spend cap across all profiles
  warn_percent: 80
```

---

## 3. Minimal Run Path (verified step-by-step)

Each step below lists the exact command, what success looks like, and how it
was verified for this doc.

### Step 0 — Prerequisites

- Python ≥ 3.11
- `uv` (preferred) or `pip`
- SQLite (bundled with Python; zero-config)
- One model-provider API key (§1.2)

### Step 1 — Install

```bash
make init
```

**What it does:** creates the venv, runs `uv sync`, creates runtime dirs.
**Success:** command exits 0; `.venv/` populated.
**Verified:** `make healthcheck` → `Worker app factory OK` / `Event loop import OK`
(run for this doc; confirms `general_ludd.daemon`, worker factory, and event
loop import cleanly). `make bootstrap` additionally runs lint + test +
healthcheck for a full green check.

### Step 2 — Configure one model provider

Pick **one** provider. Cheapest default path is Z.AI:

```bash
export ZAI_API_KEY=your-key-here
# optional, only if not using the profile's built-in endpoint:
# export ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

The default profile (`deepseek_coder`, set in `config/general-ludd.yml` →
`model_routing.default_profile`) reads `DEEPSEEK_API_KEY` at call time via its
`credential_alias`. No file edit is required for the default path.

**To switch provider:** either (a) edit `default_profile:` in
`general-ludd.yml` to a different `model_profile_id` whose
`credential_alias` you have set, or (b) set the matching `*_API_KEY` env var
and rely on auto-discovery (`daemon.py:1123` registers any `*_API_KEY` as a
metered service; `provider_presets.py` maps credential env vars per provider).

**Success:** the chosen `*_API_KEY` is present in `env`; `gludd models
router-status` (against a running daemon) lists the active profile.

### Step 3 — Start the daemon

**From a repo checkout, set `GLUDD_CONFIG_DIR` first** (§2.0) — otherwise no model
profiles load and the dispatcher silently no-ops.

```bash
export GLUDD_CONFIG_DIR="$PWD/config"
gludd daemon
# or with flags:
gludd daemon --host 127.0.0.1 --port 8000 --log-level info --tick-interval 1.0
```

**What it does:** spawns `gunicorn general_ludd.daemon:create_daemon_app()
--worker-class uvicorn_worker.UvicornWorker --workers 1 --bind 127.0.0.1:8000`
(workers clamped to 1 on SQLite). The FastAPI app boots, runs
`alembic stamp_head` on the SQLite DB, starts the event-loop tick task, and
registers model/MCP/permission subsystems.

**Success:** `curl http://localhost:8000/healthz` returns JSON `{"status":"ok"}`
(equivalently `gludd health`). The first tick logs show the event loop
running. Binding to a non-loopback host auto-generates and prints a `GLUDD_PSK`
— all clients must then send `Authorization: Bearer <psk>`.

> **`/healthz` and `/readyz` returning 200/ready does NOT prove the daemon can do
> any work.** They report 200 even when zero model profiles loaded and the
> dispatcher is a no-op. The real liveness check is
> `gludd models router-status` — it must list an active profile. See §2.0.

**Verified:** the daemon factory and event loop import cleanly
(`make healthcheck`); full boot behaviour is pinned by
`tests/unit/test_daemon_launch_config.py` and exercised end-to-end by
`make smoke` (daemon start → todo submit → todo complete → daemon stop).

### Step 4 — Submit a todo

```bash
gludd add "Write a hello-world pytest test" --work-type code
```

**What it does:** `POST /api/todos` with `{title, description, queue:"core",
priority:100, work_type}`. The todo is persisted to SQLite and becomes
claimable on the next event-loop tick.

**Success:** JSON response with the new todo's `id`, `status: "queued"`.
Confirm with `gludd list`.

### Step 5 — Watch it complete

```bash
gludd status                 # system + todo summary
gludd list --status queued   # watch the queue drain
gludd status <TODO_ID>       # per-todo detail incl. transitions
```

**What happens:** the event loop claims the runnable todo, dispatches it to
the configured model profile, executes the returned plan via the ansible
runner, and transitions the todo through `queued → running → completed`
(or `failed` / `blocked_on_human`).

**Success:** the todo reaches `completed` with a recorded result; `gludd
status <id>` shows the terminal state and any produced artifacts.

### Full green check (optional, slower)

```bash
make bootstrap               # init + lint + test + healthcheck
make gate                    # lint + typecheck + collect + test + smoke (writes .gate-status)
# or background (recommended on the main thread):
make gate-background && make gate-status-check
```

---

## 4. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Cannot connect to daemon at http://localhost:8000` | daemon not running | `gludd daemon` |
| `GLUDD_REQUIRE_AUTH is set but no GLUDD_PSK configured ... failing CLOSED (503)` | auth forced without a PSK | `export GLUDD_PSK=$(openssl rand -hex 32)` and send it as Bearer token, or unset `GLUDD_REQUIRE_AUTH` |
| 401 on CLI calls | daemon bound to non-loopback (auto-PSK) but CLI lacks it | re-read the PSK printed at daemon boot; `export GLUDD_PSK=<that value>` |
| Todo stuck in `queued` | no model profile reachable / key missing | confirm the profile's `credential_alias` env var is set; `gludd models router-status` |
| **Agents return `completed` instantly with EMPTY output, no warning, health still 200** | **No model profiles were found, so the dispatcher fell back to a no-op executor.** Almost always: the daemon was started from a repo checkout without `GLUDD_CONFIG_DIR`. | Set `GLUDD_CONFIG_DIR` (or install the config into `~/.config/general-ludd/`) and restart — see §2.0. Verify with `gludd models router-status`. |
| Every write endpoint fails / DB is read-only | `GLUDD_WRITER_MODE=subprocess` was set | Unset it. `inline` is the only working mode — see §5. |
| `non-SQLite URL refused` | `DATABASE_URL` points at Postgres | unset it (SQLite-only this release); Postgres is unsupported |
| `gunicorn workers clamped to 1` warning | `--workers N>1` on SQLite | expected; single-worker is the honest SQLite config |
| Config not applied | layering mismatch | env vars > `~/.config/general-ludd/user.yml` > `.general-ludd/agent_config.yml` > `/etc/general-ludd/general-ludd.yml` > defaults |

---

## 5. Experimental flags — DO NOT ENABLE

These knobs exist in the code and in config schemas but are **not functional**.
They are listed here so operators do not "discover" them and turn them on.

### `GLUDD_WRITER_MODE=subprocess` — structurally non-functional

Setting this **breaks every write endpoint.** Three independent defects:

- The `WriteQueue` is an in-process deque with no IPC, while the writer is a
  real subprocess — the queue **cannot reach the writer child** at all.
- A config-shape bug leaves the writer child permanently in a stub branch, so it
  never does any work.
- HTTP workers are handed a genuinely read-only engine (`PRAGMA query_only=ON`).

Net effect: writes are rejected and the writer does nothing.
**`inline` (the default) is the only working mode.** Do not set this variable.

### `pipeline.enabled` (feature #77) — EXPERIMENTAL, do not enable

The pipeline feature's quality gate is hardcoded to `return True` — it reports
"GREEN — committed" for a validation that never ran — and its anti-clobber merge
passes the repo's own content as both the merge base and "ours", so it **can
never detect a conflict**. It is harmless today only because nothing feeds it.
Leave `pipeline.enabled` off.

---

## 6. Cross-references

- `docs/quickstart.md` — fast-path narrative version of §3
- `docs/RELEASE_RUNBOOK.md` — cutting a release and verifying it actually shipped
- `docs/configuration.md` — detailed `general-ludd.yml` field reference
- `docs/model-setup.md` — model provider onboarding
- `docs/PROVIDER_ONBOARDING.md` — adding a new provider
- `docs/PROVIDER_ONBOARDING.md` / `docs/profiles.md` — profile authoring
- `docs/operations/` — operator runbooks
- `AGENTS.md` § "Project Overview" / "Key Make Targets" — make target catalogue
- `Makefile` — every runnable target (only `make <target>` is permitted in bash)
