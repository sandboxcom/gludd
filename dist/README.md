# General Ludd Agent

Autonomous coding system with multi-model AI agents, Ansible task execution,
and multi-project isolation.

---

## Installation

```bash
tar xzf general-ludd-agent-*.tar.gz
cd general-ludd-agent-*/
sudo ./install.sh
```

Then configure and start:

```bash
# 1. Add your API key
sudo vi /etc/general-ludd/env

# 2. Edit the main config
sudo vi /etc/general-ludd/general-ludd.yml

# 3. Start the daemon
sudo systemctl start general-ludd    # Linux
gludd daemon                          # macOS

# 4. Verify
gludd health
```

## CLI Reference

```
gludd daemon [--config-dir DIR] [--host HOST] [--port PORT] [--log-level LEVEL]
    Start the daemon. Default: 127.0.0.1:8000

gludd add <title> [--queue QUEUE] [--priority PRIORITY] [--project ID]
    Add a task to the queue

gludd status [ID] [--project ID]
    Show task or system status

gludd list [--queue QUEUE] [--status STATUS] [--project ID]
    List tasks

gludd health
    Check daemon health

gludd version
    Show version

gludd log-level <debug|info|warning|error>
    Change log level at runtime

gludd deployments
    List active deployments
```

## Directory Structure After Install

```
/etc/general-ludd/
  general-ludd.yml           Main configuration file (EDIT THIS)
  env                        Environment variables (API keys, DATABASE_URL)
  config/
    model_routing.yml        Default model routing (used if general-ludd.yml
                             does not override it)
    model_profiles/          One YAML file per model provider
      zai_example.yml          Z.AI / GLM models
      openai_example.yml       OpenAI GPT models
      openrouter_example.yml   OpenRouter gateway
      vllm_example.yml         Local vLLM server
      llamacpp_example.yml     Local llama.cpp server
    agents/
      default_agents.yml     Agent definitions (build, plan, explore, general)
    binary_paths.yml         Paths to terraform, podman, vault, etc.
    openbao/
      default.yml            OpenBao / Vault secrets config
    ansible/
      isolation.yml          Process isolation settings
    mcp_servers/
      example.yml            MCP server connection examples
    tasks/
      example_tasks.yml      Reusable task templates
    skills/
      codify_directive.md    Skill: port coding directives
      return_review.md       Skill: review task returns
    infra/
      providers.yml          Cloud GPU provider pricing (reference)
    examples/
      user_config_example.yml    Example user config
      agent_config_example.yml   Example per-project agent config
  templates/
    prompts/                 Jinja2 prompt templates

/var/log/general-ludd/       Daemon logs
/var/lib/general-ludd/       Runtime state
```

## Configuration Guide

The daemon loads configuration from **multiple files** in the config directory.
You pass the config directory with `--config-dir` (default: `/etc/general-ludd/`).

Loading order (highest priority wins):
1. Environment variables (in `/etc/general-ludd/env`)
2. `general-ludd.yml` (main config)
3. Individual config files in `config/` subdirectory
4. Built-in defaults

### general-ludd.yml — Main Config

This is the **single file you should edit**. It contains all high-level settings.
Every section is documented inline. Key sections:

| Section | What it controls | Required? |
|---------|-----------------|-----------|
| `model_routing` | Which AI model for each task type | Yes |
| `database` | PostgreSQL connection | For production |
| `agents` | Agent behavior defaults | No |
| `process_isolation` | Container sandboxing | No |
| `budget` | Spending limits | Recommended |
| `secrets` | OpenBao/Vault backend | For secret management |
| `quality_gates` | Coverage thresholds | No |
| `context_compaction` | Token window management | No |
| `hot_reload` | Auto-reload on file change | No |
| `metrics` | Agent cost tracking | No |
| `local_inference` | Local vLLM/llama.cpp | For local models |
| `git_automation` | Auto branch/commit/merge | No |
| `projects` | Multi-project allocation | No |
| `compute_endpoints` | GPU endpoint routing | No |
| `model_registry` | HuggingFace cache dir | No |
| `mcp_servers` | MCP tool connections | No |
| `skills` | Skill discovery paths | No |
| `rules` | Custom routing rules | No |
| `validation` | Test gap analysis | No |
| `log_auditing` | Log scanning | No |

### config/model_profiles/*.yml — Model Provider Profiles

Each file defines one model provider. The daemon uses these to make AI API calls.

**To use Z.AI (default):**
Set `ZAI_API_KEY` in `/etc/general-ludd/env`. The `zai_example.yml` profile is
preconfigured to use GLM-5.1 via the OpenAI-compatible API.

**To use OpenAI:**
```yaml
# config/model_profiles/openai.yml  (copy from openai_example.yml)
model_profile_id: openai_gpt4
provider: openai
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: gpt-4
credential_alias: OPENAI_API_KEY    # env var name for the key
context_window: 128000
max_input_tokens: 120000
max_output_tokens: 8000
cost_per_input_token: 0.03          # USD per 1K tokens
cost_per_output_token: 0.06
run_budget_usd: 50.0                # per-task limit
enabled: true
```

Then update `general-ludd.yml`:
```yaml
model_routing:
  default_profile: openai_gpt4
```

**To use OpenRouter:**
Same pattern — copy `openrouter_example.yml`, set `OPENROUTER_API_KEY`.

**To use a local model (vLLM):**
```yaml
# config/model_profiles/vllm_local.yml
model_profile_id: vllm_local
provider: openai
provider_package: langchain-openai
provider_class_hint: ChatOpenAI
model_name: my-model
credential_alias: VLLM_API_KEY      # can be anything for local
api_base_alias: VLLM_BASE_URL       # http://127.0.0.1:8080/v1
context_window: 4096
enabled: true
```

Set `VLLM_API_KEY=none` and `VLLM_BASE_URL=http://127.0.0.1:8080/v1` in env.

**Credential resolution order:**
1. Environment variable matching `credential_alias` (e.g., `OPENAI_API_KEY`)
2. OpenBao secret at `secret/general-ludd/<credential_alias>`
3. Error if not found

### config/agents/default_agents.yml — Agent Definitions

Four built-in agents, each with specific permissions:

| Agent | Role | Can edit | Can run commands | Can read | Max concurrent |
|-------|------|----------|-----------------|----------|---------------|
| `build` | Primary coder | Yes | Yes | Yes | 1 |
| `plan` | Planning/analysis | No | No | Yes | 1 |
| `explore` | Code search | No | No | Yes | 5 |
| `general` | Multi-purpose | Yes | Yes | Yes | 3 |

Each agent has a `model_profile` field pointing to a model profile ID.
Override per-agent in `general-ludd.yml`:
```yaml
agents:
  default_agent: build
  max_concurrent: 4
```

### config/binary_paths.yml — External Tool Paths

Overrides paths to external binaries. Defaults to PATH lookup.

```yaml
binary_paths:
  terraform: "terraform"         # or /usr/local/bin/terraform
  podman: "podman"               # or /usr/bin/podman
  docker: "docker"
  openbao: "bao"                 # or "vault"
  ansible_playbook: "ansible-playbook"
  git: "git"
  uv: "uv"
```

### config/openbao/default.yml — Secrets Backend

```yaml
mode: auto                      # auto (local container), external, disabled
external_url: null              # http://bao:8200 if mode=external
external_token: null            # root token if mode=external
local_image: ghcr.io/openbao/openbao
kv_mount: secret
auth_method: approle
```

### config/ansible/isolation.yml — Process Isolation

```yaml
process_isolation:
  enabled: false                # Set true for container sandboxing
  executable: podman            # podman or docker
  hide_paths: []                # Paths to hide from playbooks
  show_paths: []                # Paths to expose to playbooks
  ro_paths: []                  # Read-only paths
  block_local_tools: []         # Tools to block (bash, python, git, etc.)
```

### config/mcp_servers/example.yml — MCP Connections

```yaml
servers:
  filesystem:
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
    args: ["/tmp"]
    timeout_seconds: 30
    enabled: true
```

### config/tasks/example_tasks.yml — Task Templates

```yaml
tasks:
  - name: implement_feature
    description: "Implement the core feature"
    target_agent: build
    dependencies: []
    acceptance_criteria:
      - "All unit tests pass"
```

### /etc/general-ludd/env — Environment Variables

```bash
# API Keys (at least one required)
ZAI_API_KEY=your-zai-key
ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4
# OPENAI_API_KEY=sk-your-key
# OPENROUTER_API_KEY=your-key

# Database
DATABASE_URL=postgresql://gludd:password@localhost:5432/gludd

# HuggingFace (for local model download)
# HF_TOKEN=your-hf-token
```

## Runtime API

The daemon exposes REST endpoints for management:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/healthz` | Health check |
| POST | `/api/todos` | Add a task |
| GET | `/api/todos` | List tasks |
| GET | `/api/todos/{id}` | Get task status |
| GET | `/api/status` | System status |
| GET | `/api/deployments` | Active deployments |
| POST | `/admin/log-level` | Change log level |
| POST | `/admin/reload` | Reload config/templates/playbooks |
| GET | `/admin/models` | List model profiles |
| POST | `/admin/models` | Add model profile |
| DELETE | `/admin/models/{id}` | Remove model profile |
| POST | `/admin/models/search` | Search HuggingFace |
| GET | `/admin/models/downloaded` | List downloaded models |
| GET | `/admin/templates` | List prompt templates |
| POST | `/admin/templates/refresh` | Reload templates |
| GET | `/admin/playbooks` | List playbooks |
| POST | `/admin/playbooks/refresh` | Reload playbooks |
| GET | `/admin/hooks` | List webhooks |
| POST | `/admin/hooks` | Register webhook |
| DELETE | `/admin/hooks/{id}` | Remove webhook |
| GET | `/admin/workers` | List connected workers |
| POST | `/admin/workers/ping` | Ping all workers |
| GET | `/admin/agents` | List running agents |
| GET | `/admin/agents/{id}` | Agent detail |
| GET | `/admin/metrics/cost` | Cost estimate |
| GET | `/admin/metrics/report` | Full metrics report |
| POST | `/admin/projects` | Add project |
| DELETE | `/admin/projects/{id}` | Remove project |
| PUT | `/admin/projects/{id}/weight` | Set project weight |
| POST | `/admin/projects/rebalance` | Rebalance weights |
| GET | `/admin/projects` | List projects |
| GET | `/admin/compute/utilization` | Utilization report |
| GET | `/admin/compute/endpoints` | List compute endpoints |

## Features

| Feature | How to Use |
|---------|-----------|
| **Model routing** | Set `model_routing.default_profile` in general-ludd.yml |
| **Multiple providers** | Add profile files in config/model_profiles/, set credential_alias |
| **Budget caps** | Set `budget.max_usd` in general-ludd.yml |
| **Process isolation** | Set `process_isolation.enabled: true` in general-ludd.yml |
| **Multi-project** | POST to `/admin/projects` or define in `projects:` section |
| **Agent metrics** | Automatic — view at `/admin/metrics/report` |
| **Cost tracking** | Automatic — view at `/admin/metrics/cost` |
| **Hot reload** | Edit config files, POST `/admin/reload`, or set `hot_reload.enabled` |
| **Local inference** | Set `local_inference.enabled: true`, configure vLLM/llamacpp profile |
| **HuggingFace models** | POST `/admin/models/search` to find, download locally for inference |
| **Quality gates** | Set `quality_gates.python_line_coverage_min_percent` |
| **Context compaction** | Set `context_compaction.max_tokens` and threshold |
| **Secrets management** | Set `secrets.backend: openbao`, configure config/openbao/default.yml |
| **MCP servers** | Add servers in config/mcp_servers/ or `mcp_servers:` section |
| **Custom routing rules** | Add rules in `rules:` section of general-ludd.yml |
| **Git automation** | Set `git_automation.enabled: true` |
| **Log auditing** | Set `log_auditing.enabled: true` |
| **Validation** | Set `validation.enabled: true` |
| **Compute routing** | Define `compute_endpoints:` or register via API |
| **Skills** | Add .md files to config/skills/, set `skills.directories` |
| **Task templates** | Add YAML files to config/tasks/ |

## Documentation

| File | Description |
|------|-------------|
| `docs/quickstart.md` | Step-by-step getting started guide |
| `docs/configuration.md` | Full configuration reference |
| `docs/architecture.md` | System architecture overview |

## Support

- Issues: https://github.com/anomalyco/general-ludd/issues
