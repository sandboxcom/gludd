"""Shared MCP exception types — avoids circular imports between transport and secrets."""

from __future__ import annotations


class MCPTransportError(Exception):
    pass
