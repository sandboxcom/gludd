# Onboarding MCP services in gludd

This guide explains how to add a Model Context Protocol (MCP) server so that
**gludd agents can use its tools**, and — separately — how to make an MCP server
available to the **Claude Code session** working in this repository.

There are two distinct surfaces. They are independent; onboard whichever (or
both) you need.

| Surface | Who uses it | Where it is configured |
|---|---|---|
| **A. Claude Code (this repo's interactive agent)** | The Claude Code CLI/IDE session opened in `gludd/` | `/.mcp.json` at the repo root |
| **B. gludd runtime (the daemon's agents)** | Ansible-role agents the daemon dispatches | `src/general_ludd/mcp/catalog.py` `_KNOWN_SERVERS` + the daemon's MCP server config |

---

## A. Make a server available to Claude Code (project `.mcp.json`)

Claude Code reads project-scoped MCP servers from `/.mcp.json` at the repository
root. Shape:

```json
{
  "mcpServers": {
    "<server-name>": {
      "command": "npx",
      "args": ["-y", "<package>@<version>"],
      "env": { "SOME_TOKEN": "..." }
    }
  }
}
```

- `command` must be an executable on `PATH` (`npx`, `uvx`, `node`, `python`, an
  absolute binary path, …).
- Pin the package version (`@<version>`) for reproducibility and supply-chain
  safety — an unpinned `npx -y pkg` fetches whatever is latest at launch.
- Put only non-secret values inline; for secrets prefer your shell environment
  and reference them, or use a local (gitignored) override.

**Trust / enablement.** A server listed in `.mcp.json` is *disabled by default*
until the project is trusted. Add the server name to `enabledMcpjsonServers`
for this project in your user settings (`~/.claude/settings.local.json` under
`projects["…/gludd"]`), or accept the trust prompt the first time Claude Code
loads the project. Open `/mcp` in Claude Code to confirm it connected.

A working example already lives in this repo's `/.mcp.json`:
`codebase-memory-mcp` (see the worked example below).

---

## B. Onboard a server into the gludd runtime (used by daemon agents)

gludd has two layers here: a **catalog** (discovery/metadata) and the **client**
that actually connects a server so its tools can be called by agents.

### Step 1 — Add a catalog entry (`_KNOWN_SERVERS`)

Edit `src/general_ludd/mcp/catalog.py` and add an `MCPCatalogEntry` to the
`_KNOWN_SERVERS` dict. The model:

```python
class MCPCatalogEntry(BaseModel):
    server_name: str            # the dict key; non-empty
    display_name: str = ""
    description: str = ""
    source: str = ""            # "official" = curated by gludd (may carry a command)
    url: str = ""               # for HTTP/SSE servers
    command: list[str] = []     # the stdio launch argv
    env_aliases_needed: list[str] = []   # env var names the server requires
    tags: list[str] = []
    downloads: int = 0
```

Rules:

- **Pin the version.** Every `command` that uses a remote-fetch launcher
  (`npx`/`npm`/`pnpm`/`yarn`/`bunx`/`uvx`) **must** carry an explicit
  `@<version>` on the package spec. The launch gate
  (`mcp/transport.py` `_validate_launch_command`, which version-checks the
  package spec) refuses to spawn an unpinned spec — fail-closed — so an
  unpinned entry is a runtime break, not a style nit.
- **`source="official"`** marks the entry as curated-by-gludd and is what lets
  it carry an executable `command` (entries discovered from third-party network
  registries are hardened so they cannot smuggle a command).
- **`env_aliases_needed`** lists the env var names the server needs (e.g.
  `GITHUB_PERSONAL_ACCESS_TOKEN`). Leave empty if it needs no secrets.

Adding an entry is pure Python — no manifest regeneration, no build step. It is
immediately visible via the catalog surface:

- CLI: `gludd mcp list`, `gludd mcp search <q>`, `gludd mcp info <name>`
- Daemon API: `GET /admin/mcp/catalog/servers`,
  `GET /admin/mcp/catalog/servers/{name}`, `POST /admin/mcp/catalog/search`
  (all under `/admin/*`, so the PSK middleware protects them).

Add a guard test next to `tests/unit/test_mcp_catalog_known_servers.py`
asserting your entry loads (`MCPCatalog().get_server("<name>")`) and is
version-pinned.

### Step 2 — The launch-security model

When the client spawns a stdio server it runs `command` through
`mcp/transport.py` `_validate_launch_command`:

- The executable (`command[0]`) must be in the allow-list:
  `{npx, npm, pnpm, yarn, bunx, uvx, python, python3, node}`, and must resolve
  via `shutil.which()` (or be an absolute path that exists).
- Remote-fetch launchers must be version-pinned (see Step 1).
- The escape hatch `GLUDD_MCP_ALLOW_ANY_EXEC=1` lifts the allow-list — use it
  only for a deliberately-vetted local binary, never in production by default.

Secrets are **not** placed in the catalog. Instead `env_aliases_needed` names
the variables; gludd resolves them from the environment/secret store and
injects them into the child process's environment at spawn time, so the token
never lives in the catalog or the manifest.

### Step 3 — Connect the server (so agents can call it)

The catalog is discovery metadata. To make a server *live*, it must be present
in the daemon's MCP **server configuration** (`mcp/config.py` `MCPServerConfig`),
which `mcp/client.py` `MCPClient.start_all()` reads to spawn each configured
server and enumerate its tools (`tools/list`). Reuse the catalog entry's
`command` for the `MCPServerConfig.command`.

> Adding a server to `_KNOWN_SERVERS` alone makes it *discoverable* but does not
> auto-start it; it must also be enabled in the server configuration the client
> loads. Keep the two in sync (same pinned `command`).

### Step 4 — How agents invoke the tools

Once a server is connected, its tools are reachable **in-process** by the
daemon's event loop:

- **Model-driven tool calls** are the live path. The two-phase generation loop
  (`execution/tool_loop.py`) pulls the connected servers' tool schemas from the
  `MCPToolRegistry`, binds them to the model, and executes the tool calls the
  model emits via `MCPClient.call_tool(server_id, tool_name, args)`. The
  registry double-checks that the tool is registered to the named server before
  dispatching (anti-hijack).
- The event-loop dispatcher is wired in `daemon.py`
  (`build_event_loop_mcp_dispatcher` → `DynamicDispatcher` with an `mcp_handler`).
  The capability lattice gates this: an agent role must hold the `mcp` dispatch
  capability, and an unbound (`role=None`) dispatcher is deny-by-default. Note
  the dispatcher's `mcp_handler` is only non-null when an `MCPClient` actually
  started (i.e. at least one enabled server in config); otherwise MCP dispatch
  is a no-op.

> **Not yet wired: Ansible → MCP.** The Ansible module
> `general_ludd.agent.gludd_mcp_tool` is an honest placeholder — it returns
> `not_implemented=true` and does **not** call tools through the daemon (there
> is no agent-facing daemon HTTP route for MCP tool calls today; per decision
> W3.9 MCP is "honestly fenced"). Do not document it as functional. Roles that
> need MCP today must go through the in-process model/tool-call path above.

---

## Worked example: `codebase-memory-mcp`

[`codebase-memory-mcp`](https://github.com/DeusData/codebase-memory-mcp)
(DeusData) is a structural code-intelligence server: it indexes a repository
into a persistent SQLite knowledge graph (functions, classes, call chains,
routes) via tree-sitter, giving agents fast "where is X / what calls Y" codebase
memory. It is a single static binary speaking stdio MCP, needs **no API key or
embedding model**, and stores its graph under `CBM_CACHE_DIR`
(default `~/.cache/codebase-memory-mcp`). The npm/PyPI launchers are thin
wrappers that fetch the native binary on first run (so first launch needs
network, or pre-install the binary on offline hosts).

**Claude Code (already wired in this repo, `/.mcp.json`):**

```json
{ "mcpServers": { "codebase-memory-mcp": {
  "command": "npx", "args": ["-y", "codebase-memory-mcp@0.8.1"], "env": {} } } }
```

**gludd catalog (already added, `_KNOWN_SERVERS["codebase-memory"]`):**

```python
"codebase-memory": MCPCatalogEntry(
    server_name="codebase-memory",
    display_name="Codebase Memory",
    description="Structural code-intelligence … via tree-sitter …",
    source="official",
    command=["npx", "-y", "codebase-memory-mcp@0.8.1"],
    env_aliases_needed=[],
    tags=["memory", "code-intelligence", "codebase", "tree-sitter"],
),
```

Optional tuning via env (all optional): `CBM_CACHE_DIR`, `CBM_LOG_LEVEL`,
`CBM_WORKERS`, `CBM_DOWNLOAD_URL`.

### Installed binary (offline / pinned launch)

The `npx` command above fetches the native binary on first run. For air-gapped
or pinned deployments, gludd can instead **install the binary itself**:
`codebase-memory-mcp` is registered in
`filestore/bootstrap.py` `BinaryBootstrapper.KNOWN_VERSIONS`
(`v{CODEBASE_MEMORY_VERSION}`). `download("codebase-memory-mcp")` (or
`download_all()`) resolves the per-platform GitHub release asset
(`codebase-memory-mcp-<os>-<arch>.tar.gz`; Linux uses the static `-portable`
build), extracts the bare `codebase-memory-mcp` executable from the archive
root (traversal-validated, in-memory), stores it under the filestore
`binaries/` dir, and sets the executable bits. `get_binary_path(
"codebase-memory-mcp")` then returns its absolute path. Windows ships a `.zip`
rather than a tarball and so is served by the `npx` path, not the bootstrapper.

To launch from the installed binary instead of `npx`, point the
`MCPServerConfig.command` at that absolute path. Because the binary's basename
is not in the npm/uvx launcher allow-list, that launch requires
`GLUDD_MCP_ALLOW_ANY_EXEC=1` (it is a deliberately-vetted, version-pinned local
binary). This trades the launcher allow-list guarantee for a frozen,
network-free launch — choose per deployment.

---

## Checklist for onboarding a new MCP server

1. [ ] Pick the launch command; **pin the version** (`pkg@x.y.z`).
2. [ ] Confirm the launcher is allow-listed (`npx`/`uvx`/`node`/…) or plan for
   a vetted absolute binary path.
3. [ ] Claude Code: add to `/.mcp.json` and enable/trust it (`/mcp`).
4. [ ] gludd: add an `MCPCatalogEntry` to `_KNOWN_SERVERS` (`source="official"`,
   `env_aliases_needed` for any secrets) + a catalog guard test.
5. [ ] gludd runtime: add the server to the daemon's `MCPServerConfig` so the
   client connects it; keep the `command` identical to the catalog entry.
6. [ ] Grant the agent role the `mcp` dispatch capability if it will call the
   tools via the in-process model/tool-call path.
7. [ ] Verify discovery: `gludd mcp info <name>`. Verify connection: start the
   daemon with the server enabled and confirm the startup log
   "MCPClient started with N server(s)" and that the tools appear in the
   registry. (Ansible-role invocation via `gludd_mcp_tool` is not wired yet —
   see Step 4.)
