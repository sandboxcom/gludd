# MCP Server Configuration

This directory holds per-server YAML configs for the
[Model Context Protocol](https://modelcontextprotocol.io/) servers gludd's
daemon connects to at startup. Each file under this directory is read by
`daemon.py` and merged into the live `MCPClient` configuration.

Canonical runtime types:

- `src/general_ludd/mcp/config.py` — `MCPServerConfig` (the parsed YAML shape)
- `src/general_ludd/mcp/client.py` — `MCPClient` (the connection manager)
- `src/general_ludd/mcp/transport.py` — `MCPStdioClient` (stdio transport)
- `src/general_ludd/mcp/registry.py` — `MCPToolRegistry` (tool catalog)

---

## Connecting an MCP server

Each top-level key under `servers` is the **server_id** — a short, unique
identifier used by `MCPClient.call_tool(server_id, tool_name, args)` and
surfaced in the daemon's `/environment/tools` endpoint.

```yaml
servers:
  <server_id>:
    # REQUIRED: exactly one of `command` (stdio) or `url` (HTTP)
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem@2026.7.10"]
    args: ["/tmp"]
    # url: https://my-mcp-server.example.com/mcp

    # OPTIONAL fields (defaults shown)
    timeout_seconds: 30
    enabled: true
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"      # values may reference env vars
    env_aliases:
      API_KEY: GITHUB_TOKEN                 # alias one env var as another
    optional_env_aliases: []                # don't fail startup if these are unset
    project_id: null                        # restrict to a single project
```

At daemon startup the config loader (`daemon.py:load_config`) walks every
`*.yml` in this directory, deep-merges all `servers:` maps, and passes the
result to `MCPClient`. A server with `enabled: false` is loaded but skipped
during `start_all()` — useful for keeping a config checked in without
spawning the subprocess.

---

## Field reference

| Field | Type | Default | Purpose |
|---|---|---|---|
| `command` | list[str]\|null | null | stdio transport: argv to spawn. Mutually exclusive with `url`. |
| `args` | list[str] | `[]` | Extra argv appended after `command`. |
| `url` | str\|null | null | HTTP transport: the MCP endpoint URL. Mutually exclusive with `command`. |
| `env` | dict[str, str] | `{}` | Environment variables passed to the spawned subprocess. Values may use `${VAR}` for substitution from the daemon's own environment. |
| `env_aliases` | dict[str, str] | `{}` | Rename one env var to another for the subprocess (e.g. expose `GITHUB_TOKEN` as `API_KEY`). |
| `optional_env_aliases` | set[str] | `{}` | Like `env_aliases` but missing vars do not fail startup. |
| `timeout_seconds` | float | `30.0` | Per-call timeout. Must be positive. |
| `enabled` | bool | `true` | If `false`, the server is loaded but not started. |
| `project_id` | str\|null | null | Restricts the server to a single gludd project scope. |

A `MCPServerConfig` must have **either** `command` **or** `url`. Both empty
raises a validation error at config load time.

### Transports

| Transport | When to use | How it speaks |
|---|---|---|
| **stdio** (`command` set) | Local subprocess servers (npm packages, Python CLIs, custom binaries) | JSON-RPC over the subprocess's stdin/stdout |
| **HTTP** (`url` set) | Remote servers, sidecar containers, anything behind an HTTP endpoint | HTTP POST of JSON-RPC payloads |

---

## How MCP servers connect to agents

The wiring chain at daemon startup:

1. **Config load.** `daemon.py:load_config` reads every YAML in this
   directory and deep-merges them into `startup_config["mcp_servers"]`.
2. **Registry build.** `daemon.py` constructs a single `MCPToolRegistry`
   that will hold every tool from every connected server.
3. **Client construction.** A single `MCPClient(configs, registry,
   secrets_mgr)` is built. One client manages **all** server connections.
4. **Startup.** `MCPClient.start_all()` iterates each `enabled` server:
   - For stdio servers: spawn the subprocess (`MCPStdioClient.start`),
     then call `list_tools()` and register each tool in the shared
     registry under `<server_id>:<tool_name>`.
   - For HTTP servers: open the transport and proceed similarly.
   - If any server fails to start, **every previously started server is
     stopped and the failure is re-raised** — no orphaned subprocesses.
5. **Event loop wiring.** The live `MCPClient` is handed to the
   `EventLoop`. Tool calls from agents are dispatched via
   `MCPClient.call_tool(server_id, tool_name, arguments)`, which looks up
   the transport and forwards the call.
6. **Capability gate.** Every tool call is checked against the calling
   agent's `PermissionSpec` before the transport is invoked — a subagent
   cannot reach a tool its STS token does not permit.

Built-in servers (Python handlers running in-process, not subprocesses) can
also be registered via `MCPClient.register_builtin(server_id, tools,
handler)` — see `src/general_ludd/mcp/builtins.py`.

---

## Example: filesystem MCP server

A local stdio server exposing a directory tree as MCP tools. This is what
`example.yml` in this directory configures:

```yaml
servers:
  filesystem:
    command: ["npx", "-y", "@modelcontextprotocol/server-filesystem@2026.7.10"]
    args: ["/tmp"]                       # expose /tmp as the root
    timeout_seconds: 30
    enabled: true
```

Agents can now call `filesystem:read_file`, `filesystem:write_file`,
`filesystem:list_directory`, etc. (whatever tools the npm package exposes).

The exact npm version is intentional. The transport rejects bare package names,
tags, and ranges so a later publish cannot silently change executable code.
The pin tracks the [official npm release](https://www.npmjs.com/package/@modelcontextprotocol/server-filesystem),
while the long-running [cross-platform startup report](https://github.com/modelcontextprotocol/servers/issues/1107)
shows why reproducible launcher and package resolution matter operationally.

To expose multiple directories, either pass extra `args` (if the server
supports it) or define multiple `servers:` entries with different
`server_id`s.

---

## Example: GitHub MCP server

Connects the official GitHub MCP server, with the token pulled from the
daemon's environment so the secret never lands in the YAML:

```yaml
servers:
  github:
    command: ["npx", "-y", "@modelcontextprotocol/server-github"]
    env:
      GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
    timeout_seconds: 60
    enabled: true
```

Start the daemon with `GITHUB_PERSONAL_ACCESS_TOKEN` exported in the
shell; the `${VAR}` substitution happens at config load. Tools like
`github:create_issue`, `github:get_file_contents`, and
`github:search_repositories` become available to any agent whose
`PermissionSpec` permits the `github` server.

---

## Example: HTTP transport

For a remote or sidecar MCP server (e.g. a containerized custom server):

```yaml
servers:
  my-remote:
    url: https://mcp.internal.example.com/mcp
    timeout_seconds: 45
    enabled: true
    project_id: analytics                # restrict to one project
```

---

## Disabling a server without deleting the config

```yaml
servers:
  experimental-v2:
    command: ["./bin/mcp-experimental"]
    enabled: false                       # loaded but not started
```

Useful for keeping seasonal or in-development servers checked in without
spawning them on every daemon boot.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `MCPServerConfig must have either command or url` | Both fields are empty — set one. |
| Server fails to start, others are stopped | One server threw during `start_all()`. Check the failing server's binary path / URL / auth. |
| `timeout_seconds must be positive` | Set `timeout_seconds` to a positive float. |
| Tool not visible to agents | Server disabled (`enabled: false`), or the agent's `PermissionSpec` does not include the server_id. |
| Secret leaked into YAML | Use `${VAR}` substitution and pass the actual value via the daemon's environment; for OpenBao-managed secrets, route through `secrets_mgr` rather than `env`. |
