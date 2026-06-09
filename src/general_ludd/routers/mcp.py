from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException

from general_ludd.mcp.catalog import MCPCatalog


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/mcp/catalog/search")
    async def admin_mcp_catalog_search(req: dict[str, Any]) -> dict[str, Any]:
        catalog = MCPCatalog()
        results = catalog.search(query=req.get("query", ""), limit=req.get("limit", 20))
        return {
            "results": [
                {
                    "server_name": r.server_name,
                    "display_name": r.display_name,
                    "description": r.description,
                    "source": r.source,
                    "command": r.command,
                    "env_aliases_needed": r.env_aliases_needed,
                    "tags": r.tags,
                    "downloads": r.downloads,
                }
                for r in results
            ]
        }

    @app.get("/admin/mcp/catalog/servers")
    async def admin_mcp_catalog_servers() -> dict[str, Any]:
        catalog = MCPCatalog()
        servers = catalog.get_known_servers()
        return {
            "servers": [
                {
                    "server_name": s.server_name,
                    "display_name": s.display_name,
                    "description": s.description,
                    "source": s.source,
                    "command": s.command,
                    "env_aliases_needed": s.env_aliases_needed,
                    "tags": s.tags,
                }
                for s in servers
            ]
        }

    @app.get("/admin/mcp/catalog/servers/{name}")
    async def admin_mcp_catalog_server(name: str) -> dict[str, Any]:
        catalog = MCPCatalog()
        server = catalog.get_server(name)
        if server is None:
            raise HTTPException(status_code=404, detail=f"MCP server {name} not found")
        return {
            "server": {
                "server_name": server.server_name,
                "display_name": server.display_name,
                "description": server.description,
                "source": server.source,
                "command": server.command,
                "env_aliases_needed": server.env_aliases_needed,
                "tags": server.tags,
            }
        }
