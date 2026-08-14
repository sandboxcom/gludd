"""MCP server catalog: search and discover MCP servers from public registries.

Queries the official MCP registry (registry.modelcontextprotocol.io),
Smithery (api.smithery.ai), and Glama (glama.ai) for server discovery.
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from pydantic import BaseModel, Field

from general_ludd.mcp._validators import TrimmedNonEmptyStr
from general_ludd.security.url_fetch import FetchPolicy, secure_fetch

logger = logging.getLogger(__name__)

_REGISTRY_RESPONSE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB hard cap per registry response


class MCPCatalogEntry(BaseModel):
    """Describe one curated or remotely discovered MCP server."""

    server_name: TrimmedNonEmptyStr
    display_name: str = ""
    description: str = ""
    source: str = ""
    url: str = ""
    command: list[str] = Field(default_factory=list)
    env_aliases_needed: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    downloads: int = 0

class MCPCatalog:
    """Search and discover MCP servers from public registries."""

    def __init__(self, registries: list[str] | None = None) -> None:
        """Initialize the catalog with explicit or default registry hosts."""
        self._registries = registries if registries is not None else [
            "registry.modelcontextprotocol.io",
            "smithery.ai",
            "glama.ai",
        ]
        self._cache: list[MCPCatalogEntry] = []

    def search(
        self,
        query: str = "",
        limit: int = 20,
        source: str | None = None,
    ) -> list[MCPCatalogEntry]:
        """Synchronous registry search. Blocks the event loop — use search_async() from async callers."""
        results: list[MCPCatalogEntry] = []
        for registry in self._registries:
            if source and source not in registry:
                continue
            try:
                entries = self._query_registry(registry, query, limit)
                results.extend(entries)
            except Exception as exc:
                logger.debug("Registry %s query failed: %s", registry, exc)
        return results[:limit]

    async def search_async(
        self,
        query: str = "",
        limit: int = 20,
        source: str | None = None,
    ) -> list[MCPCatalogEntry]:
        """Run ``search`` without blocking the caller's event loop.

        Use from any async caller, including FastAPI routes and loop tasks.
        """
        import asyncio

        results: list[MCPCatalogEntry] = []
        for registry in self._registries:
            if source and source not in registry:
                continue
            try:
                entries = await asyncio.to_thread(self._query_registry, registry, query, limit)
                results.extend(entries)
            except Exception as exc:
                logger.debug("Registry %s query failed: %s", registry, exc)
        return results[:limit]

    def get_known_servers(self) -> list[MCPCatalogEntry]:
        """Return the curated, locally trusted server entries."""
        return list(_KNOWN_SERVERS.values())

    def get_server(self, name: str) -> MCPCatalogEntry | None:
        """Return a curated or cached server entry by exact name."""
        if name in _KNOWN_SERVERS:
            return _KNOWN_SERVERS[name]
        for entry in self._cache:
            if entry.server_name == name:
                return entry
        return None

    def refresh(self) -> None:
        """Clear remotely discovered entries while retaining curated entries."""
        self._cache.clear()

    @staticmethod
    def _harden_registry_entry(entry: MCPCatalogEntry) -> MCPCatalogEntry:
        """Strip any launchable command from a remote-registry entry.

        SUPPLY-CHAIN: entries discovered over the network are UNTRUSTED. They
        must never carry an executable ``command`` — a hostile registry could
        otherwise hand us an arbitrary (unpinned, attacker-controlled) spawn
        line that downstream code might launch. We fail closed by clearing the
        field unconditionally; only the curated, version-pinned _KNOWN_SERVERS
        entries are ever launchable.
        """
        if entry.command:
            logger.warning(
                "Dropping command from untrusted registry entry %r (source=%s)",
                entry.server_name,
                entry.source,
            )
            entry.command = []
        return entry

    def _query_registry(
        self, registry: str, query: str, limit: int
    ) -> list[MCPCatalogEntry]:
        import json
        if "smithery.ai" in registry:
            url = "https://api.smithery.ai/servers"
            params: dict[str, str] = {"pageSize": str(min(limit, 100))}
            if query:
                params["q"] = query
            url = f"{url}?{urlencode(params)}"
            response = secure_fetch(
                url,
                headers={"Accept": "application/json"},
                policy=FetchPolicy(
                    allowed_hosts=frozenset({"api.smithery.ai"}),
                    max_bytes=_REGISTRY_RESPONSE_MAX_BYTES,
                    timeout_seconds=10,
                    max_redirects=2,
                ),
            )
            data = json.loads(response.content.decode())
            entries: list[MCPCatalogEntry] = []
            for s in data.get("servers", []):
                entries.append(self._harden_registry_entry(MCPCatalogEntry(
                    server_name=s.get("qualifiedName", ""),
                    display_name=s.get("displayName", ""),
                    description=s.get("description", ""),
                    source="smithery.ai",
                    downloads=s.get("useCount", 0),
                )))
            return entries

        if "registry.modelcontextprotocol.io" in registry:
            url = f"https://registry.modelcontextprotocol.io/v0.1/servers?limit={min(limit, 100)}"
            response = secure_fetch(
                url,
                headers={"Accept": "application/json"},
                policy=FetchPolicy(
                    allowed_hosts=frozenset({"registry.modelcontextprotocol.io"}),
                    max_bytes=_REGISTRY_RESPONSE_MAX_BYTES,
                    timeout_seconds=10,
                    max_redirects=2,
                ),
            )
            data = json.loads(response.content.decode())
            entries = []
            for s in data.get("servers", []):
                name_val = s.get("name", "")
                if isinstance(name_val, dict):
                    name_val = name_val.get("name", str(name_val))
                entries.append(self._harden_registry_entry(MCPCatalogEntry(
                    server_name=str(name_val),
                    description=s.get("description", ""),
                    source="registry.modelcontextprotocol.io",
                )))
            return entries

        return []


# SUPPLY-CHAIN: every command below pins an explicit @<version> on the npm
# package spec. An unpinned `npx -y @scope/pkg` auto-installs the LATEST build
# from npm at launch, so a hijacked or typo-squatted release is silently
# fetched and executed (remote code execution). Pinning freezes the bytes we
# run. The launch-time gate in transport._assert_pinned_command refuses to
# spawn any npm-family command whose spec is not version-pinned, so this list
# is enforced (fail-closed) and not merely advisory.
#
# Versions below are the documented current releases as of 2026-06:
#   - Actively maintained servers (modelcontextprotocol/servers) ship a
#     date-based version; 2026.1.26 is the current line.
#   - Archived servers (modelcontextprotocol/servers-archived) were frozen at
#     their final 0.6.2 release.
# REVIEW REQUIRED: re-verify these pins against npm before each release and
# bump deliberately — never relax a pin back to a bare/unpinned spec.
_FS_VER = "2026.1.26"
_GITHUB_VER = "2026.1.26"
_MEMORY_VER = "2026.1.26"
_ARCHIVED_VER = "0.6.2"
# Third-party (DeusData) structural code-intelligence server. Pinned like the
# rest; bump deliberately after re-verifying the release on npm/PyPI.
_CODEBASE_MEMORY_VER = "0.8.1"

_KNOWN_SERVERS: dict[str, MCPCatalogEntry] = {
    "filesystem": MCPCatalogEntry(
        server_name="filesystem",
        display_name="Filesystem",
        description="Read, write, and search files on the local filesystem",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-filesystem@{_FS_VER}"],
        tags=["files", "local", "official"],
    ),
    "github": MCPCatalogEntry(
        server_name="github",
        display_name="GitHub",
        description="GitHub API integration for repos, issues, PRs, and more",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-github@{_GITHUB_VER}"],
        env_aliases_needed=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        tags=["git", "github", "official"],
    ),
    "gitlab": MCPCatalogEntry(
        server_name="gitlab",
        display_name="GitLab",
        description="GitLab API integration for projects, issues, MRs",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-gitlab@{_ARCHIVED_VER}"],
        env_aliases_needed=["GITLAB_PERSONAL_ACCESS_TOKEN"],
        tags=["git", "gitlab", "official"],
    ),
    "fetch": MCPCatalogEntry(
        server_name="fetch",
        display_name="Fetch",
        description="Web content fetching and scraping",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-fetch@{_ARCHIVED_VER}"],
        tags=["web", "http", "official"],
    ),
    "brave-search": MCPCatalogEntry(
        server_name="brave-search",
        display_name="Brave Search",
        description="Web search using Brave Search API",
        source="official",
        command=[
            "npx", "-y", f"@modelcontextprotocol/server-brave-search@{_ARCHIVED_VER}"
        ],
        env_aliases_needed=["BRAVE_API_KEY"],
        tags=["search", "web", "official"],
    ),
    "sqlite": MCPCatalogEntry(
        server_name="sqlite",
        display_name="SQLite",
        description="SQLite database operations",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-sqlite@{_ARCHIVED_VER}"],
        tags=["database", "sqlite", "official"],
    ),
    "postgres": MCPCatalogEntry(
        server_name="postgres",
        display_name="PostgreSQL",
        description="PostgreSQL database operations",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-postgres@{_ARCHIVED_VER}"],
        tags=["database", "postgres", "official"],
    ),
    "slack": MCPCatalogEntry(
        server_name="slack",
        display_name="Slack",
        description="Slack workspace integration",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-slack@{_ARCHIVED_VER}"],
        env_aliases_needed=["SLACK_BOT_TOKEN"],
        tags=["communication", "slack", "official"],
    ),
    "puppeteer": MCPCatalogEntry(
        server_name="puppeteer",
        display_name="Puppeteer",
        description="Browser automation for web scraping and testing",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-puppeteer@{_ARCHIVED_VER}"],
        tags=["browser", "automation", "official"],
    ),
    "memory": MCPCatalogEntry(
        server_name="memory",
        display_name="Memory",
        description="Knowledge graph for persistent memory across sessions",
        source="official",
        command=["npx", "-y", f"@modelcontextprotocol/server-memory@{_MEMORY_VER}"],
        tags=["memory", "knowledge-graph", "official"],
    ),
    "codebase-memory": MCPCatalogEntry(
        server_name="codebase-memory",
        display_name="Codebase Memory",
        description=(
            "Structural code-intelligence server (DeusData codebase-memory-mcp): "
            "indexes a repository into a persistent SQLite knowledge graph of "
            "functions, classes, call chains and routes via tree-sitter. Gives "
            "agents fast 'where is X / what calls Y' codebase memory. No API key "
            "or embedding model required; storage in CBM_CACHE_DIR (stdio MCP)."
        ),
        source="official",
        command=["npx", "-y", f"codebase-memory-mcp@{_CODEBASE_MEMORY_VER}"],
        env_aliases_needed=[],
        tags=["memory", "code-intelligence", "codebase", "tree-sitter"],
    ),
}
