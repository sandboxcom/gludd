"""Tests for MCP catalog: search and discovery of MCP servers."""

from __future__ import annotations

import json
from unittest.mock import patch

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

    @patch("urllib.request.urlopen")
    def test_search_smithery_registry(self, mock_urlopen):
        catalog = MCPCatalog(registries=["smithery.ai"])
        mock_resp = type("Resp", (), {
            "read": lambda self: json.dumps({
                "servers": [
                    {"qualifiedName": "test-server", "displayName": "Test", "description": "A test", "useCount": 42},
                ]
            }).encode(),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        mock_urlopen.return_value = mock_resp
        results = catalog.search(query="test", limit=10)
        assert len(results) == 1
        assert results[0].server_name == "test-server"
        assert results[0].source == "smithery.ai"
        assert results[0].downloads == 42

    @patch("urllib.request.urlopen")
    def test_search_mcp_registry(self, mock_urlopen):
        catalog = MCPCatalog(registries=["registry.modelcontextprotocol.io"])
        mock_resp = type("Resp", (), {
            "read": lambda self: json.dumps({
                "servers": [
                    {"name": "my-server", "description": "An MCP server"},
                ]
            }).encode(),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        mock_urlopen.return_value = mock_resp
        results = catalog.search(query="my", limit=10)
        assert len(results) == 1
        assert results[0].server_name == "my-server"
        assert results[0].source == "registry.modelcontextprotocol.io"

    @patch("urllib.request.urlopen")
    def test_search_mcp_registry_dict_name(self, mock_urlopen):
        catalog = MCPCatalog(registries=["registry.modelcontextprotocol.io"])
        mock_resp = type("Resp", (), {
            "read": lambda self: json.dumps({
                "servers": [
                    {"name": {"name": "nested-name"}, "description": "Nested"},
                ]
            }).encode(),
            "__enter__": lambda self: self,
            "__exit__": lambda self, *a: None,
        })()
        mock_urlopen.return_value = mock_resp
        results = catalog.search(limit=10)
        assert len(results) == 1
        assert results[0].server_name == "nested-name"

    def test_search_filters_by_source(self):
        catalog = MCPCatalog(registries=["smithery.ai", "glama.ai"])
        results = catalog.search(query="test", source="smithery")
        assert isinstance(results, list)

    @patch("urllib.request.urlopen")
    def test_search_handles_registry_error(self, mock_urlopen):
        catalog = MCPCatalog(registries=["smithery.ai"])
        mock_urlopen.side_effect = Exception("Network error")
        results = catalog.search(query="test")
        assert results == []

    def test_search_limits_results(self):
        catalog = MCPCatalog(registries=[])
        results = catalog.search(query="test", limit=1)
        assert len(results) <= 1


class TestMCPCatalogRefresh:
    def test_refresh_clears_cache(self):
        catalog = MCPCatalog()
        catalog._cache.append(MCPCatalogEntry(server_name="cached-server"))
        assert len(catalog._cache) == 1
        catalog.refresh()
        assert len(catalog._cache) == 0

    def test_get_server_finds_cached_entry(self):
        catalog = MCPCatalog(registries=[])
        cached_entry = MCPCatalogEntry(server_name="cached-server")
        catalog._cache.append(cached_entry)
        assert catalog.get_server("cached-server") is cached_entry

    def test_refresh_then_known_still_works(self):
        catalog = MCPCatalog()
        catalog._cache.append(MCPCatalogEntry(server_name="temp"))
        catalog.refresh()
        assert catalog.get_server("github") is not None
