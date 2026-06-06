# General Ludd Agent

The autonomous coding system with Ansible runners and multi-model AI agents.

## Quick Start

1. Install:
   ```bash
   tar xzf general-ludd-agent-*.tar.gz
   cd general-ludd-agent-*
   sudo ./install.sh
   ```

2. Configure your model credentials:
   ```bash
   mkdir -p ~/.config/general-ludd
   cp config/general-ludd.yml ~/.config/general-ludd/
   # Edit ~/.config/general-ludd/general-ludd.yml with your API keys
   ```

3. Start the daemon:
   ```bash
   gludd daemon
   ```

4. Add a task:
   ```bash
   gludd add "Fix the login bug" --work-type bug_fix
   ```

## CLI Reference

```
gludd daemon       Start the event loop daemon
gludd add          Add a new task
gludd status       Show system status
gludd list         List pending tasks
gludd health       Health check
gludd models       Model operations (search, downloaded)
gludd mcp          MCP server catalog (search, list, info)
gludd skills       Skills catalog (search, list, install)
gludd compute      Compute endpoints (endpoints, register, unregister)
gludd scores       View benchmark scores
gludd leaderboard  View prompt+model leaderboard
gludd deployments  List deployments
gludd log-level    Change log level
gludd local-serve  Start local inference server
```

## Directory Layout

After installation:
```
/usr/local/bin/gludd          The CLI binary
/etc/general-ludd/            System-wide config
/var/log/general-ludd/        Logs
/var/lib/general-ludd/        State data
~/.config/general-ludd/       User config
```

## Providers

### Anthropic / Claude
Set `ANTHROPIC_API_KEY` environment variable and configure a model profile with `credential_alias: ANTHROPIC_API_KEY`.

### OpenAI
Set `OPENAI_API_KEY` environment variable and configure a model profile with `credential_alias: OPENAI_API_KEY`.

### Z.AI / GLM
Set `ZAI_API_KEY` environment variable and configure a model profile with `credential_alias: ZAI_API_KEY`.

### Azure
Set `ARM_CLIENT_ID`, `ARM_CLIENT_SECRET`, `ARM_SUBSCRIPTION_ID`, `ARM_TENANT_ID` for infrastructure deployment.

## MCP Servers

Discover and install MCP servers:
```bash
gludd mcp search github
gludd mcp info github
gludd mcp list
```

## Skills

Discover and install AI skills:
```bash
gludd skills search security
gludd skills list
gludd skills install tdd-discipline
```

## Compute Endpoints

Register remote compute endpoints for distributed inference:
```bash
gludd compute register --id gpu-1 --url http://gpu:8000 --model llama-7b
gludd compute endpoints
gludd compute unregister gpu-1
```
