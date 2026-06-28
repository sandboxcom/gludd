from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from general_ludd.filestore.bootstrap import BinaryBootstrapper
from general_ludd.filestore.store import FileStore
from general_ludd.security.sanitize import sanitize_path

# Maximum allowed byte length for a single /admin/filestore/write request body.
# Override via FILESTORE_WRITE_MAX_BYTES env var (bytes).
_DEFAULT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
FILESTORE_WRITE_MAX_BYTES: int = int(
    os.environ.get("FILESTORE_WRITE_MAX_BYTES", _DEFAULT_MAX_BYTES)
)


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.get("/admin/filestore/list")
    async def admin_filestore_list(path: str = "/") -> dict[str, Any]:
        safe_path = sanitize_path(path.lstrip("/")) or ""
        store = FileStore()
        entries = store.list_dir(safe_path)
        return {"path": safe_path, "entries": entries, "count": len(entries)}

    @app.get("/admin/filestore/read")
    async def admin_filestore_read(path: str = "") -> dict[str, Any]:
        safe_path = sanitize_path(path.lstrip("/"))
        if safe_path is None:
            return {"error": "Invalid path"}
        store = FileStore()
        if not store.exists(safe_path):
            return {"error": f"Path not found: {safe_path}"}
        if store.is_dir(safe_path):
            entries = store.list_dir(safe_path)
            return {"path": safe_path, "is_dir": True, "entries": entries}
        try:
            content = store.read_text(safe_path)
            return {"path": safe_path, "is_dir": False, "content": content}
        except Exception:
            return {"path": safe_path, "is_dir": False, "binary": True}

    @app.post("/admin/filestore/write")
    async def admin_filestore_write(request: Request) -> Any:
        store = FileStore()
        body = await request.json()
        raw_path = body.get("path", "")
        safe_path = sanitize_path(raw_path)
        if safe_path is None:
            return {"error": "Invalid path", "success": False}
        content = body.get("content", "")
        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        if len(content_bytes) > FILESTORE_WRITE_MAX_BYTES:
            return JSONResponse(
                status_code=413,
                content={
                    "error": "Content exceeds maximum allowed size",
                    "max_bytes": FILESTORE_WRITE_MAX_BYTES,
                    "received_bytes": len(content_bytes),
                    "success": False,
                },
            )
        store.write_text(safe_path, content)
        return {"success": True, "path": safe_path}

    @app.delete("/admin/filestore/remove")
    async def admin_filestore_remove(path: str = "") -> dict[str, Any]:
        safe_path = sanitize_path(path)
        if safe_path is None:
            return {"error": "Invalid path", "success": False}
        store = FileStore()
        if not store.exists(safe_path):
            return {"error": f"Path not found: {safe_path}"}
        store.remove(safe_path)
        return {"success": True, "path": safe_path}

    @app.post("/admin/filestore/bootstrap")
    async def admin_filestore_bootstrap(
        binary: str = "openbao",
    ) -> dict[str, Any]:
        store = FileStore()
        boot = BinaryBootstrapper(store=store)
        if binary == "openbao":
            success = await boot.download_openbao()
            return {"success": success, "binary": binary, "stored": boot.check_openbao_in_store()}
        # Generic path: any other KNOWN_VERSIONS binary (opentofu, osquery,
        # codebase-memory-mcp, ...) is downloaded/extracted/chmod'd via download().
        if binary in boot.get_known_versions():
            if not boot.is_platform_available(binary):
                return {
                    "success": False,
                    "binary": binary,
                    "error": "No download available for this platform/arch",
                }
            success = await boot.download(binary)
            return {
                "success": success,
                "binary": binary,
                "version": boot.get_known_versions().get(binary),
                "stored": boot.get_binary_path(binary) is not None,
            }
        return {
            "success": False,
            "error": f"Unknown binary: {binary}",
            "known": sorted(boot.get_known_versions()),
        }

    @app.get("/admin/filestore/binaries")
    async def admin_filestore_binaries() -> dict[str, Any]:
        store = FileStore()
        boot = BinaryBootstrapper(store=store)
        bins = boot.list_binaries()
        return {"binaries": bins, "count": len(bins)}
