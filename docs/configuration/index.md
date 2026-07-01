# Configuration Reference

Configuration files and their purposes.

## Contents

| Document | Description |
|----------|-------------|
| [Configuration](configuration.md) | Configuration reference |

## Configuration Files

| File | Purpose |
|------|---------|
| `config/general-ludd.yml` | Main configuration (model routing, database, agents, budget) |
| `config/model_routing.yml` | Model routing with fallback chains |
| `config/model_profiles/*.yml` | Individual model profiles (API keys, costs, limits) |
| `config/openbao/default.yml` | OpenBao secrets backend |
| `config/ansible/isolation.yml` | Process isolation settings |
| `config/mcp_servers/example.yml` | MCP server connections |
| `config/binary_paths.yml` | External binary paths |

## Per-Project Override

Projects can override routing and profile selection via:
```
<project-repo>/.general-ludd/agent_config.yml
```

See [Profile Configuration Guide](../profiles/profiles.md) for details.

---

[Back to Documentation Index](../index.md)
