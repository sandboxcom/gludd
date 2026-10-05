"""Deployment health checking and self-healing routing for model deployments.

Wraps :class:`ModelHealthTracker` as an underlying circuit breaker per
model_id, with deployment→model mapping, content quality checks, latency
tracking, and a structured :class:`DeploymentIncidentLog`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Protocol, cast

from general_ludd.models.failover import ModelFailoverChain
from general_ludd.models.timeout_detector import (
    ModelHealthTracker,
    TimeoutClassifier,
    TimeoutEvent,
    TimeoutKind,
)
from general_ludd.util.async_lifecycle import cancel_and_drain_tasks

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InvalidContentError(ValueError):
    """Raised when content quality checks fail."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DeploymentIncident:
    """Structured incident recorded by the router.

    Persisted to :class:`DeploymentIncidentLog` and (optionally) the audit
    repository.
    """

    timestamp: float
    deployment_id: str
    model_id: str
    error_type: str
    error_message: str
    routed_to: str | None = None
    remediation_attempted: list[str] | None = None
    remediation_result: str | None = None

    def to_audit_details(self) -> dict[str, str | float | list[str] | None]:
        """Render this incident as a JSON-safe audit detail dict."""
        details: dict[str, str | float | list[str] | None] = {
            "deployment_id": self.deployment_id,
            "model_id": self.model_id,
            "timestamp": self.timestamp,
            "error_type": self.error_type,
            "error_message": self.error_message,
        }
        if self.routed_to is not None:
            details["routed_to"] = self.routed_to
        if self.remediation_attempted is not None:
            details["remediation_attempted"] = self.remediation_attempted
        if self.remediation_result is not None:
            details["remediation_result"] = self.remediation_result
        return details


@dataclass
class DeploymentHealthIncident:
    """Lightweight incident record kept inside :class:`DeploymentHealthChecker`."""

    deployment_id: str
    timestamp: float
    kind: str
    detail: str
    was_remediated: bool = False


@dataclass
class DeploymentStatus:
    """Snapshot of a single deployment's health."""

    deployment_id: str
    healthy: bool = True
    last_check: float = 0.0
    consecutive_failures: int = 0
    last_error: str | None = None


@dataclass
class ContentQualityCheck:
    """Validates generated content against configurable quality criteria."""

    non_empty: bool = False
    min_length: int | None = None
    parseable_json: bool = False
    max_length: int | None = None

    def evaluate(self, content: str) -> tuple[bool, str]:
        """Evaluate content against the configured quality gates."""
        if self.non_empty and not content:
            return False, "Content is empty"
        if self.min_length is not None and len(content) < self.min_length:
            return False, (f"Content too short: {len(content)} < {self.min_length}")
        if self.max_length is not None and len(content) > self.max_length:
            return False, (f"Content too long: {len(content)} > {self.max_length}")
        if self.parseable_json:
            try:
                json.loads(content)
            except (json.JSONDecodeError, ValueError):
                return False, "Content is not parseable JSON"
        return True, "OK"


# ---------------------------------------------------------------------------
# DeploymentIncidentLog
# ---------------------------------------------------------------------------


class AuditRepo(Protocol):
    """Structural type for an async audit-event repository."""

    async def create(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        project_id: str | None = None,
        details: str | None = None,
    ) -> None:
        """Persist an audit event asynchronously."""


class DeploymentIncidentLog:
    """Structured, optionally-persisted log of :class:`DeploymentIncident`.

    When an ``audit_repo``
    (:class:`~general_ludd.db.repository.AuditEventRepository`) is provided,
    every incident is persisted asynchronously.  Falls back to in-memory-only
    storage when no repo is given or no event loop is running.
    """

    def __init__(
        self,
        audit_repo: AuditRepo | None = None,
        max_in_memory: int = 1000,
        project_id: str | None = None,
        in_memory: bool = False,
    ) -> None:
        """Initialise the log with an optional async audit repository."""
        self._audit_repo = audit_repo
        self._max_in_memory = max_in_memory
        self._project_id = project_id
        self._incidents: list[DeploymentIncident] = []
        self._lock = threading.RLock()
        self._persistence_tasks: set[asyncio.Task[None]] = set()

    # -- public API ---------------------------------------------------------

    def record(self, incident: DeploymentIncident) -> None:
        """Record an incident in memory and (optionally) persist it."""
        with self._lock:
            self._incidents.append(incident)
            while len(self._incidents) > self._max_in_memory:
                self._incidents.pop(0)
        if self._audit_repo is not None:
            self._persist_async(incident)

    def get_incidents(self) -> list[DeploymentIncident]:
        """Return a copy of all in-memory incidents."""
        with self._lock:
            return list(self._incidents)

    # -- persistence helpers ------------------------------------------------

    def _persist_async(self, incident: DeploymentIncident) -> None:
        """Schedule persistence on the running event loop, if one exists."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._persist(incident))
        self._persistence_tasks.add(task)
        task.add_done_callback(self._persistence_tasks.discard)

    async def aclose(self) -> None:
        """Cancel and await every persistence task owned by this log."""
        await cancel_and_drain_tasks(
            self._persistence_tasks,
            registry=self._persistence_tasks,
        )

    async def _persist(self, incident: DeploymentIncident) -> None:
        assert self._audit_repo is not None  # guarded by caller
        try:
            await self._audit_repo.create(
                event_type="deployment_incident",
                entity_type="deployment",
                entity_id=incident.deployment_id,
                project_id=self._project_id,
                details=json.dumps(
                    {
                        "model_id": incident.model_id,
                        "error_type": incident.error_type,
                        "error_message": incident.error_message,
                        "routed_to": incident.routed_to,
                        "remediation_attempted": incident.remediation_attempted,
                        "remediation_result": incident.remediation_result,
                        "timestamp": incident.timestamp,
                    }
                ),
            )
        except Exception:
            logger.exception("Failed to persist DeploymentIncident to audit repo")


# ---------------------------------------------------------------------------
# DeploymentHealthChecker
# ---------------------------------------------------------------------------


class DeploymentHealthChecker:
    """Tracks health of model deployments and checks before model calls.

    Delegates circuit-breaking to :class:`ModelHealthTracker` (one per
    ``model_id``), maintaining a deployment→model mapping internally.

    Public API: ``is_healthy``, ``record_failure``, ``record_success``,
    ``get_status``, ``all_statuses``, ``get_incidents``, ``force_remediate``.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_interval: float = 60.0,
        max_content_length: int | None = None,
        check_json: bool = False,
        max_avg_latency_s: float | None = None,
        max_latency_samples: int = 50,
        incident_log: DeploymentIncidentLog | None = None,
        content_quality: ContentQualityCheck | None = None,
    ) -> None:
        """Initialise the checker with thresholds, trackers, and optional log."""
        self._failure_threshold = failure_threshold
        self._recovery_interval = recovery_interval
        self._max_content_length = max_content_length
        self._check_json = check_json
        self._max_avg_latency_s = max_avg_latency_s
        self._max_incidents = 1000
        self._content_quality = content_quality

        self._model_tracker = ModelHealthTracker(
            failure_threshold=failure_threshold,
            cooldown_seconds=recovery_interval,
        )

        self._deployment_to_model: dict[str, str] = {}
        self._incident_log = incident_log or DeploymentIncidentLog()
        self._statuses: dict[str, DeploymentStatus] = {}
        self._incidents: list[DeploymentHealthIncident] = []
        self._latency_samples: dict[str, deque[float]] = {}
        self._max_latency_samples = max_latency_samples
        self._lock = threading.RLock()

    async def aclose(self) -> None:
        """Drain persistence owned by the incident log."""
        await self._incident_log.aclose()

    # -- deployment → model mapping -----------------------------------------

    def set_deployment_model(self, deployment_id: str, model_id: str) -> None:
        """Map a deployment id to the model id it serves."""
        with self._lock:
            self._deployment_to_model[deployment_id] = model_id

    def _get_model_id(self, deployment_id: str) -> str:
        with self._lock:
            return self._deployment_to_model.get(deployment_id, deployment_id)

    # -- health checks ------------------------------------------------------

    def is_healthy(self, deployment_id: str) -> bool:
        """Report whether the deployment (and its model) is healthy."""
        with self._lock:
            status = self._statuses.get(deployment_id)
        if status is not None and not status.healthy:
            if time.time() - status.last_check > self._recovery_interval:
                with self._lock:
                    status.healthy = True
                    status.consecutive_failures = 0
                    status.last_error = None
                logger.info(
                    "Deployment %s auto-recovered after %.0fs",
                    deployment_id,
                    self._recovery_interval,
                )
            else:
                return False

        model_id = self._get_model_id(deployment_id)
        healthy = self._model_tracker.is_healthy(model_id)
        if healthy and self._max_avg_latency_s is not None:
            with self._lock:
                samples = self._latency_samples.get(deployment_id)
                if samples and len(samples) > 0:
                    avg = sum(samples) / len(samples)
                    if avg > self._max_avg_latency_s:
                        return False
        return healthy

    # -- content quality ----------------------------------------------------

    def check_content(self, deployment_id: str, content: object) -> tuple[bool, str]:
        """Run content-quality checks and return (ok, reason)."""
        if self._content_quality is not None and isinstance(content, str):
            ok, reason = self._content_quality.evaluate(content)
            if not ok:
                return False, reason

        if content is None:
            return False, "Content is None"

        if isinstance(content, str) and not content.strip():
            return False, "Content is empty"

        if self._check_json:
            if isinstance(content, str):
                try:
                    json.loads(content)
                except json.JSONDecodeError as exc:
                    return False, f"Content is not valid JSON: {exc}"
            elif isinstance(content, (dict, list)):
                pass
            else:
                return False, (f"Content is not JSON-compatible: {type(content).__name__}")

        if self._max_content_length is not None:
            if isinstance(content, str):
                length = len(content)
            elif isinstance(content, (dict, list)):
                length = len(json.dumps(content))
            else:
                length = len(str(content))
            if length > self._max_content_length:
                return False, (f"Content length {length} exceeds max {self._max_content_length}")

        return True, "Content passes quality checks"

    def validate_content(self, content: object) -> None:
        """Validate content and raise InvalidContentError on failure."""
        if self._content_quality is not None and isinstance(content, str):
            ok, reason = self._content_quality.evaluate(content)
            if not ok:
                raise InvalidContentError(reason)
        ok, reason = self.check_content("", content)
        if not ok:
            raise InvalidContentError(reason)

    # -- latency tracking ---------------------------------------------------

    def record_latency(self, deployment_id: str, latency_s: float) -> None:
        """Append a latency sample for the deployment."""
        with self._lock:
            samples = self._latency_samples.setdefault(deployment_id, deque(maxlen=self._max_latency_samples))
            samples.append(latency_s)

    # -- failure / success recording ---------------------------------------

    def record_failure(
        self,
        deployment_id: str,
        error: str | Exception,
        kind: str = "error",
    ) -> None:
        """Record a failed call: increment counters, log an incident."""
        model_id = self._get_model_id(deployment_id)

        timeout_kind: TimeoutKind
        if isinstance(error, Exception):
            timeout_kind = TimeoutClassifier.classify(error)
        else:
            try:
                timeout_kind = TimeoutKind(kind)
            except ValueError:
                timeout_kind = TimeoutKind.UNKNOWN

        event = TimeoutEvent(
            model_id=model_id,
            kind=timeout_kind,
            timestamp=time.monotonic(),
            duration_s=0.0,
        )
        self._model_tracker.record_event(event)

        error_str = str(error)[:500]

        with self._lock:
            status = self._statuses.get(deployment_id)
            if status is None:
                status = DeploymentStatus(deployment_id=deployment_id)
                self._statuses[deployment_id] = status
            status.last_check = time.time()
            status.consecutive_failures += 1
            status.last_error = error_str
            if status.consecutive_failures >= self._failure_threshold:
                status.healthy = False
                logger.warning(
                    "Deployment %s marked unhealthy after %d consecutive failures",
                    deployment_id,
                    status.consecutive_failures,
                )
            self._add_incident(deployment_id, kind, error_str)

        dep_incident = DeploymentIncident(
            timestamp=time.time(),
            deployment_id=deployment_id,
            model_id=model_id,
            error_type=timeout_kind.value,
            error_message=error_str,
        )
        self._incident_log.record(dep_incident)

    def record_success(self, deployment_id: str) -> None:
        """Record a successful call: recover or reset the failure counter."""
        model_id = self._get_model_id(deployment_id)
        self._model_tracker.record_success(model_id)

        with self._lock:
            status = self._statuses.get(deployment_id)
            if status is None:
                status = DeploymentStatus(deployment_id=deployment_id)
                self._statuses[deployment_id] = status
            status.last_check = time.time()
            if not status.healthy:
                status.consecutive_failures = max(0, status.consecutive_failures - 1)
                if status.consecutive_failures == 0:
                    status.healthy = True
                    status.last_error = None
                    logger.info(
                        "Deployment %s recovered after successful call",
                        deployment_id,
                    )
            else:
                # A success while still healthy clears any below-threshold
                # failure streak so a single transient error never lingers.
                status.consecutive_failures = 0

    # -- status queries -----------------------------------------------------

    def get_status(self, deployment_id: str) -> DeploymentStatus:
        """Return the status for a deployment, creating it when unknown."""
        with self._lock:
            if deployment_id not in self._statuses:
                self._statuses[deployment_id] = DeploymentStatus(deployment_id=deployment_id)
            return self._statuses[deployment_id]

    def all_statuses(self) -> dict[str, DeploymentStatus]:
        """Return a snapshot dict of all deployment statuses."""
        with self._lock:
            return dict(self._statuses)

    def get_incidents(self, limit: int = 100) -> list[DeploymentHealthIncident]:
        """Return up to *limit* recent incidents."""
        with self._lock:
            return self._incidents[-limit:]

    def force_remediate(self, deployment_id: str) -> bool:
        """Force a deployment healthy and reset its failure counter."""
        model_id = self._get_model_id(deployment_id)
        self._model_tracker.record_success(model_id)

        with self._lock:
            status = self._statuses.get(deployment_id)
            if status is None:
                return False
            status.healthy = True
            status.consecutive_failures = 0
            status.last_error = None
            self._add_incident(
                deployment_id,
                "remediated",
                "Forced remediation via admin endpoint",
            )
            return True

    # -- internal -----------------------------------------------------------

    def _add_incident(self, deployment_id: str, kind: str, detail: str) -> None:
        incident = DeploymentHealthIncident(
            deployment_id=deployment_id,
            timestamp=time.time(),
            kind=kind,
            detail=detail,
        )
        self._incidents.append(incident)
        while len(self._incidents) > self._max_incidents:
            self._incidents.pop(0)


# ---------------------------------------------------------------------------
# SelfHealingRouter
# ---------------------------------------------------------------------------


class SelfHealingRouter:
    """Routes model calls away from unhealthy deployments to fallback profiles.

    Wraps a :class:`ModelFailoverChain` for the default failover list and
    supports per-deployment override chains via ``set_failover_chain``.

    When a deployment is marked unhealthy the router logs structured
    diagnostics, attempts remediation (restart / scale request), and routes
    to the next healthy fallback.

    ``record_failure`` returns a :class:`DeploymentIncident` and updates the
    ``deployment_health`` dict.
    """

    def __init__(
        self,
        health_checker: DeploymentHealthChecker | None = None,
        failover_chain: ModelFailoverChain | None = None,
    ) -> None:
        """Initialise the router with a checker and optional default chain."""
        self._health_checker = health_checker or DeploymentHealthChecker()
        self._failover_chain = failover_chain
        self._per_deployment_chains: dict[str, ModelFailoverChain] = {}
        self._fallback_map: dict[str, list[str]] = {}
        self._deployment_health: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()

    # -- properties ---------------------------------------------------------

    @property
    def health_checker(self) -> DeploymentHealthChecker:
        """The configured DeploymentHealthChecker."""
        return self._health_checker

    @property
    def failover_chain(self) -> ModelFailoverChain | None:
        """The default ModelFailoverChain, when configured."""
        return self._failover_chain

    @property
    def deployment_health(self) -> dict[str, dict[str, object]]:
        """Snapshot of per-deployment health dicts."""
        with self._lock:
            return {did: dict(data) for did, data in self._deployment_health.items()}

    async def aclose(self) -> None:
        """Release resources owned by the underlying health checker."""
        await self._health_checker.aclose()

    # -- chain management ---------------------------------------------------

    def set_failover_chain(self, deployment_id: str, chain: ModelFailoverChain) -> None:
        """Register a failover chain for a deployment."""
        with self._lock:
            self._per_deployment_chains[deployment_id] = chain

    def set_fallbacks(self, deployment_id: str, fallback_ids: list[str]) -> None:
        """Legacy helper: build a chain from fallback profile ids.

        Create a :class:`ModelFailoverChain` from a list of string profiles
        and register it for *deployment_id*.
        """
        chain = ModelFailoverChain(
            primary_profile=deployment_id,
            fallback_profiles=fallback_ids,
        )
        self.set_failover_chain(deployment_id, chain)
        self._fallback_map[deployment_id] = list(fallback_ids)

    def _get_chain(self, deployment_id: str) -> ModelFailoverChain | None:
        with self._lock:
            return self._per_deployment_chains.get(deployment_id, self._failover_chain)

    # -- routing ------------------------------------------------------------

    def check_and_route(
        self,
        deployment_id: str,
        fallback_profiles: list[str] | None = None,
    ) -> str | None:
        """Route the call to the deployment or its first healthy fallback."""
        if self._health_checker.is_healthy(deployment_id):
            return deployment_id

        fallbacks: list[str] | None = fallback_profiles
        if fallbacks is None:
            chain = self._get_chain(deployment_id)
            if chain is not None:
                fallbacks = [p for p in chain.get_chain() if p != deployment_id]

        if fallbacks:
            for fb in fallbacks:
                if self._health_checker.is_healthy(fb):
                    logger.info(
                        "SelfHealingRouter: routing %s -> %s (fallback)",
                        deployment_id,
                        fb,
                    )
                    self._attempt_remediation(deployment_id)
                    return fb

        logger.warning(
            "SelfHealingRouter: no healthy fallback for %s (tried %d)",
            deployment_id,
            len(fallbacks or []),
        )
        return None

    # -- incident recording -------------------------------------------------

    def record_failure(
        self,
        deployment_id: str,
        error: str | Exception,
        model_id: str = "",
    ) -> DeploymentIncident:
        """Record a failure and return the corresponding incident."""
        self._health_checker.record_failure(deployment_id, error)

        error_str = str(error)[:500]
        with self._lock:
            health = self._deployment_health.setdefault(deployment_id, {})
            health["status"] = "unhealthy"
            health["last_check"] = time.time()
            health["error_count"] = cast(int, health.get("error_count", 0)) + 1

        return DeploymentIncident(
            timestamp=time.time(),
            deployment_id=deployment_id,
            model_id=model_id,
            error_type="failure",
            error_message=error_str,
        )

    # -- remediation --------------------------------------------------------

    def _attempt_remediation(self, deployment_id: str) -> None:
        logger.info(
            "SelfHealingRouter: attempting remediation for %s",
            deployment_id,
        )
        with self._lock:
            health = self._deployment_health.get(deployment_id, {})
            logger.info(
                "SelfHealingRouter: diagnostics — deployment=%s status=%s error_count=%s last_check=%s",
                deployment_id,
                health.get("status", "unknown"),
                health.get("error_count", 0),
                health.get("last_check", 0),
            )
        logger.info(
            "SelfHealingRouter: remediation — restart_request for %s",
            deployment_id,
        )
        logger.info(
            "SelfHealingRouter: remediation — scale_request for %s",
            deployment_id,
        )
