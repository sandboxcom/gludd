from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from general_ludd.security.permissions import check_capability

logger = logging.getLogger(__name__)


class RequireCapability:
    def __init__(self, *, resource: str, action: str) -> None:
        self._resource = resource
        self._action = action

    async def __call__(self, request: Request) -> None:
        spec = getattr(request.state, "auth_spec", None)
        if spec is None:
            logger.warning(
                "Capability guard failed: no auth_spec on request state for %s %s (required %s:%s)",
                request.method,
                request.url.path,
                self._resource,
                self._action,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden: no_auth_spec",
                    "required": f"{self._resource}:{self._action}",
                },
            )
        if not check_capability(spec, self._resource, self._action):
            logger.warning(
                "Capability guard denied: auth_spec=%s lacks %s:%s for %s %s",
                getattr(spec, "agent_type", "unknown"),
                self._resource,
                self._action,
                request.method,
                request.url.path,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "forbidden: insufficient_capability",
                    "required": f"{self._resource}:{self._action}",
                },
            )
