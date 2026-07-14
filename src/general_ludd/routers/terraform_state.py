from __future__ import annotations

import datetime
import logging
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

_state_store: dict[str, dict[str, Any]] = {}
_lock_store: dict[str, dict[str, str]] = {}


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:

    def _now_iso() -> str:
        return datetime.datetime.now(datetime.UTC).isoformat()

    @app.api_route(
        "/api/terraform/state/{stack_name}",
        methods=["GET", "POST", "DELETE"],
    )
    async def terraform_state(request: Request, stack_name: str) -> JSONResponse:
        if request.method == "GET":
            state = _state_store.get(stack_name)
            if state is None:
                raise HTTPException(status_code=404, detail=f"State not found for stack: {stack_name}")
            return JSONResponse(content={"state": state})

        if request.method == "POST":
            body = await request.json()
            _state_store[stack_name] = body
            logger.info("Terraform state uploaded for stack=%s", stack_name)
            return JSONResponse(content={})

        if request.method == "DELETE":
            _state_store.pop(stack_name, None)
            _lock_store.pop(stack_name, None)
            logger.info("Terraform state deleted for stack=%s", stack_name)
            return JSONResponse(content={})

        raise HTTPException(status_code=405, detail="Method not allowed")

    @app.api_route(
        "/api/terraform/state/{stack_name}",
        methods=["LOCK"],
    )
    async def terraform_state_lock(request: Request, stack_name: str) -> JSONResponse:
        existing = _lock_store.get(stack_name)
        if existing is not None:
            return JSONResponse(status_code=423, content=existing)

        body = await request.json()
        lock_id = body.get("ID", str(uuid.uuid4()))
        lock_info: dict[str, str] = {
            "ID": lock_id,
            "Operation": body.get("Operation", ""),
            "Info": body.get("Info", ""),
            "Who": body.get("Who", ""),
            "Version": body.get("Version", ""),
            "Created": body.get("Created", _now_iso()),
            "Path": body.get("Path", ""),
        }
        _lock_store[stack_name] = lock_info
        logger.info("Terraform lock acquired for stack=%s lock_id=%s", stack_name, lock_id)
        return JSONResponse(content=lock_info)

    @app.api_route(
        "/api/terraform/state/{stack_name}",
        methods=["UNLOCK"],
    )
    async def terraform_state_unlock(request: Request, stack_name: str) -> JSONResponse:
        existing = _lock_store.get(stack_name)
        if existing is None:
            logger.warning("Unlock attempted on stack with no lock: %s", stack_name)
            return JSONResponse(content={})

        body = await request.json()
        provided_id = body.get("ID", "")
        if provided_id and provided_id != existing["ID"]:
            raise HTTPException(
                status_code=409,
                detail=f"Lock ID mismatch: provided={provided_id}, current={existing['ID']}",
            )

        _lock_store.pop(stack_name, None)
        logger.info("Terraform lock released for stack=%s lock_id=%s", stack_name, existing.get("ID"))
        return JSONResponse(content={})
