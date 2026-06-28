from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from general_ludd.secrets.cosign import delete_cosign_key, generate_and_store_cosign_key, read_cosign_key
from general_ludd.secrets.gitsign import read_gitsign_config, write_gitsign_config

# Allowlisted root that output_dir must nest under.  Using the FileStore root
# keeps cosign key exports in the same location the rest of the daemon uses for
# on-disk artifacts and prevents callers from writing to arbitrary paths
# (e.g. /etc, /root, or paths outside the application data directory).
_COSIGN_OUTPUT_ALLOWED_ROOT: str = os.path.realpath(
    os.path.expanduser("~/.local/share/general-ludd/filestore")
)


def _validate_output_dir(output_dir: str | None) -> str | None:
    """Return the resolved path when safe, or raise ValueError when not.

    Resolves ``output_dir`` to its real (symlink-free) absolute path and
    asserts it is strictly nested under ``_COSIGN_OUTPUT_ALLOWED_ROOT``.
    Rejects None-as-string, absolute paths that escape the allowed root, and
    relative traversal sequences (``../../etc``).
    """
    if output_dir is None:
        return None
    # os.path.realpath resolves symlinks and ``..`` in one step.
    resolved = os.path.realpath(os.path.abspath(output_dir))
    # Require that resolved path is the allowed root itself OR a strict descendant.
    # The trailing os.sep ensures "/allowed-rootXYZ" is not accepted as a child.
    allowed = _COSIGN_OUTPUT_ALLOWED_ROOT
    if resolved != allowed and not resolved.startswith(allowed + os.sep):
        raise ValueError(
            f"output_dir {output_dir!r} resolves to {resolved!r} which is "
            f"outside the allowed root {allowed!r}"
        )
    return resolved


def register(app: FastAPI, _daemon_state: dict[str, Any]) -> None:

    @app.post("/admin/signing/cosign/generate")
    async def admin_cosign_generate(req: dict[str, Any]) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "write_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        raw_output_dir: str | None = req.get("output_dir")
        try:
            safe_output_dir = _validate_output_dir(raw_output_dir)
            key = generate_and_store_cosign_key(
                mgr=resolver,
                project_id=req.get("project_id", "default"),
                key_name=req.get("key_name", "cosign-key"),
                output_dir=safe_output_dir,
                password=req.get("password"),
            )
        except ValueError as exc:
            # Invalid project_id/key_name (segment regex) or output_dir → clean
            # 400, not an uncaught 500. The inner _scoped_path raises ValueError
            # on any segment outside ^[A-Za-z0-9_-]+$ (e.g. a dotted key name).
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return {"key_name": key.key_name, "public_key": key.public_key, "created_at": key.created_at}

    @app.get("/admin/signing/cosign/list/{project_id}")
    async def admin_cosign_list(project_id: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "read_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        prefix = f"projects/{project_id}/cosign/"
        keys = []
        try:
            if hasattr(resolver, "list_secrets"):
                for path in resolver.list_secrets(prefix):
                    name = path.replace(prefix, "")
                    key = read_cosign_key(resolver, project_id, name)
                    if key:
                        keys.append(
                            {
                                "key_name": key.key_name,
                                "public_key": key.public_key,
                                "created_at": key.created_at,
                            }
                        )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return keys

    @app.get("/admin/signing/cosign/{project_id}/{key_name}")
    async def admin_cosign_read(project_id: str, key_name: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "read_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        try:
            key = read_cosign_key(resolver, project_id, key_name)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        if key is None:
            return JSONResponse(status_code=404, content={"error": "key not found"})
        return {"key_name": key.key_name, "public_key": key.public_key, "created_at": key.created_at}

    @app.delete("/admin/signing/cosign/{project_id}/{key_name}")
    async def admin_cosign_delete(project_id: str, key_name: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "delete_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        try:
            delete_cosign_key(resolver, project_id, key_name)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return {"status": "deleted", "project_id": project_id, "key_name": key_name}

    @app.post("/admin/signing/gitsign/config")
    async def admin_gitsign_write(req: dict[str, Any]) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "write_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        try:
            write_gitsign_config(
                mgr=resolver,
                project_id=req.get("project_id", "default"),
                fulcio_url=req.get("fulcio_url", "https://fulcio.sigstore.dev"),
                rekor_url=req.get("rekor_url", "https://rekor.sigstore.dev"),
                oidc_issuer=req.get("oidc_issuer", "https://oauth2.sigstore.dev/auth"),
                key_ref=req.get("key_ref", ""),
                enabled=req.get("enabled", True),
            )
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        return {"status": "ok"}

    @app.get("/admin/signing/gitsign/{project_id}")
    async def admin_gitsign_read(project_id: str) -> Any:
        resolver = getattr(app.state, "_secrets_resolver", None)
        if resolver is None or not hasattr(resolver, "read_secret"):
            return JSONResponse(status_code=503, content={"error": "secrets resolver not available"})
        try:
            config = read_gitsign_config(resolver, project_id)
        except ValueError as exc:
            return JSONResponse(status_code=400, content={"error": str(exc)})
        if config is None:
            return JSONResponse(status_code=404, content={"error": "gitsign config not found"})
        return {
            "fulcio_url": config.fulcio_url,
            "rekor_url": config.rekor_url,
            "oidc_issuer": config.oidc_issuer,
            "key_ref": config.key_ref,
            "enabled": config.enabled,
        }
