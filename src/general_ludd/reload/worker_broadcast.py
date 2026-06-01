from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WorkerInfo:
    worker_id: str
    address: str
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class BroadcastResult:
    worker_id: str
    success: bool
    error: str | None = None


class WorkerBroadcaster:
    def __init__(self, stale_threshold_seconds: float = 300.0) -> None:
        self._workers: dict[str, WorkerInfo] = {}
        self._stale_threshold = stale_threshold_seconds

    def register(self, worker: WorkerInfo) -> None:
        self._workers[worker.worker_id] = worker

    def unregister(self, worker_id: str) -> None:
        self._workers.pop(worker_id, None)

    def heartbeat(self, worker_id: str) -> None:
        w = self._workers.get(worker_id)
        if w:
            w.last_seen = time.time()

    def list_workers(self) -> list[WorkerInfo]:
        return list(self._workers.values())

    def cleanup_stale(self) -> None:
        now = time.time()
        stale = [wid for wid, w in self._workers.items() if now - w.last_seen > self._stale_threshold]
        for wid in stale:
            self._workers.pop(wid, None)

    def broadcast_reload(self, scope: Any) -> list[BroadcastResult]:
        results = []
        scope_value = scope.value if hasattr(scope, "value") else str(scope)
        for w in self._workers.values():
            try:
                resp = httpx.post(
                    f"{w.address}/admin/reload",
                    json={"scope": scope_value},
                    timeout=10.0,
                )
                results.append(BroadcastResult(worker_id=w.worker_id, success=resp.status_code == 200))
            except Exception as exc:
                logger.warning("Broadcast to %s failed: %s", w.worker_id, exc)
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error=str(exc)))
        return results

    def broadcast_model_update(
        self, action: str, model_id: str, profile: dict[str, Any]
    ) -> list[BroadcastResult]:
        results = []
        for w in self._workers.values():
            try:
                resp = httpx.post(
                    f"{w.address}/admin/models/sync",
                    json={"action": action, "model_id": model_id, "profile": profile},
                    timeout=10.0,
                )
                results.append(BroadcastResult(worker_id=w.worker_id, success=resp.status_code == 200))
            except Exception as exc:
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error=str(exc)))
        return results

    def ping_all(self) -> dict[str, bool]:
        results = {}
        for w in self._workers.values():
            try:
                resp = httpx.get(f"{w.address}/healthz", timeout=5.0)
                results[w.worker_id] = resp.status_code == 200
            except Exception:
                results[w.worker_id] = False
        return results
