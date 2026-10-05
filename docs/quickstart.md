# Quick Start Guide

## Prerequisites

Before installing General Ludd Agent, make sure you have:

1. **Python 3.11+** and [uv](https://docs.astral.sh/uv/) (recommended)
2. **SQLite** (default, zero-config — no server required)
3. **A model provider API key** (one of):
   - Z.AI (GLM models) — primary/default
   - OpenAI (GPT-4, etc.)
   - Anthropic (Claude)
   - OpenRouter (multi-provider gateway)
   - A local model server (vLLM, llama.cpp)

## Installation

### From Tarball

```bash
tar xzf gludd-*.tar.gz
cd gludd-*/
sudo ./install.sh
```

The installer:
- Copies the `gludd` binary to `/usr/local/bin/`
- Installs config files to `/etc/general-ludd/`
- Installs the systemd service unit
- Runs pre-flight checks

### From Source

```bash
git clone https://github.com/sandboxcom/gludd.git
cd gludd
make init        # set up dirs + install deps
make bootstrap   # init + lint + test + healthcheck
```

## Configuration

### Step 1: Set Your API Key

Edit `/etc/general-ludd/env` (or set env vars) for your provider:

```bash
# For Z.AI (default)
ZAI_API_KEY=your-key-here
ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# For OpenAI
OPENAI_API_KEY=sk-your-key-here

# For Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-your-key-here
```

### Step 2: Database (SQLite — zero config)

The default database is **SQLite** stored in `~/.local/share/general-ludd/gludd.db`.
No setup is required. The daemon is SQLite-only; any non-SQLite URL is refused at startup.

### Step 3: Select a Model Profile

> **Read this or nothing will work.** The daemon discovers its config directory as
> `$GLUDD_CONFIG_DIR` → `~/.config/general-ludd` → `/etc/general-ludd`. **The repo's
> own `config/` directory is NOT on that path** — it holds examples to copy, not
> files the daemon reads. See "The silent no-op trap" below.

The example profiles live in the repo at `config/model_profiles/`. Copy the one you
want into the discovery path and edit it:

```bash
mkdir -p ~/.config/general-ludd/model_profiles
cp config/model_profiles/openai_example.yml ~/.config/general-ludd/model_profiles/openai.yml
```

Then update `~/.config/general-ludd/general-ludd.yml`:

```yaml
model_routing:
  default_profile: openai_gpt4
```

Alternatively, point the daemon straight at the repo's config tree:

```bash
export GLUDD_CONFIG_DIR="$PWD/config"
```

### The silent no-op trap (read before filing a bug)

If you start the daemon **from a repo checkout without `GLUDD_CONFIG_DIR` set**, it
finds no `model_profiles/`. No profiles load, the model gateway stays `None`, and the
dispatcher silently falls back to a **no-op executor**. Every task you dispatch then
comes back `status="completed"` with **empty output**, **no warning is logged**, and
`/healthz` and `/readyz` still return 200/ready.

Symptom: **agents "succeed" instantly and do nothing.** Fix: set `GLUDD_CONFIG_DIR`,
or install the config into `~/.config/general-ludd/`.

Verify before you submit work:

```bash
gludd models router-status   # must list an active profile — an empty list means you are in the trap
```

### Step 4: Start the Daemon

```bash
uv run gludd daemon --port 8000
```

Or via systemd (tarball install):

```bash
sudo systemctl start general-ludd
sudo systemctl status general-ludd
```

Check health:

```bash
uv run gludd health
# or:  curl http://localhost:8000/healthz
```

## First Task

```bash
# Add a coding task
uv run gludd todo add "Refactor the authentication module" --queue core

# Watch it process
uv run gludd todo list

# Check a specific task
uv run gludd status <task-id>
```

## What Happens Next

1. The daemon's event loop picks up the task (claim phase)
2. It dispatches the task to the Ansible runner (dispatch phase) with the appropriate model
3. The Ansible runner invokes `general_ludd.agent` roles and modules against the AI model
4. Results are reviewed with (optionally) a different model (review phase)
5. The change is reconciled — approved, retried, or rejected (reconcile phase)

## Checking Facts and Observability

The daemon exposes a live facts snapshot that Ansible playbooks can consume:

```bash
# Full daemon state snapshot (work/todos/models/history/messages/metrics/traces)
curl -H "Authorization: Bearer $GLUDD_AUTH_PSK" http://localhost:8000/api/facts

# Metrics only
curl -H "Authorization: Bearer $GLUDD_AUTH_PSK" http://localhost:8000/api/metrics

# Recent execution traces
curl -H "Authorization: Bearer $GLUDD_AUTH_PSK" http://localhost:8000/api/traces
```

In a playbook, use `gludd_facts` to branch on live data:

```yaml
- name: Load daemon facts
  general_ludd.agent.gludd_facts:
  # ansible_facts.gludd is now populated

- name: Only proceed when there is backlog
  ansible.builtin.debug:
    msg: "Backlog: {{ gludd.todos.backlog_size }}"
  when: gludd.todos.backlog_size | int > 0
```

## Dogfood

Run the daemon on its own codebase:

```bash
make dogfood
```

## Searching and Using MCP Servers

```bash
# Search for MCP tools
uv run gludd mcp search filesystem

# View server details
uv run gludd mcp info filesystem

# Register in config/mcp_servers/ (see dist/README.md for full example)
```

## Searching and Using Skills

```bash
# Find a skill
uv run gludd skills search tdd

# Install it locally
uv run gludd skills install tdd-discipline
```

## Next Steps

- Read `docs/CONFIG_REFERENCE.md` — the authoritative config reference, including the
  **experimental flags you must not enable** (`GLUDD_WRITER_MODE=subprocess`,
  `pipeline.enabled`)
- Read `docs/configuration.md` for full config options
- Read `docs/architecture.md` to understand how the system works
- Read `docs/RELEASE_RUNBOOK.md` if you are cutting a release
- Explore `config/` for model profiles, agent definitions, etc. (examples to copy —
  the daemon does not read this directory)
- Run `make help` to see all available make targets
