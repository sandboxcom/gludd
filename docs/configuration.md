# Configuration Reference

## Config Directory Discovery

The daemon looks for its config directory in exactly this order:

1. `$GLUDD_CONFIG_DIR` (if set)
2. `~/.config/general-ludd`
3. `/etc/general-ludd`

**The repo's own `config/` directory is NOT on that path.** Everything under `config/`
in a source checkout is an *example to copy*, not a file the daemon reads.

> ### ⚠ The silent no-op trap
>
> Starting the daemon from a repo checkout **without `GLUDD_CONFIG_DIR` set** means no
> `model_profiles/` are found. No model profiles load, the model gateway stays `None`,
> and the dispatcher silently falls back to a **no-op executor**: every dispatched agent
> returns `status="completed"` with **empty output** and **no warning is logged**, while
> `/healthz` and `/readyz` still report 200/ready.
>
> If agents appear to succeed instantly and produce nothing, this is why. Fix it by
> setting `GLUDD_CONFIG_DIR="$PWD/config"`, or by copying the config into
> `~/.config/general-ludd/`. Confirm with `gludd models router-status` — an empty
> profile list means you are still in the trap.

## Config File Locations

Within the discovered config directory, General Ludd Agent loads configuration from
multiple layers, in priority order:

| Priority | Location | Purpose |
|----------|----------|---------|
| 1 (highest) | Environment variables | `DATABASE_URL`, `ZAI_API_KEY`, etc. |
| 2 | `~/.config/general-ludd/user.yml` | Per-user overrides |
| 3 | `.general-ludd/agent_config.yml` | Per-project agent settings |
| 4 | `/etc/general-ludd/general-ludd.yml` | System-wide defaults |
| 5 (lowest) | Built-in defaults | Hardcoded fallbacks (**not** the repo's `config/` tree) |

## Main Config: general-ludd.yml

The main config file has these sections:

### model_routing

Controls which AI model profile is used for each type of task.

```yaml
model_routing:
  # Default profile for all tasks (required)
  default_profile: zai_coder

  # Cheaper model for low-stakes work
  weak_model_profile: zai_coder

  # Route by agent role
  role_routing:
    coder: zai_coder
    planner: zai_coder
    reviewer: zai_coder

  # Route by quality requirement
  quality_routing:
    high: zai_coder
    medium: zai_coder

  # Route by latency requirement
  latency_routing:
    fast: zai_coder

  # Route by work pattern
  pattern_routing:
    return_review: reviewer
    commit_message: weak
    code_generation: coder
    planning: planner
```

Profile IDs must match a `model_profile_id` from `config/model_profiles/*.yml`.

### database

gludd is **SQLite only**. The database defaults to
`~/.local/share/general-ludd/gludd.db` with no configuration needed. If you set a `url`
it must be a `sqlite+aiosqlite:///` URL; any other URL is refused at startup.

```yaml
database:
  # Optional: override the default SQLite path
  url: sqlite+aiosqlite:////var/lib/general-ludd/gludd.db
  # WAL retention after a successful reset/checkpoint (1 MiB..1 GiB).
  journal_size_limit_bytes: 67108864
  # Trigger a passive checkpoint after this many pages (1..100000).
  wal_autocheckpoint_pages: 1000
  # Bound lock waiting so a stalled writer cannot block forever (1..60000 ms).
  busy_timeout_ms: 5000
```

Environment variable `DATABASE_URL` overrides the above (must also be a SQLite URL).
The same keys can be overridden through the nested `GLUDD_DATABASE` settings
object. Invalid types, booleans, numeric strings, zero, negative, or out-of-range
values fail startup before the SQLite file is provisioned.

These settings bound retained WAL size after a successful reset and control when
passive checkpoints are attempted; they are not an absolute file-size guarantee
during checkpoint starvation. The SQLite user-forum reports
[WAL growth past the auto-checkpoint threshold](https://sqlite.org/forum/info/915267efb1f68f9c525c32e3ae8ef4251285e1111c5f5c221fb348df50119640)
when a read statement remains unfinished, and confirms that
[passive auto-checkpoints cannot restart or truncate the WAL](https://sqlite.org/forum/forumpost/e37d976043a22458070ce00a4ae00dc6e49ef6dd34aa59e2c5ff7cf5fd543a93).
Production operators should monitor the database, `-wal`, and filesystem as one
capacity unit until the coordinated checkpoint/disk-pressure phase is available.

### agents

Global agent behavior settings. Individual agent definitions are in `config/agents/default_agents.yml`.

```yaml
agents:
  default_agent: build    # Which agent handles unassigned tasks
  max_concurrent: 4       # Max agents running simultaneously
```

### process_isolation

Run Ansible playbooks in containers for safety.

```yaml
process_isolation:
  enabled: false           # Enable for production
  container_runtime: podman  # podman or docker
```

### budget

Spending limits for AI model API calls.

```yaml
budget:
  max_usd: 50        # Hard limit in USD
  warn_percent: 80   # Warn at 80% of limit
```

## Model Profiles

Model profiles are YAML files in the **`model_profiles/` subdirectory of the discovered
config directory** (see "Config Directory Discovery" above) — i.e.
`~/.config/general-ludd/model_profiles/`, `/etc/general-ludd/model_profiles/`, or
`$GLUDD_CONFIG_DIR/model_profiles/`. Each defines a model provider connection; the
daemon loads all `*.yml` files it finds there at startup.

The repo's `config/model_profiles/` holds **examples to copy** into that directory —
the daemon does not read it unless you point `GLUDD_CONFIG_DIR` at the repo's `config/`.
If no profiles are found, the daemon dispatches to a silent no-op executor (see the trap
above).

**API keys are NEVER stored in profile YAML files.** Each profile has a
`credential_alias` field that names the secret. The daemon resolves it through:

1. **OpenBao/Vault** (if configured) — reads `secret/general-ludd/<alias>`
2. **Environment variable** (fallback) — reads the env var named by the alias

```yaml
# config/model_profiles/my_provider.yml
model_profile_id: my_gpt4       # Unique ID referenced in model_routing
role_names: [coder, planner]    # Which roles can use this profile
provider: openai                # Provider type
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: gpt-4               # Model identifier
credential_alias: OPENAI_API_KEY  # Resolved from vault or env (NEVER the actual key)
api_base_alias: OPENAI_BASE_URL   # Optional: resolved the same way
context_window: 128000          # Token context window
max_input_tokens: 120000
max_output_tokens: 8000
cost_per_input_token: 0.03      # USD per 1K tokens
cost_per_output_token: 0.06
run_budget_usd: 50.0            # Per-task budget
enabled: true
```

### Z.AI Example

The default profile uses Z.AI (GLM-5.1) via the OpenAI-compatible API:

```yaml
# config/model_profiles/zai_example.yml
model_profile_id: zai_coder
model_name: glm-5.1
credential_alias: ZAI_API_KEY     # reads ZAI_API_KEY from env or vault
api_base_alias: ZAI_BASE_URL      # reads ZAI_BASE_URL from env or vault
provider: openai
enabled: true
```

Required env vars in `/etc/general-ludd/env`:
```bash
ZAI_API_KEY=your-zai-api-key
ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
```

Or in OpenBao:
```bash
bao kv put secret/general-ludd/ZAI_API_KEY value=your-zai-api-key
bao kv put secret/general-ludd/ZAI_BASE_URL value=https://open.bigmodel.cn/api/paas/v4
```

### Local Model Example (no API key needed)

```yaml
# config/model_profiles/vllm_local.yml
model_profile_id: vllm_local
model_name: my-model
api_base_alias: VLLM_BASE_URL
# No credential_alias — local servers don't need auth
provider: openai
enabled: true
```

### Supported Providers

| Provider | `provider` value | `provider_package` | Needs `credential_alias`? |
|----------|-----------------|-------------------|--------------------------|
| OpenAI | `openai` | `langchain-openai` | Yes: `OPENAI_API_KEY` |
| Z.AI | `openai` (compatible) | `langchain-openai` | Yes: `ZAI_API_KEY` + `ZAI_BASE_URL` |
| OpenRouter | `openai` (compatible) | `langchain-openai` | Yes: `OPENROUTER_API_KEY` + `OPENROUTER_BASE_URL` |
| vLLM (local) | `openai` (compatible) | `langchain-openai` | No (use `api_base_alias` only) |
| llama.cpp (local) | `openai` (compatible) | `langchain-openai` | No (use `api_base_alias` only) |

### Credential Resolution

**Most secure to least secure:**

1. **OpenBao/Vault** (recommended for production) — reads `secret/general-ludd/<credential_alias>`
   - Configure in `config/openbao/default.yml` with `mode: external`
   - Supports AppRole auth with short-lived tokens
   - Store keys: `bao kv put secret/general-ludd/ZAI_API_KEY value=actual-key`
2. **Environment variable** — reads the env var named by `credential_alias`
   - Set in `/etc/general-ludd/env` (mode 600, owner-only readable)
   - Example: `ZAI_API_KEY=your-key`
3. **Error** if the secret is not found anywhere

## Agent Definitions

Agents are defined in `config/agents/default_agents.yml`:

```yaml
agents:
  - name: build
    description: "Primary build agent"
    type: primary               # primary or subagent
    model_profile: zai_coder    # Model profile ID
    prompt_profile: default     # Prompt template set
    max_steps: 10               # Max actions per task
    permissions:
      can_edit: true            # Can modify files
      can_bash: true            # Can run commands
      can_read: true            # Can read files
      can_dispatch_subagents: true
      allowed_subagents: ["*"]
    max_concurrent: 1
    enabled: true
```

## Environment File

`/etc/general-ludd/env` contains environment variables loaded by the systemd unit:

```bash
# API Keys
ZAI_API_KEY=your-key
ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# Database (SQLite only — a non-SQLite URL is refused at startup)
DATABASE_URL=sqlite+aiosqlite:////var/lib/general-ludd/gludd.db

# Config directory (required when running from a source checkout)
GLUDD_CONFIG_DIR=/etc/general-ludd

# Optional
GLUDD_LOG_LEVEL=info
GLUDD_WORKERS=1
```

## Experimental Flags — DO NOT ENABLE

These exist in the code and in config schemas but are **not functional**. They are
documented here only so operators do not turn them on.

### `GLUDD_WRITER_MODE=subprocess`

**Structurally non-functional — enabling it breaks every write endpoint.** The
in-process `WriteQueue` has no IPC and cannot reach the writer subprocess; a
config-shape bug keeps the writer child permanently in a stub branch; and HTTP workers
are handed a genuinely read-only engine (`PRAGMA query_only=ON`). Writes are rejected
and the writer does nothing.

**`inline` (the default) is the only working mode.** Do not set this variable.

### `pipeline.enabled` (feature #77)

**EXPERIMENTAL — do not enable.** Its quality gate is hardcoded to `return True` (it
logs "GREEN — committed" for a validation that never ran), and its anti-clobber merge
passes the repo's own content as both the merge base and "ours", so it can never detect
a conflict. It is harmless today only because nothing feeds it.

## Directory Structure

```text
/etc/general-ludd/
  general-ludd.yml          Main config
  env                       Environment variables
  config/
    model_routing.yml       Default model routing
    model_profiles/         Model provider profiles
      zai_example.yml
      openai_example.yml
      openrouter_example.yml
      vllm_example.yml
      llamacpp_example.yml
    agents/
      default_agents.yml    Agent definitions
    binary_paths.yml        External binary paths
    ansible/
      isolation.yml         Process isolation settings
    tasks/
      example_tasks.yml     Example task definitions
    examples/               Config examples for reference
    mcp_servers/            MCP server configurations
    openbao/                OpenBao settings
    infra/                  Infrastructure pricing reference
  templates/                Prompt templates

/var/log/general-ludd/      Logs
/var/lib/general-ludd/      Runtime state
```
