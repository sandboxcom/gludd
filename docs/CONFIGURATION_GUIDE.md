# Configuration Guide: Required vs Optional

This guide tells you what you actually need to configure — and what you can leave
alone. If you are new to gludd, read "Zero-Config Boot" first and then jump to
the "Decision Tree" for your use case.

For the exhaustive field-by-field reference, see
[`docs/configuration.md`](./configuration.md). This document is the *opinionated*
companion: which knobs matter, which are optional, and which files (if any) you
need to create.

---

## Zero-Config Boot

**gludd works out of the box with built-in defaults. No files need to be created. Period.**

When the daemon starts and finds no config files on the discovery path, it falls
back to hardcoded defaults compiled into the daemon itself (NOT the repo's
`config/` tree — those are examples to copy). The defaults give you:

- A working SQLite database at `~/.local/share/general-ludd/gludd.db`
- A daemon bound to `127.0.0.1:8000` (loopback only — not exposed to the network)
- The built-in `zai_coder` model profile (Z.AI GLM-5.1 via the OpenAI-compatible API)
- A `$50 USD` per-run budget cap with an 80% warning threshold
- Process isolation **disabled** (Ansible playbooks run in the daemon's own process)
- No MCP servers connected
- The default `build` agent with workspace read/write/bash permissions

The only thing zero-config cannot give you is **API credentials**. If your
default model profile needs a key (Z.AI does), set the env var
(`ZAI_API_KEY`, `ZAI_BASE_URL`) — but no YAML file is required for that either.

> **The silent no-op trap:** If you run from a source checkout without setting
> `GLUDD_CONFIG_DIR`, the daemon will not find `model_profiles/`, the model
> gateway stays `None`, and dispatches silently succeed with empty output. Either
> set `GLUDD_CONFIG_DIR="$PWD/config"` or copy profiles into
> `~/.config/general-ludd/`. See `docs/configuration.md` for details.

---

## Configuration Hierarchy

gludd layers configuration in five steps. Each step overrides the one above it:

```text
┌─────────────────────────────────────────────────────────┐
│ 5. Command-line flags        (highest priority)         │
├─────────────────────────────────────────────────────────┤
│ 4. Environment variables    (GLUDD_*, DATABASE_URL,     │
│                              ZAI_API_KEY, …)            │
├─────────────────────────────────────────────────────────┤
│ 3. ~/.config/general-ludd/user.yml   (per-user)         │
├─────────────────────────────────────────────────────────┤
│ 2. /etc/general-ludd/general-ludd.yml (system-wide)    │
├─────────────────────────────────────────────────────────┤
│ 1. Built-in defaults         (lowest priority)          │
└─────────────────────────────────────────────────────────┘
```

| Layer | Path | When it wins |
|-------|------|--------------|
| 1. Built-in defaults | compiled into the daemon | Always present; only wins if no higher layer sets the value. |
| 2. System-wide | `/etc/general-ludd/general-ludd.yml` | Multi-user hosts, production baselines. |
| 3. Per-user | `~/.config/general-ludd/user.yml` | Single-user developer machines. |
| 4. Environment | `GLUDD_*`, `DATABASE_URL`, provider keys | Container deploys, CI, secrets injection. |
| 5. CLI flags | `--host`, `--port`, etc. | One-off overrides, debugging. |

**Config directory discovery** (where the daemon looks for the YAML files above)
follows this order:

1. `$GLUDD_CONFIG_DIR` (if set)
2. `~/.config/general-ludd`
3. `/etc/general-ludd`

The repo's own `config/` directory is **NOT** on this path — it holds examples
to copy, not files the daemon reads.

---

## Required vs Optional

**Nothing in the table below is required to boot.** "Required?" means "required
for the named use case," not "required for the daemon to start."

| Config Area | Required? | Default Behavior | When to Customize |
|-------------|-----------|------------------|-------------------|
| **Database** | No | SQLite at `~/.local/share/general-ludd/gludd.db` (or in-memory for tests). PostgreSQL-style keys in the YAML are accepted but ignored — the daemon is SQLite-only and refuses non-SQLite `DATABASE_URL`s at startup. | When relocating the SQLite file (e.g. `/var/lib/general-ludd/gludd.db` for a systemd unit). |
| **Model Profile** | No | Uses the built-in `zai_coder` profile (Z.AI GLM-5.1, OpenAI-compatible). | When using OpenAI, OpenRouter, Anthropic, vLLM, llama.cpp, or any other provider. |
| **Network** | No | Binds to `127.0.0.1:8000` (loopback only). `allowed_cidr` is commented out. | When exposing to a LAN/VPN, or running behind a reverse proxy on a different port. |
| **Permissions** | No | The default human role is `human-operator` (`config/permissions/human-operator.yml`). Agents inherit the intersection of the human spec, the agent spec, and the requested spec. | When adding admins/viewers, or restricting what an agent may read/write. |
| **Process Isolation** | No | Disabled. Ansible playbooks execute in the daemon's own process via `ansible-runner`. | When running untrusted playbooks, multi-tenant workloads, or any production deployment. |
| **Budget** | No | `$50 USD` hard cap per run, warning at 80% ($40). | When adjusting spend limits up (more headroom) or down (tighter guardrails). |
| **MCP Servers** | No | None configured. The MCP client starts with an empty server list. | When connecting external tools (filesystem bridges, GitHub, Slack, custom MCP servers). |
| **Prompt Profiles** | No | The built-in default prompt set is used for every agent role. | When customizing agent behavior — system prompts, few-shot examples, role-specific framing. |
| **Agent Definitions** | No | A single `build` agent ships in `config/agents/default_agents.yml`. | When adding specialized agents (reviewer, planner, researcher, etc.). |
| **OpenBao / Secrets** | No | Secret resolution falls back to environment variables (`credential_alias` names the env var). | When centralizing secrets in a vault for production. |

---

## Decision Tree — "I want to…"

Match your goal to the config area. Every arrow points at the minimal file or
section you need to touch.

```bash
"I want to..."
│
├── ...use a different AI model
│   └── Configure model_profiles/
│       • Copy config/model_profiles/<provider>_example.yml
│         into ~/.config/general-ludd/model_profiles/
│       • Set model_routing.default_profile to the new profile ID
│       • Set the credential_alias env var (e.g. OPENAI_API_KEY)
│
├── ...restrict what agents can do
│   └── Configure permissions/
│       • Edit config/permissions/human-{admin,operator,viewer}.yml
│       • Or add a new PermissionSpec and reference it via default_human_role
│       • Intersection rule narrows: effective = human ∩ agent ∩ requested
│
├── ...connect external tools
│   └── Configure mcp_servers/
│       • Copy config/mcp_servers/example.yml
│       • Add a server entry per MCP server (command, args, timeout)
│       • Set enabled: true on each server you want auto-started
│
├── ...customize agent behavior
│   └── Configure prompt_profiles/
│       • Default prompt profile is "default"; override per agent
│       • Reference the new profile by name in agents[].prompt_profile
│
├── ...run in production
│   └── Configure database + network + budget (min 2 files)
│       • database: relocate SQLite to /var/lib/general-ludd/gludd.db
│       • network: set host/port + uncomment allowed_cidr for your LAN
│       • budget: tune max_usd to your spend ceiling
│       • Also recommended: process_isolation.enabled: true
│
├── ...run fully offline (air-gapped)
│   └── Configure a local model profile (vllm or llamacpp)
│       • Copy config/model_profiles/vllm_example.yml
│       • Set api_base_alias (no credential_alias needed for local)
│       • Set model_routing.default_profile to the local profile ID
│       • Confirm cost_per_*_token: 0.0 and api_metered: false
│
├── ...add a new agent role
│   └── Configure agents/default_agents.yml
│       • Add an entry with name, type, model_profile, prompt_profile
│       • Define permissions (can_edit, can_bash, can_dispatch_subagents)
│       • Reference it from model_routing.role_routing if it needs a specific model
│
└── ...centralize secrets
    └── Configure openbao/default.yml
        • Set mode: external, backend: openbao
        • Store each credential_alias at secret/general-ludd/<alias>
        • Env-var fallback still works for any alias not in the vault
```

---

## Minimal Required Config for Common Setups

Counting only files **you must create**. Built-in defaults cover everything else.

### 1. Single-user local dev — **ZERO config files**

```bash
export ZAI_API_KEY=your-key
export ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
make smoke
```

That's it. The daemon uses SQLite in `~/.local/share/general-ludd/`, binds to
loopback, and uses the built-in `zai_coder` profile.

### 2. Team setup — **1 file** (permissions for human roles)

Create `~/.config/general-ludd/user.yml` with the human role overrides:

```yaml
# Optional: switch the default human role
# default_human_role: human-operator   # already the default

# Optional: cap concurrency for a shared host
agents:
  max_concurrent: 4
```

If you want role tiers (admin/operator/viewer), the three files in
`config/permissions/human-*.yml` are already correct — copy the one matching
each user's responsibility into the user config dir, or assign per-user via
`default_human_role` overrides.

### 3. Production — **2 files** (database config + network config)

`/etc/general-ludd/general-ludd.yml`:

```yaml
network:
  host: 0.0.0.0
  port: 8000
  allowed_cidr:
    - 10.0.0.0/8        # your internal LAN
    - 127.0.0.0/8

database:
  url: sqlite+aiosqlite:////var/lib/general-ludd/gludd.db

process_isolation:
  enabled: true
  container_runtime: podman

budget:
  max_usd: 200
  warn_percent: 80
```

`/etc/general-ludd/env` (mode 600, owner-only readable):

```bash
ZAI_API_KEY=your-production-key
ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
GLUDD_CONFIG_DIR=/etc/general-ludd
```

Recommended additions for production:
- A model profile pointed at OpenBao (not env vars) for secret rotation
- `process_isolation.enabled: true` (run playbooks in podman containers)
- A reverse proxy (nginx/caddy) in front of the daemon for TLS

### 4. Air-gapped — **1 file** (local model profile)

`~/.config/general-ludd/model_profiles/vllm_local.yml`:

```yaml
model_profile_id: vllm_local
role_names: [coder, planner, reviewer]
provider: vllm
provider_package: langchain-community
provider_class_hint: ChatVLLM
model_name: mistral-7b-instruct
api_base_alias: VLLM_BASE_URL       # resolved from env or vault
context_window: 32000
max_input_tokens: 30000
max_output_tokens: 4000
cost_per_input_token: 0.0
cost_per_output_token: 0.0
api_metered: false
run_budget_usd: 0.0
enabled: true
latency_class: medium
quality_class: medium
fallback_profiles: []
probe_enabled: false
```

`~/.config/general-ludd/user.yml`:

```yaml
model_routing:
  default_profile: vllm_local
  weak_model_profile: vllm_local
  role_routing:
    coder: vllm_local
    planner: vllm_local
    reviewer: vllm_local
```

Set `VLLM_BASE_URL=http://your-vllm-host:8000/v1` in the environment. No API
key is needed for a local vLLM/llama.cpp server.

---

## See Also

- [`docs/configuration.md`](./configuration.md) — exhaustive field reference,
  config directory discovery, credential resolution order, experimental flags
  to avoid.
- [`docs/quickstart.md`](./quickstart.md) — first-run walkthrough.
- [`docs/MCP_ONBOARDING.md`](./MCP_ONBOARDING.md) — connecting MCP servers.
- [`docs/SECURITY_ROLES.md`](./SECURITY_ROLES.md) — PermissionSpec details and
  the intersection rule.
- [`docs/model-setup.md`](./model-setup.md) — provider onboarding (OpenAI,
  OpenRouter, Anthropic, vLLM, llama.cpp).
