from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from general_ludd.security import is_safe_fetch_url

logger = logging.getLogger(__name__)


def _is_safe_worker_address(address: str) -> bool:
    """SSRF guard for a worker address BEFORE the daemon PSK is ever sent to it.

    Delegates to the canonical :func:`general_ludd.security.is_safe_fetch_url`
    (https-only + literal-host deny) — the SAME guard the webhook-registration
    endpoint uses — so the policy can never drift. Returns ``True`` only when the
    address uses ``https`` and does not target a loopback / link-local /
    RFC-1918 / cloud-metadata (``169.254.169.254``, ``::1``, ``127.0.0.0/8`` …)
    host. A worker registered with a plain-http or metadata/loopback address
    would otherwise receive the ``Authorization: Bearer <GLUDD_PSK>`` header in
    cleartext or exfiltrate it to an attacker/SSRF target. Performs NO DNS
    resolution and NO network I/O, so it is safe on the broadcast hot path.
    """
    return is_safe_fetch_url(address)


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
        # SSRF / PSK-leak guard: never register a worker whose address is not a
        # safe https target. Sending the daemon PSK (broadcast_reload /
        # broadcast_model_update attach `Authorization: Bearer <GLUDD_PSK>`) to a
        # plain-http, loopback, link-local, or cloud-metadata address would leak
        # the credential in cleartext or to an attacker. Fail closed: refuse to
        # store the worker and warn, rather than crash the caller.
        if not _is_safe_worker_address(worker.address):
            logger.warning(
                "Refusing to register worker %s: address %r is not a safe https "
                "target (must be https and not loopback/link-local/RFC-1918/"
                "cloud-metadata) — the daemon PSK is never sent to it",
                worker.worker_id,
                worker.address,
            )
            return
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

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """Attach the daemon PSK as a Bearer token so internal /admin POSTs are
        accepted by a secured worker (GLUDD_REQUIRE_AUTH). Without this the
        reload/model-sync broadcasts 401 silently and the fleet never converges.
        Fail-open only when no PSK is configured (auth disabled)."""
        psk = os.environ.get("GLUDD_PSK", "")
        return {"Authorization": f"Bearer {psk}"} if psk else {}

    def broadcast_reload(self, scope: Any) -> list[BroadcastResult]:
        results = []
        scope_value = scope.value if hasattr(scope, "value") else str(scope)
        headers = self._auth_headers()
        for w in self._workers.values():
            # Defense in depth: re-validate the address at send time so the PSK
            # Bearer header is NEVER POSTed to a plain-http / loopback / link-local
            # / cloud-metadata target, even if one slipped into the registry.
            if not _is_safe_worker_address(w.address):
                logger.warning(
                    "Skipping reload broadcast to %s: address %r is not a safe "
                    "https target — not sending the daemon PSK to it",
                    w.worker_id,
                    w.address,
                )
                results.append(
                    BroadcastResult(worker_id=w.worker_id, success=False, error="unsafe address")
                )
                continue
            try:
                resp = httpx.post(
                    f"{w.address}/admin/reload",
                    json={"scope": scope_value},
                    headers=headers,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    results.append(BroadcastResult(worker_id=w.worker_id, success=True))
                elif resp.status_code == 401:
                    logger.error(
                        "Broadcast to %s rejected (401): PSK mismatch or auth misconfiguration",
                        w.worker_id,
                    )
                    results.append(BroadcastResult(worker_id=w.worker_id, success=False, error="Unauthorized"))
                else:
                    results.append(
                        BroadcastResult(worker_id=w.worker_id, success=False, error=f"HTTP {resp.status_code}")
                    )
            except Exception as exc:
                logger.warning("Broadcast to %s failed: %s", w.worker_id, exc)
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error=str(exc)))
        return results

    def broadcast_model_update(
        self, action: str, model_id: str, profile: dict[str, Any]
    ) -> list[BroadcastResult]:
        results = []
        headers = self._auth_headers()
        for w in self._workers.values():
            # Defense in depth: re-validate the address at send time so the PSK
            # Bearer header is NEVER POSTed to a plain-http / loopback / link-local
            # / cloud-metadata target, even if one slipped into the registry.
            if not _is_safe_worker_address(w.address):
                logger.warning(
                    "Skipping model-update broadcast to %s: address %r is not a "
                    "safe https target — not sending the daemon PSK to it",
                    w.worker_id,
                    w.address,
                )
                results.append(
                    BroadcastResult(worker_id=w.worker_id, success=False, error="unsafe address")
                )
                continue
            try:
                resp = httpx.post(
                    f"{w.address}/admin/models/sync",
                    json={"action": action, "model_id": model_id, "profile": profile},
                    headers=headers,
                    timeout=10.0,
                )
                if resp.status_code == 200:
                    results.append(BroadcastResult(worker_id=w.worker_id, success=True))
                elif resp.status_code == 401:
                    logger.error(
                        "Broadcast to %s rejected (401): PSK mismatch or auth misconfiguration",
                        w.worker_id,
                    )
                    results.append(BroadcastResult(worker_id=w.worker_id, success=False, error="Unauthorized"))
                else:
                    results.append(
                        BroadcastResult(worker_id=w.worker_id, success=False, error=f"HTTP {resp.status_code}")
                    )
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
