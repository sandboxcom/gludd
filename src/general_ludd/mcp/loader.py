from __future__ import annotations

from pathlib import Path

import yaml

from general_ludd.mcp.config import MCPServerConfig

_MCP_CONFIG_MAX_BYTES = 512 * 1024  # 512 KB — MCP configs are tiny; cap fails hard


def load_mcp_config(config_path: str) -> dict[str, MCPServerConfig]:
    path = Path(config_path)
    if not path.exists():
        return {}
    size = path.stat().st_size
    if size > _MCP_CONFIG_MAX_BYTES:
        raise ValueError(
            f"MCP config {config_path!r} is too large "
            f"({size} bytes > {_MCP_CONFIG_MAX_BYTES}); refusing to load."
        )
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    servers = data.get("servers", {})
    configs: dict[str, MCPServerConfig] = {}
    for server_id, server_data in servers.items():
        server_data.setdefault("server_id", server_id)
        configs[server_id] = MCPServerConfig(**server_data)
    return configs
