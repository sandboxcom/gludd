from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from general_ludd.secrets.cosign import delete_cosign_key, generate_and_store_cosign_key, read_cosign_key
from general_ludd.secrets.gitsign import read_gitsign_config, write_gitsign_config


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/signing/cosign/generate")
    async def admin_cosign_generate(req: dict[str, Any]) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "write_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        key = generate_and_store_cosign_key(
            mgr=resolver,
            project_id=req.get("project_id", "default"),
            key_name=req.get("key_name", "cosign-key"),
            output_dir=req.get("output_dir"),
            password=req.get("password"),
        )
        return {"key_name": key.key_name, "public_key": key.public_key, "created_at": key.created_at}

    @app.get("/admin/signing/cosign/list/{project_id}")
    async def admin_cosign_list(project_id: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "read_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        prefix = f"projects/{project_id}/cosign/"
        keys = []
        if hasattr(resolver, "list_secrets"):
            for path in resolver.list_secrets(prefix):
                name = path.replace(prefix, "")
                key = read_cosign_key(resolver, project_id, name)
                if key:
                    keys.append({"key_name": key.key_name, "public_key": key.public_key, "created_at": key.created_at})
        return keys

    @app.get("/admin/signing/cosign/{project_id}/{key_name}")
    async def admin_cosign_read(project_id: str, key_name: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "read_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        key = read_cosign_key(resolver, project_id, key_name)
        if key is None:
            return JSONResponse(status_code=404, content={"error": "key not found"})
        return {"key_name": key.key_name, "public_key": key.public_key, "created_at": key.created_at}

    @app.delete("/admin/signing/cosign/{project_id}/{key_name}")
    async def admin_cosign_delete(project_id: str, key_name: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "delete_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        delete_cosign_key(resolver, project_id, key_name)
        return {"status": "deleted", "project_id": project_id, "key_name": key_name}

    @app.post("/admin/signing/gitsign/config")
    async def admin_gitsign_write(req: dict[str, Any]) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "write_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        write_gitsign_config(
            mgr=resolver,
            project_id=req.get("project_id", "default"),
            fulcio_url=req.get("fulcio_url", "https://fulcio.sigstore.dev"),
            rekor_url=req.get("rekor_url", "https://rekor.sigstore.dev"),
            oidc_issuer=req.get("oidc_issuer", "https://oauth2.sigstore.dev/auth"),
            key_ref=req.get("key_ref", ""),
            enabled=req.get("enabled", True),
        )
        return {"status": "ok"}

    @app.get("/admin/signing/gitsign/{project_id}")
    async def admin_gitsign_read(project_id: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "read_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        config = read_gitsign_config(resolver, project_id)
        if config is None:
            return JSONResponse(status_code=404, content={"error": "gitsign config not found"})
        return {
            "fulcio_url": config.fulcio_url,
            "rekor_url": config.rekor_url,
            "oidc_issuer": config.oidc_issuer,
            "key_ref": config.key_ref,
            "enabled": config.enabled,
        }
