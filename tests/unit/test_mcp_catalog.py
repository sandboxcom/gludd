"""Tests for MCP catalog: search and discovery of MCP servers."""

from __future__ import annotations

from general_ludd.mcp.catalog import MCPCatalog, MCPCatalogEntry


class TestMCPCatalogEntry:
    def test_entry_has_required_fields(self):
        entry = MCPCatalogEntry(
            server_name="github",
            display_name="GitHub",
            description="GitHub API",
            command=["npx", "-y", "@mcp/server-github"],
            env_aliases_needed=["GITHUB_PERSONAL_ACCESS_TOKEN"],
        )
        assert entry.server_name == "github"
        assert entry.env_aliases_needed == ["GITHUB_PERSONAL_ACCESS_TOKEN"]

    def test_entry_defaults(self):
        entry = MCPCatalogEntry(server_name="test")
        assert entry.tags == []
        assert entry.command == []
        assert entry.env_aliases_needed == []


class TestMCPCatalogKnownServers:
    def test_get_known_servers_returns_list(self):
        catalog = MCPCatalog()
        servers = catalog.get_known_servers()
        assert len(servers) >= 10

    def test_known_servers_include_github(self):
        catalog = MCPCatalog()
        github = catalog.get_server("github")
        assert github is not None
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in github.env_aliases_needed

    def test_known_servers_include_filesystem(self):
        catalog = MCPCatalog()
        fs = catalog.get_server("filesystem")
        assert fs is not None
        assert fs.display_name == "Filesystem"

    def test_get_server_returns_none_for_unknown(self):
        catalog = MCPCatalog()
        assert catalog.get_server("nonexistent") is None

    def test_all_known_servers_have_names(self):
        catalog = MCPCatalog()
        for server in catalog.get_known_servers():
            assert server.server_name
            assert server.source

    def test_servers_with_env_aliases_need_secrets(self):
        catalog = MCPCatalog()
        servers_needing_secrets = [
            s for s in catalog.get_known_servers()
            if s.env_aliases_needed
        ]
        assert len(servers_needing_secrets) >= 3


class TestMCPCatalogSearch:
    def test_search_returns_list(self):
        catalog = MCPCatalog(registries=[])
        results = catalog.search(query="test", limit=5)
        assert isinstance(results, list)

    def test_search_empty_registries_returns_empty(self):
        catalog = MCPCatalog(registries=[])
        results = catalog.search(query="anything")
        assert results == []
