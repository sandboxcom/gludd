"""Cross-provider resource lifecycle manager.

Tracks deployed resources across Azure, AWS, GCP, and RunPod.  Guarantees
cleanup on crash/timeout via atexit + signal handlers, and runs a background
thread that polls for idle resources every 5 minutes.
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TrackedResource:
    provider: str
    instance_id: str
    deploy_dir: str
    registered_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    cleaned_up: bool = False


LIFECYCLE_TARGETS: dict[str, dict[str, Any]] = {
    "azure": {
        "validator_script": "scripts/validate_azure_iam_policy.py",
    },
    "aws": {
        "validator_script": "scripts/validate_aws_iam_policy.py",
    },
    "gcp": {
        "validator_script": "scripts/validate_gcp_iam_policy.py",
    },
    "runpod": {
        "validator_script": "",
    },
}


class ResourceLifecycleManager:
    """Cross-provider resource lifecycle tracker with guaranteed cleanup."""

    _POLL_INTERVAL_S: float = 300.0

    def __init__(self) -> None:
        self._resources: dict[str, TrackedResource] = {}
        self._lock = threading.Lock()
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._destroy_fn: Callable[[str, str], None] | None = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def set_destroy_fn(self, fn: Callable[[str, str], None]) -> None:
        self._destroy_fn = fn

    def register(self, provider: str, instance_id: str, deploy_dir: str) -> None:
        with self._lock:
            tracked = TrackedResource(
                provider=provider,
                instance_id=instance_id,
                deploy_dir=deploy_dir,
            )
            self._resources[instance_id] = tracked
            logger.info(
                "Registered resource %s (provider=%s, dir=%s)",
                instance_id,
                provider,
                deploy_dir,
            )

    def deregister(self, instance_id: str) -> None:
        with self._lock:
            existing = self._resources.get(instance_id)
            if existing is None:
                return
            existing.cleaned_up = True
            del self._resources[instance_id]
            logger.info("Deregistered resource %s", instance_id)

    def is_tracked(self, instance_id: str) -> bool:
        with self._lock:
            return instance_id in self._resources

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def pending_cleanup(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "provider": r.provider,
                    "instance_id": r.instance_id,
                    "deploy_dir": r.deploy_dir,
                    "registered_at": r.registered_at,
                }
                for r in self._resources.values()
                if not r.cleaned_up
            ]

    def all_tracked(self) -> list[TrackedResource]:
        with self._lock:
            return list(self._resources.values())

    def cost_estimate(self, provider: str | None = None) -> float:
        provider_rates: dict[str, float] = {
            "azure": 0.50,
            "aws": 0.55,
            "gcp": 0.52,
            "runpod": 0.49,
        }
        with self._lock:
            total = 0.0
            for r in self._resources.values():
                if r.cleaned_up:
                    continue
                if provider and r.provider != provider:
                    continue
                hours = (time.time() - r.registered_at) / 3600.0
                rate = provider_rates.get(r.provider, 0.50)
                total += max(hours * rate, 0.0)
            return round(total, 4)

    def orphan_report(self) -> list[dict[str, Any]]:
        orphans: list[dict[str, Any]] = []
        with self._lock:
            for r in self._resources.values():
                if r.cleaned_up:
                    continue
                deploy_path = Path(r.deploy_dir)
                if not deploy_path.exists() or not deploy_path.is_dir():
                    orphans.append(
                        {
                            "instance_id": r.instance_id,
                            "provider": r.provider,
                            "deploy_dir": r.deploy_dir,
                            "reason": "deploy directory missing",
                        }
                    )
                elif not any(deploy_path.iterdir()):
                    orphans.append(
                        {
                            "instance_id": r.instance_id,
                            "provider": r.provider,
                            "deploy_dir": r.deploy_dir,
                            "reason": "deploy directory empty",
                        }
                    )
        return orphans

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def cleanup_all(self, provider: str | None = None) -> int:
        cleaned = 0
        with self._lock:
            items = [
                (iid, r)
                for iid, r in self._resources.items()
                if not r.cleaned_up and (provider is None or r.provider == provider)
            ]
        for instance_id, resource in items:
            try:
                if self._destroy_fn:
                    self._destroy_fn(instance_id, resource.deploy_dir)
            except Exception:
                logger.exception("Failed to destroy %s", instance_id)
                continue
            with self._lock:
                if instance_id in self._resources:
                    self._resources[instance_id].cleaned_up = True
                    del self._resources[instance_id]
            cleaned += 1
        return cleaned

    def cleanup_idle(self, provider: str, idle_minutes: int = 10) -> int:
        threshold = time.time() - (idle_minutes * 60)
        with self._lock:
            idle_ids = [
                (iid, r)
                for iid, r in self._resources.items()
                if not r.cleaned_up and r.provider == provider and r.last_activity < threshold
            ]
        cleaned = 0
        for instance_id, resource in idle_ids:
            try:
                if self._destroy_fn:
                    self._destroy_fn(instance_id, resource.deploy_dir)
            except Exception:
                logger.exception("Failed to destroy idle %s", instance_id)
                continue
            with self._lock:
                if instance_id in self._resources:
                    self._resources[instance_id].cleaned_up = True
                    del self._resources[instance_id]
            cleaned += 1
        return cleaned

    # ------------------------------------------------------------------
    # Background poll thread
    # ------------------------------------------------------------------

    def start_background_poll(self) -> None:
        if self._poll_thread is not None and self._poll_thread.is_alive():
            return
        self._stop_event.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name="resource-lifecycle-poll",
        )
        self._poll_thread.start()
        logger.info("Started resource lifecycle background poll thread")

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(self._POLL_INTERVAL_S):
            try:
                with self._lock:
                    providers = {r.provider for r in self._resources.values() if not r.cleaned_up}
                for provider in sorted(providers):
                    self.cleanup_idle(provider, idle_minutes=10)
            except Exception:
                logger.exception("Background poll error")

    def stop_background_poll(self) -> None:
        self._stop_event.set()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
            self._poll_thread = None

    # ------------------------------------------------------------------
    # Guaranteed cleanup on crash / timeout
    # ------------------------------------------------------------------

    def _guaranteed_cleanup(self) -> None:
        logger.warning("Guaranteed cleanup triggered — destroying all tracked resources")
        try:
            self.cleanup_all()
        except Exception:
            logger.exception("Guaranteed cleanup failed")

    def _handle_signal(self, signum: int, _frame: object) -> None:
        logger.warning("Received signal %d — guaranteed cleanup", signum)
        self._guaranteed_cleanup()
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)


# ------------------------------------------------------------------
# Module-level singleton
# ------------------------------------------------------------------

_lifecycle_singleton: ResourceLifecycleManager | None = None
_lifecycle_singleton_lock = threading.Lock()
_signal_handlers_installed = False


def _install_signal_handlers(manager: ResourceLifecycleManager) -> bool:
    """Install lifecycle handlers only from the main interpreter thread.

    A worker thread may be the first caller of :func:`get_lifecycle`; that must
    construct a usable singleton without raising.  A later main-thread call
    retries installation so crash cleanup is not permanently lost.
    """
    global _signal_handlers_installed
    if _signal_handlers_installed:
        return True
    if threading.current_thread() is not threading.main_thread():
        logger.debug("Deferring lifecycle signal handlers to the main thread")
        return False
    try:
        signal.signal(signal.SIGTERM, manager._handle_signal)
        signal.signal(signal.SIGINT, manager._handle_signal)
    except ValueError:
        logger.debug(
            "Deferring lifecycle signal handlers outside the main interpreter",
            exc_info=True,
        )
        return False
    _signal_handlers_installed = True
    return True


def get_lifecycle() -> ResourceLifecycleManager:
    global _lifecycle_singleton
    with _lifecycle_singleton_lock:
        if _lifecycle_singleton is None:
            _lifecycle_singleton = ResourceLifecycleManager()
            atexit.register(_lifecycle_singleton._guaranteed_cleanup)
        manager = _lifecycle_singleton
        _install_signal_handlers(manager)
        return manager
