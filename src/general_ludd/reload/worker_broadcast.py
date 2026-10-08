"""PSK-secured reload/model-sync broadcast from the daemon to registered workers."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from general_ludd.reload.worker_liveness import (
    MAX_WORKERS as _MAX_WORKERS,
)
from general_ludd.reload.worker_liveness import (
    NANOSECONDS_PER_SECOND as _NANOSECONDS_PER_SECOND,
)
from general_ludd.reload.worker_liveness import (
    enforcement_enabled as _liveness_enforced,
)
from general_ludd.reload.worker_liveness import (
    probe_deadline_ns,
    probe_timeout_seconds,
)
from general_ludd.security import is_safe_fetch_url

logger = logging.getLogger(__name__)

def _is_safe_worker_address(address: str) -> bool:
    """Apply the canonical no-I/O HTTPS/SSRF policy before sending a PSK."""
    return is_safe_fetch_url(address)


@dataclass
class WorkerInfo:
    """Registry entry for one worker: id, https address, and liveness stamps."""

    worker_id: str
    address: str
    registered_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class BroadcastResult:
    """Per-worker outcome of one broadcast attempt."""

    worker_id: str
    success: bool
    error: str | None = None


class WorkerBroadcaster:
    """Thread-safe registry that broadcasts reloads/model updates to workers."""

    def __init__(
        self,
        stale_threshold_seconds: float = 300.0,
        allowlist: set[str] | None = None,
        post: Callable[..., httpx.Response] | None = None,
        get: Callable[..., httpx.Response] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
        liveness_lease_seconds: float = 30.0,
    ) -> None:
        """Initialize the registry, policy, and instance-owned HTTP transport."""
        if liveness_lease_seconds <= 0:
            raise ValueError("liveness_lease_seconds must be positive")
        self._workers: dict[str, WorkerInfo] = {}
        self._lease_renewed_ns: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stale_threshold = stale_threshold_seconds
        self._monotonic_ns = monotonic_ns or time.monotonic_ns
        self._liveness_lease_ns = int(liveness_lease_seconds * _NANOSECONDS_PER_SECOND)
        # Bind once per broadcaster so concurrent callers cannot replace or
        # restore a mutable module-global HTTP function underneath one another.
        # Lazy binding preserves the existing patch-before-first-call seam.
        self._post: Callable[..., httpx.Response] | None = post
        self._get: Callable[..., httpx.Response] | None = get
        # Defense-in-depth worker-identity allowlist (task #18). When configured,
        # the daemon only broadcasts a reload / model-sync — and, critically, the
        # PSK Bearer header — to workers whose ``worker_id`` OR ``address`` appears
        # in this set, even if some other address slipped past registration and the
        # send-time SSRF guard. ``None`` (the default) means "not configured via the
        # constructor" and defers to the ``GLUDD_WORKER_ALLOWLIST`` env var.
        self._allowlist: set[str] | None = allowlist

    def _resolve_allowlist(self) -> set[str]:
        """Resolve the effective worker allowlist.

        Precedence: an explicit constructor ``allowlist`` (when not ``None``) wins;
        otherwise the ``GLUDD_WORKER_ALLOWLIST`` environment variable is parsed as a
        comma-separated set of permitted ``worker_id`` and/or ``host:port``
        addresses (whitespace-trimmed, blanks dropped). An **empty** set means "no
        allowlist configured" — broadcasts stay unrestricted (see
        :meth:`broadcast_reload`). Read on each broadcast, mirroring how the PSK
        itself is read per-call in :meth:`_auth_headers`, so an operator can tighten
        the allowlist without restarting the daemon.
        """
        if self._allowlist is not None:
            return self._allowlist
        raw = os.environ.get("GLUDD_WORKER_ALLOWLIST", "")
        return {entry.strip() for entry in raw.split(",") if entry.strip()}

    @staticmethod
    def _is_allowlisted(worker: WorkerInfo, allowlist: set[str]) -> bool:
        """A worker is permitted when either its id or its address is listed."""
        return worker.worker_id in allowlist or worker.address in allowlist

    def register(self, worker: WorkerInfo) -> bool:
        """Add or renew a worker after verifying its safe https target.

        New identities are bounded to 64 entries. Re-registering an existing
        identity remains available at capacity so rolling worker replacement
        can update its address and renew its liveness lease without a gap.
        """
        # SSRF / PSK-leak guard: never register a worker whose address is not a
        # safe https target. Sending the daemon PSK (broadcast_reload /
        # broadcast_model_update attach `Authorization: Bearer <GLUDD_AUTH_PSK>`) to a
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
            return False
        renewed_ns = self._monotonic_ns()
        with self._lock:
            if worker.worker_id not in self._workers and len(self._workers) >= _MAX_WORKERS:
                logger.warning(
                    "Refusing to register worker %s: registry capacity of %d reached",
                    worker.worker_id,
                    _MAX_WORKERS,
                )
                return False
            self._workers[worker.worker_id] = worker
            self._lease_renewed_ns[worker.worker_id] = renewed_ns
        return True

    def unregister(self, worker_id: str) -> None:
        """Remove a worker from the registry by id."""
        with self._lock:
            self._workers.pop(worker_id, None)
            self._lease_renewed_ns.pop(worker_id, None)

    def heartbeat(self, worker_id: str) -> None:
        """Refresh the last-seen timestamp for one worker."""
        with self._lock:
            w = self._workers.get(worker_id)
            if w:
                w.last_seen = time.time()
                self._lease_renewed_ns[worker_id] = self._monotonic_ns()

    def _snapshot_workers(self) -> list[WorkerInfo]:
        with self._lock:
            return list(self._workers.values())

    def _post_request(
        self,
        url: str,
        *,
        payload: dict[str, object],
        headers: dict[str, str],
    ) -> httpx.Response:
        """Send through the broadcaster's stable, lazily bound transport."""
        with self._lock:
            if self._post is None:
                self._post = httpx.post
            post = self._post
        return post(
            url,
            json=payload,
            headers=headers,
            timeout=10.0,
            follow_redirects=False,
            verify=True,
        )

    def _get_request(self, url: str, *, timeout: float) -> httpx.Response:
        """Send an unauthenticated health probe through the stable transport."""
        with self._lock:
            if self._get is None:
                self._get = httpx.get
            get = self._get
        return get(
            url,
            timeout=timeout,
            follow_redirects=False,
            verify=True,
        )

    def _lease_is_active(self, worker: WorkerInfo) -> bool:
        """Check one process-local monotonic lease without wall-clock input."""
        now_ns = self._monotonic_ns()
        with self._lock:
            if self._workers.get(worker.worker_id) is not worker:
                return False
            renewed_ns = self._lease_renewed_ns.get(worker.worker_id)
        return renewed_ns is not None and now_ns < renewed_ns + self._liveness_lease_ns

    def _renew_lease(self, worker: WorkerInfo) -> bool:
        """Renew the lease only if the probed snapshot is still registered."""
        renewed_ns = self._monotonic_ns()
        with self._lock:
            if self._workers.get(worker.worker_id) is not worker:
                return False
            worker.last_seen = time.time()
            self._lease_renewed_ns[worker.worker_id] = renewed_ns
        return True

    def _probe_liveness(self, worker: WorkerInfo, *, deadline_ns: int) -> bool:
        """Run one bounded, public health probe and renew on HTTP 200."""
        if not _is_safe_worker_address(worker.address):
            return False
        remaining_ns = deadline_ns - self._monotonic_ns()
        if remaining_ns <= 0:
            return False
        timeout = probe_timeout_seconds(remaining_ns)
        try:
            response = self._get_request(f"{worker.address}/healthz", timeout=timeout)
        except Exception as exc:
            logger.warning("Liveness probe to %s failed: %s", worker.worker_id, exc)
            return False
        if response.status_code != 200:
            logger.warning(
                "Liveness probe to %s returned HTTP %d",
                worker.worker_id,
                response.status_code,
            )
            return False
        return self._renew_lease(worker)

    def _broadcast_guard_error(
        self,
        worker: WorkerInfo,
        *,
        allowlist: set[str],
        operation: str,
        require_live_lease: bool,
        probe_deadline_ns: int,
    ) -> str | None:
        """Fail closed before a credentialed transport is invoked."""
        if allowlist and not self._is_allowlisted(worker, allowlist):
            logger.warning(
                "Skipping %s broadcast to %s: worker id/address %r is not in "
                "the configured worker allowlist — not sending the daemon PSK",
                operation,
                worker.worker_id,
                worker.address,
            )
            return "not allowlisted"
        if not _is_safe_worker_address(worker.address):
            logger.warning(
                "Skipping %s broadcast to %s: address %r is not a safe https "
                "target — not sending the daemon PSK to it",
                operation,
                worker.worker_id,
                worker.address,
            )
            return "unsafe address"
        if (
            require_live_lease
            and not self._lease_is_active(worker)
            and not self._probe_liveness(worker, deadline_ns=probe_deadline_ns)
        ):
            logger.warning(
                "Skipping %s broadcast to %s: liveness lease expired and the "
                "bounded public health probe failed — not sending the daemon PSK",
                operation,
                worker.worker_id,
            )
            return "liveness check failed"
        return None

    def list_workers(self) -> list[WorkerInfo]:
        """Return a snapshot of all registered workers."""
        with self._lock:
            return list(self._workers.values())

    def cleanup_stale(self) -> None:
        """Drop workers whose last heartbeat is older than the threshold."""
        now = time.time()
        with self._lock:
            stale = [wid for wid, w in self._workers.items() if now - w.last_seen > self._stale_threshold]
            for wid in stale:
                self._workers.pop(wid, None)
                self._lease_renewed_ns.pop(wid, None)

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """Attach the daemon PSK as a Bearer token for secured worker POSTs.

        Without this the reload/model-sync broadcasts 401 silently and the
        fleet never converges. Fail-open only when no PSK is configured (auth
        disabled).
        """
        psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
        return {"Authorization": f"Bearer {psk}"} if psk else {}

    def _broadcast(
        self,
        *,
        operation: str,
        endpoint: str,
        payload: dict[str, object],
    ) -> list[BroadcastResult]:
        """Run the common guarded credentialed broadcast pipeline."""
        results: list[BroadcastResult] = []
        headers = self._auth_headers()
        allowlist = self._resolve_allowlist()
        require_live_lease = bool(headers) and _liveness_enforced()
        probe_deadline = probe_deadline_ns(self._monotonic_ns())
        if not allowlist:
            logger.warning(
                "No worker allowlist configured (GLUDD_WORKER_ALLOWLIST unset/empty)"
                ": %s broadcast is UNRESTRICTED — the daemon PSK will be sent to "
                "every registered safe worker. Set GLUDD_WORKER_ALLOWLIST to restrict.",
                operation,
            )
        for w in self._snapshot_workers():
            guard_error = self._broadcast_guard_error(
                w,
                allowlist=allowlist,
                operation=operation,
                require_live_lease=require_live_lease,
                probe_deadline_ns=probe_deadline,
            )
            if guard_error is not None:
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error=guard_error))
                continue
            try:
                resp = self._post_request(
                    f"{w.address}{endpoint}",
                    payload=payload,
                    headers=headers,
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

    def broadcast_reload(self, scope: object) -> list[BroadcastResult]:
        """POST a reload with the given scope to every eligible worker."""
        scope_value = scope.value if hasattr(scope, "value") else str(scope)
        return self._broadcast(
            operation="reload",
            endpoint="/admin/reload",
            payload={"scope": scope_value},
        )

    def broadcast_model_update(self, action: str, model_id: str, profile: dict[str, object]) -> list[BroadcastResult]:
        """POST a model sync action for one model to every eligible worker."""
        return self._broadcast(
            operation="model-update",
            endpoint="/admin/models/sync",
            payload={"action": action, "model_id": model_id, "profile": profile},
        )

    def ping_all(self) -> dict[str, bool]:
        """Health-check every worker's /healthz endpoint; worker_id -> reachable."""
        results = {}
        probe_deadline = probe_deadline_ns(self._monotonic_ns())
        for w in self._snapshot_workers():
            # Defense in depth (task #37): re-validate the address at send time,
            # identically to the PSK-bearing broadcast_* methods, so the health
            # probe is NEVER issued to a plain-http / loopback / link-local /
            # cloud-metadata target even if one slipped into the registry. No PSK
            # is sent here so it is lower risk, but the re-validate-on-send
            # invariant must hold uniformly across every worker-contacting path.
            if not _is_safe_worker_address(w.address):
                logger.warning(
                    "Skipping ping to %s: address %r is not a safe https target "
                    "(must be https and not loopback/link-local/RFC-1918/"
                    "cloud-metadata) — treating as unreachable",
                    w.worker_id,
                    w.address,
                )
                results[w.worker_id] = False
                continue
            results[w.worker_id] = self._probe_liveness(w, deadline_ns=probe_deadline)
        return results
