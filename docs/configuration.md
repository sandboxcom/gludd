# Configuration Reference

## Config File Locations

General Ludd Agent loads configuration from multiple layers, in priority order:

| Priority | Location | Purpose |
|----------|----------|---------|
| 1 (highest) | Environment variables | `DATABASE_URL`, `ZAI_API_KEY`, etc. |
| 2 | `~/.config/general-ludd/user.yml` | Per-user overrides |
| 3 | `.general-ludd/agent_config.yml` | Per-project agent settings |
| 4 | `/etc/general-ludd/general-ludd.yml` | System-wide defaults |
| 5 (lowest) | Built-in defaults | Hardcoded fallbacks |

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

PostgreSQL connection settings. If omitted, falls back to SQLite (not for production).

```yaml
database:
  # Option 1: Full URL (overrides everything else)
  url: postgresql://user:pass@host:5432/dbname

  # Option 2: Individual components
  host: localhost
  port: 5432
  name: gludd
  user: gludd
  password: secret  # Better: put in env file
```

Environment variable `DATABASE_URL` overrides all of the above.

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

Model profiles are YAML files in `config/model_profiles/`. Each defines a model
provider connection. The daemon loads all `*.yml` files in this directory at startup.

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

# Database
DATABASE_URL=postgresql://gludd:password@localhost:5432/gludd

# Optional
GLUDD_LOG_LEVEL=info
GLUDD_WORKERS=1
```

## Directory Structure

```
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
