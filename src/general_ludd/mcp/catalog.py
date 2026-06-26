"""MCP server catalog: search and discover MCP servers from public registries.

Queries the official MCP registry (registry.modelcontextprotocol.io),
Smithery (api.smithery.ai), and Glama (glama.ai) for server discovery.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

_REGISTRY_RESPONSE_MAX_BYTES = 2 * 1024 * 1024  # 2 MB hard cap per registry response


class MCPCatalogEntry(BaseModel):
    server_name: str
    display_name: str = ""
    description: str = ""
    source: str = ""
    url: str = ""
    command: list[str] = Field(default_factory=list)
    env_aliases_needed: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    downloads: int = 0

    @field_validator("server_name", mode="before")
    @classmethod
    def _strip_and_require(cls, v: str) -> str:
        if isinstance(v, str):
            v = v.strip()
        if not v:
            raise ValueError("server_name must not be empty")
        return v


class MCPCatalog:
    """Search and discover MCP servers from public registries."""

    def __init__(self, registries: list[str] | None = None) -> None:
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
        """Async search() — dispatches blocking network I/O to a thread pool.
        Use from any async caller (FastAPI route, event-loop task)."""
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
        return list(_KNOWN_SERVERS.values())

    def get_server(self, name: str) -> MCPCatalogEntry | None:
        if name in _KNOWN_SERVERS:
            return _KNOWN_SERVERS[name]
        for entry in self._cache:
            if entry.server_name == name:
                return entry
        return None

    def refresh(self) -> None:
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
        import urllib.parse
        import urllib.request

        if "smithery.ai" in registry:
            url = "https://api.smithery.ai/servers"
            params: dict[str, str] = {"pageSize": str(min(limit, 100))}
            if query:
                params["q"] = query
            url = f"{url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read(_REGISTRY_RESPONSE_MAX_BYTES + 1)
                if len(raw) > _REGISTRY_RESPONSE_MAX_BYTES:
                    raise ValueError(
                        f"Smithery registry response exceeded {_REGISTRY_RESPONSE_MAX_BYTES}-byte cap"
                    )
                data = json.loads(raw.decode())
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
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read(_REGISTRY_RESPONSE_MAX_BYTES + 1)
                if len(raw) > _REGISTRY_RESPONSE_MAX_BYTES:
                    raise ValueError(
                        f"MCP registry response exceeded {_REGISTRY_RESPONSE_MAX_BYTES}-byte cap"
                    )
                data = json.loads(raw.decode())
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
}
