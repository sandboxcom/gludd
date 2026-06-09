from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.get("/admin/filestore/list")
    async def admin_filestore_list(path: str = "/") -> dict[str, Any]:
        from general_ludd.filestore.store import FileStore
        from general_ludd.security.sanitize import sanitize_path

        safe_path = sanitize_path(path.lstrip("/")) or ""
        store = FileStore()
        entries = store.list_dir(safe_path)
        return {"path": safe_path, "entries": entries, "count": len(entries)}

    @app.get("/admin/filestore/read")
    async def admin_filestore_read(path: str = "") -> dict[str, Any]:
        from general_ludd.filestore.store import FileStore
        from general_ludd.security.sanitize import sanitize_path

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
    async def admin_filestore_write(request: Request) -> dict[str, Any]:
        from general_ludd.filestore.store import FileStore
        from general_ludd.security.sanitize import sanitize_path

        store = FileStore()
        body = await request.json()
        raw_path = body.get("path", "")
        safe_path = sanitize_path(raw_path)
        if safe_path is None:
            return {"error": "Invalid path", "success": False}
        content = body.get("content", "")
        store.write_text(safe_path, content)
        return {"success": True, "path": safe_path}

    @app.delete("/admin/filestore/remove")
    async def admin_filestore_remove(path: str = "") -> dict[str, Any]:
        from general_ludd.filestore.store import FileStore
        from general_ludd.security.sanitize import sanitize_path

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
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        store = FileStore()
        boot = BinaryBootstrapper(store=store)
        if binary == "openbao":
            success = await boot.download_openbao()
            return {"success": success, "binary": binary, "stored": boot.check_openbao_in_store()}
        return {"success": False, "error": f"Unknown binary: {binary}"}

    @app.get("/admin/filestore/binaries")
    async def admin_filestore_binaries() -> dict[str, Any]:
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        store = FileStore()
        boot = BinaryBootstrapper(store=store)
        bins = boot.list_binaries()
        return {"binaries": bins, "count": len(bins)}
