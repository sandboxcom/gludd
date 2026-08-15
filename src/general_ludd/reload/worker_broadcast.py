"""PSK-secured reload/model-sync broadcast from the daemon to registered workers."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

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
    would otherwise receive the ``Authorization: Bearer <GLUDD_AUTH_PSK>`` header in
    cleartext or exfiltrate it to an attacker/SSRF target. Performs NO DNS
    resolution and NO network I/O, so it is safe on the broadcast hot path.
    """
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
    ) -> None:
        """Initialize the registry with a staleness threshold and allowlist."""
        self._workers: dict[str, WorkerInfo] = {}
        self._lock = threading.Lock()
        self._stale_threshold = stale_threshold_seconds
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

    def register(self, worker: WorkerInfo) -> None:
        """Add a worker after verifying its address is a safe https target."""
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
            return
        with self._lock:
            self._workers[worker.worker_id] = worker

    def unregister(self, worker_id: str) -> None:
        """Remove a worker from the registry by id."""
        with self._lock:
            self._workers.pop(worker_id, None)

    def heartbeat(self, worker_id: str) -> None:
        """Refresh the last-seen timestamp for one worker."""
        with self._lock:
            w = self._workers.get(worker_id)
            if w:
                w.last_seen = time.time()

    def _snapshot_workers(self) -> list[WorkerInfo]:
        with self._lock:
            return list(self._workers.values())

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

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        """Attach the daemon PSK as a Bearer token for secured worker POSTs.

        Without this the reload/model-sync broadcasts 401 silently and the
        fleet never converges. Fail-open only when no PSK is configured (auth
        disabled).
        """
        psk = os.environ.get("GLUDD_AUTH_PSK", "").strip()
        return {"Authorization": f"Bearer {psk}"} if psk else {}

    def broadcast_reload(self, scope: object) -> list[BroadcastResult]:
        """POST a reload with the given scope to every eligible worker."""
        results = []
        scope_value = scope.value if hasattr(scope, "value") else str(scope)
        headers = self._auth_headers()
        allowlist = self._resolve_allowlist()
        if not allowlist:
            logger.warning(
                "No worker allowlist configured (GLUDD_WORKER_ALLOWLIST unset/empty)"
                ": reload broadcast is UNRESTRICTED — the daemon PSK will be sent to "
                "every registered safe worker. Set GLUDD_WORKER_ALLOWLIST to restrict."
            )
        for w in self._snapshot_workers():
            # Defense in depth (allowlist gate, task #18): only broadcast — and only
            # send the PSK — to explicitly permitted workers. Checked BEFORE the SSRF
            # guard so a non-allowlisted target is refused outright.
            if allowlist and not self._is_allowlisted(w, allowlist):
                logger.warning(
                    "Skipping reload broadcast to %s: worker id/address %r is not in "
                    "the configured worker allowlist — not sending the daemon PSK",
                    w.worker_id,
                    w.address,
                )
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error="not allowlisted"))
                continue
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
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error="unsafe address"))
                continue
            try:
                resp = httpx.post(
                    f"{w.address}/admin/reload",
                    json={"scope": scope_value},
                    headers=headers,
                    timeout=10.0,
                    # Defense in depth (task #37): make the SSRF-via-redirect and
                    # TLS guarantees independent of httpx defaults. follow_redirects
                    # =False prevents an SSRF-validated URL from being redirected to
                    # an internal host (leaking the PSK) AFTER the check; verify=True
                    # enforces TLS certificate verification.
                    follow_redirects=False,
                    verify=True,
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

    def broadcast_model_update(self, action: str, model_id: str, profile: dict[str, object]) -> list[BroadcastResult]:
        """POST a model sync action for one model to every eligible worker."""
        results = []
        headers = self._auth_headers()
        allowlist = self._resolve_allowlist()
        if not allowlist:
            logger.warning(
                "No worker allowlist configured (GLUDD_WORKER_ALLOWLIST unset/empty)"
                ": model-update broadcast is UNRESTRICTED — the daemon PSK will be "
                "sent to every registered safe worker. Set GLUDD_WORKER_ALLOWLIST to "
                "restrict."
            )
        for w in self._snapshot_workers():
            # Defense in depth (allowlist gate, task #18): only broadcast — and only
            # send the PSK — to explicitly permitted workers. Checked BEFORE the SSRF
            # guard so a non-allowlisted target is refused outright.
            if allowlist and not self._is_allowlisted(w, allowlist):
                logger.warning(
                    "Skipping model-update broadcast to %s: worker id/address %r is "
                    "not in the configured worker allowlist — not sending the PSK",
                    w.worker_id,
                    w.address,
                )
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error="not allowlisted"))
                continue
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
                results.append(BroadcastResult(worker_id=w.worker_id, success=False, error="unsafe address"))
                continue
            try:
                resp = httpx.post(
                    f"{w.address}/admin/models/sync",
                    json={"action": action, "model_id": model_id, "profile": profile},
                    headers=headers,
                    timeout=10.0,
                    # Defense in depth (task #37): make the SSRF-via-redirect and
                    # TLS guarantees independent of httpx defaults. follow_redirects
                    # =False prevents an SSRF-validated URL from being redirected to
                    # an internal host (leaking the PSK) AFTER the check; verify=True
                    # enforces TLS certificate verification.
                    follow_redirects=False,
                    verify=True,
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
        """Health-check every worker's /healthz endpoint; worker_id -> reachable."""
        results = {}
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
            try:
                resp = httpx.get(
                    f"{w.address}/healthz",
                    timeout=5.0,
                    follow_redirects=False,
                    verify=True,
                )
                results[w.worker_id] = resp.status_code == 200
            except Exception:
                results[w.worker_id] = False
        return results
