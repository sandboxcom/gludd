# General Ludd Agent

**The black swan agentic coding system.**

General Ludd Agent is an autonomous coding system with multi-model AI agents,
Ansible-based task execution, and multi-project isolation.

## Quick Start

### Requirements

- Linux (systemd) or macOS
- PostgreSQL 14+
- At least one model provider API key (OpenAI, Z.AI, OpenRouter, etc.)

### Installation

```bash
# 1. Unpack the tarball
tar xzf general-ludd-agent-*.tar.gz
cd general-ludd-agent-*/

# 2. Run the installer (requires root on Linux)
sudo ./install.sh

# 3. Configure your model provider
#    Edit the main config file:
sudo vim /etc/general-ludd/general-ludd.yml

#    At minimum, set your API key in the environment file:
sudo vim /etc/general-ludd/env

# 4. Start the daemon
sudo systemctl start general-ludd

# 5. Verify it's running
gludd health
```

### First Task

```bash
# Add a coding task
gludd add "Implement user authentication" --priority high

# Check status
gludd status

# List all tasks
gludd list
```

## Configuration

The main config file is `/etc/general-ludd/general-ludd.yml`. See
`docs/configuration.md` for full reference.

Key sections:

| Section | Purpose |
|---------|---------|
| `model_routing` | Which AI model to use for each task type |
| `database` | PostgreSQL connection settings |
| `agents` | Agent definitions and limits |
| `budget` | Spending caps and warnings |

## Documentation

| Document | Description |
|----------|-------------|
| `docs/quickstart.md` | Step-by-step getting started guide |
| `docs/configuration.md` | Full configuration reference |
| `docs/architecture.md` | System architecture overview |

## CLI Reference

```
gludd daemon              Start the daemon
gludd add <title>         Add a task to the queue
gludd status [id]         Show task or system status
gludd list                List all tasks
gludd health              Check daemon health
gludd version             Show version
gludd log-level <level>   Change log level at runtime
```

## Directory Layout

After installation:

```
/etc/general-ludd/
  general-ludd.yml       Main configuration (edit this)
  env                    Environment variables (API keys)
  config/                Model profiles, agent definitions, etc.
  templates/             Prompt templates

/var/log/general-ludd/   Daemon logs
/var/lib/general-ludd/   Runtime state (SQLite if no PostgreSQL)
```

## Support

- Issues: https://github.com/anomalyco/general-ludd/issues
- Docs: See the `docs/` directory in this archive
