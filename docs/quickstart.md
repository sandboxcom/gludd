# Quick Start Guide

## Prerequisites

Before installing General Ludd Agent, make sure you have:

1. **PostgreSQL 14+** running and accessible
2. **A model provider API key** (one of):
   - Z.AI (GLM models)
   - OpenAI (GPT-4, etc.)
   - OpenRouter (multi-provider gateway)
   - A local model server (vLLM, llama.cpp)

## Installation

### From Tarball

```bash
tar xzf general-ludd-agent-*.tar.gz
cd general-ludd-agent-*/
sudo ./install.sh
```

The installer:
- Copies the `gludd` binary to `/usr/local/bin/`
- Installs config files to `/etc/general-ludd/`
- Installs the systemd service unit
- Runs pre-flight checks

### From Source

```bash
git clone <repo-url>
cd general-ludd-agent
make init
```

## Configuration

### Step 1: Set Your API Key

Edit `/etc/general-ludd/env` and add your provider's API key:

```bash
# For Z.AI
ZAI_API_KEY=your-key-here
ZAI_BASE_URL=https://open.bigmodel.cn/api/paas/v4

# For OpenAI
OPENAI_API_KEY=sk-your-key-here
```

### Step 2: Configure the Database

Edit `/etc/general-ludd/general-ludd.yml`:

```yaml
database:
  host: localhost
  port: 5432
  name: gludd
  user: gludd
  password: your-password
```

Or set the `DATABASE_URL` environment variable:

```bash
export DATABASE_URL=postgresql://gludd:password@localhost:5432/gludd
```

Create the database:

```bash
sudo -u postgres createdb gludd
sudo -u postgres createuser gludd
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE gludd TO gludd;"
```

### Step 3: Select a Model Profile

Model profiles are in `/etc/general-ludd/config/model_profiles/`. The default
is `zai_coder`. To use a different provider, copy the example profile and edit it:

```bash
cp /etc/general-ludd/config/model_profiles/openai_example.yml \
   /etc/general-ludd/config/model_profiles/openai.yml
```

Then update `general-ludd.yml`:

```yaml
model_routing:
  default_profile: openai_gpt4
```

### Step 4: Start the Daemon

```bash
sudo systemctl start general-ludd
sudo systemctl status general-ludd
```

Check health:

```bash
gludd health
```

## First Task

```bash
# Add a coding task
gludd add "Refactor the authentication module" --priority high

# Watch it process
gludd list

# Check a specific task
gludd status <task-id>
```

## What Happens Next

1. The daemon's event loop picks up the task
2. It dispatches the task to the configured agent (default: `build`)
3. The agent calls the AI model to generate a plan
4. Ansible playbooks execute the plan
5. Results are reviewed and returned

## Next Steps

- Read `docs/configuration.md` for full config options
- Read `docs/architecture.md` to understand how the system works
- Explore `/etc/general-ludd/config/` for model profiles, agent definitions, etc.
