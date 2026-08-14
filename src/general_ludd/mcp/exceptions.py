"""Shared MCP exception types — avoids circular imports between transport and secrets."""

from __future__ import annotations


class MCPTransportError(RuntimeError):
    """Raised when an MCP transport cannot safely complete an operation."""
