"""Shared bounded request and idempotency primitives for daemon routers."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict


class StrictRuntimeRequest(BaseModel):
    """Base model that rejects unknown or coerced control-plane inputs."""

    model_config = ConfigDict(extra="forbid", strict=True)


class IdempotencyStore:
    """Small bounded replay store that serializes daemon-owned mutations."""

    def __init__(self, max_entries: int = 256) -> None:
        self._entries: OrderedDict[str, tuple[str, dict[str, Any]]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._max_entries = max_entries

    @staticmethod
    def _fingerprint(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def run(
        self,
        *,
        key: str | None,
        payload: dict[str, Any],
        producer: Callable[[], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Execute once per key+payload and reject key reuse for another request."""
        if not key:
            return await producer()
        fingerprint = self._fingerprint(payload)
        async with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                cached_fingerprint, cached_result = cached
                if cached_fingerprint != fingerprint:
                    raise HTTPException(
                        status_code=409,
                        detail="idempotency key was already used for a different request",
                    )
                self._entries.move_to_end(key)
                return {**cached_result, "idempotent_replay": True}
            result = await producer()
            self._entries[key] = (fingerprint, dict(result))
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)
            return result
