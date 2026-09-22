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

from general_ludd.projects.identity import ProjectResourceIdentity, validate_project_id

logger = logging.getLogger(__name__)


@dataclass
class TrackedResource:
    """Describe one project-owned provider resource awaiting cleanup."""

    provider: str
    instance_id: str
    deploy_dir: str
    project_id: str = "default"
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
        """Initialize an empty, thread-safe lifecycle registry."""
        self._resources: dict[ProjectResourceIdentity, TrackedResource] = {}
        self._lock = threading.Lock()
        self._poll_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._destroy_fn: Callable[[str, str], None] | None = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def set_destroy_fn(self, fn: Callable[[str, str], None]) -> None:
        """Install the owner callback that destroys one deployed resource."""
        self._destroy_fn = fn

    @staticmethod
    def _key(
        project_id: str,
        provider: str,
        instance_id: str,
    ) -> ProjectResourceIdentity:
        return ProjectResourceIdentity(project_id, provider, instance_id)

    def _matching_keys(
        self,
        instance_id: str | None = None,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> list[ProjectResourceIdentity]:
        return [
            key
            for key, resource in self._resources.items()
            if (instance_id is None or resource.instance_id == instance_id)
            and (provider is None or resource.provider == provider)
            and (project_id is None or resource.project_id == project_id)
        ]

    def _single_key(
        self,
        instance_id: str,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> ProjectResourceIdentity | None:
        matches = self._matching_keys(
            instance_id,
            provider=provider,
            project_id=project_id,
        )
        if len(matches) > 1:
            raise ValueError(
                "ambiguous resource identity; provide project_id and provider for "
                f"instance_id {instance_id!r}"
            )
        return matches[0] if matches else None

    def register(
        self,
        provider: str,
        instance_id: str,
        deploy_dir: str,
        *,
        project_id: str = "default",
    ) -> None:
        """Register a resource under its project, provider, and instance identity."""
        with self._lock:
            tracked = TrackedResource(
                provider=provider,
                instance_id=instance_id,
                deploy_dir=deploy_dir,
                project_id=project_id,
            )
            self._resources[self._key(project_id, provider, instance_id)] = tracked
            logger.info(
                "Registered resource %s (project=%s, provider=%s, dir=%s)",
                instance_id,
                project_id,
                provider,
                deploy_dir,
            )

    def deregister(
        self,
        instance_id: str,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """Remove exactly one tracked resource after confirmed destruction."""
        with self._lock:
            key = self._single_key(
                instance_id,
                provider=provider,
                project_id=project_id,
            )
            if key is None:
                return
            existing = self._resources[key]
            existing.cleaned_up = True
            del self._resources[key]
            logger.info(
                "Deregistered resource %s (project=%s, provider=%s)",
                instance_id,
                existing.project_id,
                existing.provider,
            )

    def is_tracked(
        self,
        instance_id: str,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> bool:
        """Return whether any resource matches the supplied ownership scope."""
        with self._lock:
            return bool(
                self._matching_keys(
                    instance_id,
                    provider=provider,
                    project_id=project_id,
                )
            )

    def touch(
        self,
        instance_id: str,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> None:
        """Record activity for exactly one owned resource."""
        with self._lock:
            key = self._single_key(
                instance_id,
                provider=provider,
                project_id=project_id,
            )
            if key is None:
                raise KeyError(instance_id)
            self._resources[key].last_activity = time.time()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def pending_cleanup(
        self,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return active cleanup records filtered by provider and project."""
        with self._lock:
            return [
                {
                    "project_id": r.project_id,
                    "provider": r.provider,
                    "instance_id": r.instance_id,
                    "deploy_dir": r.deploy_dir,
                    "registered_at": r.registered_at,
                }
                for r in self._resources.values()
                if not r.cleaned_up
                and (provider is None or r.provider == provider)
                and (project_id is None or r.project_id == project_id)
            ]

    def all_tracked(
        self,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> list[TrackedResource]:
        """Return tracked resource objects filtered by provider and project."""
        with self._lock:
            return [
                resource
                for resource in self._resources.values()
                if (provider is None or resource.provider == provider)
                and (project_id is None or resource.project_id == project_id)
            ]

    def cost_estimate(
        self,
        provider: str | None = None,
        *,
        project_id: str | None = None,
    ) -> float:
        """Estimate current hourly spend for the selected ownership scope."""
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
                if project_id and r.project_id != project_id:
                    continue
                hours = (time.time() - r.registered_at) / 3600.0
                rate = provider_rates.get(r.provider, 0.50)
                total += max(hours * rate, 0.0)
            return round(total, 4)

    def orphan_report(
        self,
        *,
        provider: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Report tracked resources whose deployment state directory is absent."""
        orphans: list[dict[str, Any]] = []
        with self._lock:
            for r in self._resources.values():
                if r.cleaned_up:
                    continue
                if provider is not None and r.provider != provider:
                    continue
                if project_id is not None and r.project_id != project_id:
                    continue
                deploy_path = Path(r.deploy_dir)
                if not deploy_path.exists() or not deploy_path.is_dir():
                    orphans.append(
                        {
                            "project_id": r.project_id,
                            "instance_id": r.instance_id,
                            "provider": r.provider,
                            "deploy_dir": r.deploy_dir,
                            "reason": "deploy directory missing",
                        }
                    )
                elif not any(deploy_path.iterdir()):
                    orphans.append(
                        {
                            "project_id": r.project_id,
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

    def _cleanup_matching(
        self,
        *,
        provider: str | None = None,
        project_id: str | None = None,
        idle_before: float | None = None,
    ) -> int:
        with self._lock:
            items = [
                (key, resource)
                for key, resource in self._resources.items()
                if not resource.cleaned_up
                and (provider is None or resource.provider == provider)
                and (project_id is None or resource.project_id == project_id)
                and (idle_before is None or resource.last_activity < idle_before)
            ]
            destroy_fn = self._destroy_fn
        if not items:
            return 0
        if destroy_fn is None:
            logger.error(
                "Refusing to release %d tracked resource(s): no destroy owner",
                len(items),
            )
            return 0
        cleaned = 0
        for key, resource in items:
            try:
                destroy_fn(resource.instance_id, resource.deploy_dir)
            except Exception:
                logger.exception(
                    "Failed to destroy %s (project=%s, provider=%s)",
                    resource.instance_id,
                    resource.project_id,
                    resource.provider,
                )
                continue
            with self._lock:
                existing = self._resources.get(key)
                if existing is resource:
                    existing.cleaned_up = True
                    del self._resources[key]
            cleaned += 1
        return cleaned

    def cleanup_all(
        self,
        provider: str | None = None,
        *,
        project_id: str | None = None,
    ) -> int:
        """Destroy every resource matching the optional ownership filters."""
        return self._cleanup_matching(provider=provider, project_id=project_id)

    def cleanup_project(self, project_id: str, *, provider: str | None = None) -> int:
        """Destroy only resources owned by one project."""
        return self._cleanup_matching(
            provider=provider,
            project_id=validate_project_id(project_id),
        )

    def cleanup_idle(
        self,
        provider: str,
        idle_minutes: int = 10,
        *,
        project_id: str | None = None,
    ) -> int:
        """Destroy resources idle beyond the requested project-scoped threshold."""
        threshold = time.time() - (idle_minutes * 60)
        return self._cleanup_matching(
            provider=provider,
            project_id=project_id,
            idle_before=threshold,
        )

    # ------------------------------------------------------------------
    # Background poll thread
    # ------------------------------------------------------------------

    def start_background_poll(self) -> None:
        """Start the single namespaced idle-resource cleanup thread."""
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
        """Stop and join the idle-resource cleanup thread when present."""
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
    """Return the process lifecycle singleton and install signal ownership."""
    global _lifecycle_singleton
    with _lifecycle_singleton_lock:
        if _lifecycle_singleton is None:
            _lifecycle_singleton = ResourceLifecycleManager()
            atexit.register(_lifecycle_singleton._guaranteed_cleanup)
        manager = _lifecycle_singleton
        _install_signal_handlers(manager)
        return manager
