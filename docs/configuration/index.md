# Configuration Reference

Configuration files and their purposes.

## Contents

This directory does not yet hold split-out configuration pages. The full
configuration reference is [docs/CONFIG_REFERENCE.md](../CONFIG_REFERENCE.md).

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
```text
<project-repo>/.general-ludd/agent_config.yml
```

See [Profile Configuration Guide](../profiles.md) for details.

---

[Back to Documentation Index](../index.md)
