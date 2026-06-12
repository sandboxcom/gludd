# general_ludd.agent Ansible Collection

Ansible collection for the `general_ludd` agentic SDLC harness.

## Modules

| Module | Description |
|---|---|
| `gludd_ping` | Verify daemon reachability |
| `gludd_model_call` | Run a model generation via the daemon API |
| `gludd_worktree` | Manage git worktrees (present/absent) |
| `gludd_git` | Git operations (commit/branch) |
| `gludd_db` | Todo/resource CRUD via daemon API |
| `gludd_skill` | Render a skill with Jinja2 variables |
| `gludd_mcp_tool` | Invoke an MCP tool (see note below) |

## Roles

| Role | Description |
|---|---|
| `agent_task` | Full task: db-read → worktree → skill → model → gate → commit → db-write |

## Authentication

All modules that contact the daemon accept `daemon_url` and `psk` parameters.
`psk` is never logged (`no_log: true`).

Set `GLUDD_PSK` in the environment — the module_utils shim reads it automatically.

## Note on MCP

`gludd_mcp_tool` returns `not_implemented` per the W3.9 decision (MCP is
honestly fenced until the protocol wiring is completed).
