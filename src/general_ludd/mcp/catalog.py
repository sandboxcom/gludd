"""MCP server catalog: search and discover MCP servers from public registries.

Queries the official MCP registry (registry.modelcontextprotocol.io),
Smithery (api.smithery.ai), and Glama (glama.ai) for server discovery.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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
                data = json.loads(resp.read().decode())
            entries: list[MCPCatalogEntry] = []
            for s in data.get("servers", []):
                entries.append(MCPCatalogEntry(
                    server_name=s.get("qualifiedName", ""),
                    display_name=s.get("displayName", ""),
                    description=s.get("description", ""),
                    source="smithery.ai",
                    downloads=s.get("useCount", 0),
                ))
            return entries

        if "registry.modelcontextprotocol.io" in registry:
            url = f"https://registry.modelcontextprotocol.io/v0.1/servers?limit={min(limit, 100)}"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            entries = []
            for s in data.get("servers", []):
                name_val = s.get("name", "")
                if isinstance(name_val, dict):
                    name_val = name_val.get("name", str(name_val))
                entries.append(MCPCatalogEntry(
                    server_name=str(name_val),
                    description=s.get("description", ""),
                    source="registry.modelcontextprotocol.io",
                ))
            return entries

        return []


_KNOWN_SERVERS: dict[str, MCPCatalogEntry] = {
    "filesystem": MCPCatalogEntry(
        server_name="filesystem",
        display_name="Filesystem",
        description="Read, write, and search files on the local filesystem",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-filesystem"],
        tags=["files", "local", "official"],
    ),
    "github": MCPCatalogEntry(
        server_name="github",
        display_name="GitHub",
        description="GitHub API integration for repos, issues, PRs, and more",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-github"],
        env_aliases_needed=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        tags=["git", "github", "official"],
    ),
    "gitlab": MCPCatalogEntry(
        server_name="gitlab",
        display_name="GitLab",
        description="GitLab API integration for projects, issues, MRs",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-gitlab"],
        env_aliases_needed=["GITLAB_PERSONAL_ACCESS_TOKEN"],
        tags=["git", "gitlab", "official"],
    ),
    "fetch": MCPCatalogEntry(
        server_name="fetch",
        display_name="Fetch",
        description="Web content fetching and scraping",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-fetch"],
        tags=["web", "http", "official"],
    ),
    "brave-search": MCPCatalogEntry(
        server_name="brave-search",
        display_name="Brave Search",
        description="Web search using Brave Search API",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-brave-search"],
        env_aliases_needed=["BRAVE_API_KEY"],
        tags=["search", "web", "official"],
    ),
    "sqlite": MCPCatalogEntry(
        server_name="sqlite",
        display_name="SQLite",
        description="SQLite database operations",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-sqlite"],
        tags=["database", "sqlite", "official"],
    ),
    "postgres": MCPCatalogEntry(
        server_name="postgres",
        display_name="PostgreSQL",
        description="PostgreSQL database operations",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-postgres"],
        tags=["database", "postgres", "official"],
    ),
    "slack": MCPCatalogEntry(
        server_name="slack",
        display_name="Slack",
        description="Slack workspace integration",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-slack"],
        env_aliases_needed=["SLACK_BOT_TOKEN"],
        tags=["communication", "slack", "official"],
    ),
    "puppeteer": MCPCatalogEntry(
        server_name="puppeteer",
        display_name="Puppeteer",
        description="Browser automation for web scraping and testing",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-puppeteer"],
        tags=["browser", "automation", "official"],
    ),
    "memory": MCPCatalogEntry(
        server_name="memory",
        display_name="Memory",
        description="Knowledge graph for persistent memory across sessions",
        source="official",
        command=["npx", "-y", "@modelcontextprotocol/server-memory"],
        tags=["memory", "knowledge-graph", "official"],
    ),
}
