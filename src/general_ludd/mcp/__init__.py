"""MCP — Model Context Protocol client, config, catalog, and registry."""

from __future__ import annotations

__all__ = (
    "MCPCatalog",
    "MCPCatalogEntry",
    "MCPClient",
    "MCPServerConfig",
    "MCPStdioClient",
    "MCPTool",
    "MCPToolRegistry",
    "MCPTransportError",
    "load_mcp_config",
    "resolve_mcp_env",
    "scrub_mcp_config",
)

from general_ludd.mcp.catalog import MCPCatalog, MCPCatalogEntry
from general_ludd.mcp.client import MCPClient
from general_ludd.mcp.config import MCPServerConfig
from general_ludd.mcp.loader import load_mcp_config
from general_ludd.mcp.registry import MCPTool, MCPToolRegistry
from general_ludd.mcp.secrets import resolve_mcp_env, scrub_mcp_config
from general_ludd.mcp.transport import MCPStdioClient, MCPTransportError
