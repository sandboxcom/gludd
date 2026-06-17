"""Core abstractions for compute resource discovery.

This module deliberately imports ONLY stdlib + already-core symbols (no cloud
SDK) so ``import general_ludd.infra.discovery`` and pytest collection succeed
with ZERO optional ``[compute]`` dependencies installed.

Key pieces:

* :class:`DiscoverySource` — a NEW StrEnum identifying a *discovery* backend.
  This is intentionally separate from :class:`general_ludd.infra.compute.ComputeProvider`
  (the *deployable* cloud provider enum) so discovery stays orthogonal to the
  terraform/deploy path.
* :class:`DiscoveredResource` — a normalized, frozen description of one
  schedulable compute resource.
* :class:`DiscoveryResult` / :class:`DiscoveryStatus` — a STRUCTURED result that
  a provider ALWAYS returns; ``discover()`` never raises or hangs.
* :class:`WorkSpec` — the work item's resource need + per-pick budget cap.
* :class:`DiscoveryProvider` — the discovery ABC.
* :func:`auth_env` — a reusable transient-credential context manager mirroring
  ``DeploymentManager._inject_auth_env`` / ``_restore_auth_env``.
* :func:`guarded_call` — wraps a blocking callable in
  ``asyncio.wait_for(asyncio.to_thread(...))`` with a TimeoutRetryPolicy + a
  circuit breaker and ALWAYS returns a structured result.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, ClassVar, TypeVar

from general_ludd.connectors.base import is_safe_endpoint
from general_ludd.infra.compute import GPUType
from general_ludd.infra.deployment import SecretsResolver

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Serializes os.environ mutation so two concurrent discover() calls cannot
# clobber each other's restore window. The to_thread SDK call runs INSIDE the
# locked+injected window, so env is restored even when wait_for times out.
_ENV_LOCK = threading.Lock()


class DiscoverySource(StrEnum):
    """Identity of a discovery backend (NOT a deployable provider)."""

    LOCAL = "local"
    KUBERNETES = "kubernetes"
    VMWARE = "vmware"
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"


class DiscoveryStatus(StrEnum):
    """Outcome of a single ``discover()`` call. Never an exception."""

    OK = "ok"
    PARTIAL = "partial"
    OFFLINE = "offline"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    AUTH_FAILED = "auth_failed"
    ERROR = "error"


class ProviderNotInstalled(RuntimeError):
    """Raised inside a provider when its optional SDK is absent.

    Caught by the provider and surfaced as ``PROVIDER_UNAVAILABLE`` — it never
    escapes ``discover()``.
    """


_INSTALL_HINT = "pip install general-ludd-agent[compute]"


@dataclass(frozen=True)
class DiscoveredResource:
    """A normalized, schedulable compute resource from some discovery source."""

    source: DiscoverySource
    id: str
    region: str | None = None
    gpu_type: GPUType | None = None
    gpu_count: int = 0
    vcpu: int = 0
    mem_gb: float = 0.0
    cost_per_hour: float | None = None  # None => unknown (fail-closed under a cap)
    available: bool = True
    endpoint_url: str | None = None  # already-serving URL, else None (needs deploy)
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DiscoveryResult:
    """Structured result a provider ALWAYS returns (never raises/hangs)."""

    source: DiscoverySource
    status: DiscoveryStatus
    resources: tuple[DiscoveredResource, ...] = ()
    error: str | None = None  # human string, NEVER a secret value
    from_cache: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (DiscoveryStatus.OK, DiscoveryStatus.PARTIAL)


@dataclass(frozen=True)
class WorkSpec:
    """The work item's resource need + per-pick budget cap."""

    model: str = ""
    task_type: Any | None = None  # TaskType for weights_for; None -> defaults
    needs_gpu: bool = False
    gpu_type: GPUType | None = None
    gpu_count: int = 0
    vcpu: int = 0
    mem_gb: float = 0.0
    max_cost_usd: float | None = None  # per-pick budget cap
    project_id: str | None = None


# --------------------------------------------------------------------------- #
# Transient-credential context manager (mirrors DeploymentManager)
# --------------------------------------------------------------------------- #
@contextmanager
def auth_env(
    aliases: Mapping[str, str] | None,
    resolver: SecretsResolver | None,
) -> Iterator[None]:
    """Inject resolved creds into ``os.environ`` for the duration of the block.

    Mirrors ``DeploymentManager._inject_auth_env`` / ``_restore_auth_env`` and
    ALWAYS restores in ``finally``. Fail-closed: an alias that cannot resolve
    raises ``RuntimeError`` (caught by ``discover()`` -> ``AUTH_FAILED``) so the
    SDK is NEVER called unauthenticated. The mutation is serialized under a
    module-level lock so concurrent providers cannot clobber each other's
    restore. NEVER logs a resolved value — only env-var/alias NAMES.
    """
    original: dict[str, str | None] = {}
    if not aliases:
        # Nothing to inject; still take the lock-free fast path.
        yield
        return
    with _ENV_LOCK:
        try:
            for env_var, alias in aliases.items():
                original[env_var] = os.environ.get(env_var)
                value: str | None = None
                if resolver is not None:
                    # SecretsManager.resolve may raise on a backend OUTAGE; let
                    # that propagate so an OpenBao outage ALSO fails closed.
                    value = resolver.resolve(alias)
                if value is not None:
                    os.environ[env_var] = value
                elif alias in os.environ:
                    os.environ[env_var] = os.environ[alias]
                else:
                    raise RuntimeError(
                        f"Could not resolve auth alias {alias!r} for env var "
                        f"{env_var!r} (set it in OpenBao or as an env var)."
                    )
            yield
        finally:
            for env_var, prior in original.items():
                if prior is None:
                    os.environ.pop(env_var, None)
                else:
                    os.environ[env_var] = prior


# --------------------------------------------------------------------------- #
# Guarded network call: timeout + retry + circuit breaker -> structured result
# --------------------------------------------------------------------------- #
async def guarded_call(
    fn: Callable[[], T],
    *,
    breaker: Any | None = None,
    timeout_s: float = 10.0,
    max_retries: int = 2,
) -> T:
    """Run a BLOCKING ``fn`` off the event loop with timeout + bounded retry.

    Returns ``fn``'s value on success. Raises on exhausted retries / timeout /
    open breaker — the CALLER (a provider ``discover()``) is responsible for
    catching and converting to a structured ``DiscoveryResult``.

    * ``asyncio.to_thread`` keeps a blocking SDK off the loop; ``wait_for``
      bounds it so a hung SDK cannot stall the daemon.
    * Retries are driven by ``TimeoutRetryPolicy`` over the classified error;
      AUTH/non-retryable errors short-circuit immediately.
    * A per-provider circuit breaker (when supplied) is consulted before the
      call and records the outcome.
    """
    from general_ludd.models.timeout_detector import (
        TimeoutClassifier,
        TimeoutKind,
        TimeoutRetryPolicy,
    )

    if breaker is not None and not breaker.allow():
        raise TimeoutError("circuit breaker open")

    policy = TimeoutRetryPolicy(max_retries=max_retries)
    attempt = 0
    last_exc: BaseException | None = None
    while True:
        attempt += 1
        try:
            result = await asyncio.wait_for(asyncio.to_thread(fn), timeout_s)
            if breaker is not None:
                breaker.record_success()
            return result
        except (TimeoutError, asyncio.TimeoutError) as exc:  # noqa: UP041
            last_exc = exc
            kind = TimeoutKind.READ_TIMEOUT
        except Exception as exc:
            last_exc = exc
            kind = TimeoutClassifier.classify(exc)
        if breaker is not None:
            breaker.record_failure()
        decision = policy.decide(kind, attempt)
        if not decision.should_retry:
            assert last_exc is not None
            raise last_exc
        await asyncio.sleep(min(decision.wait_seconds, timeout_s))


class DiscoveryProvider(ABC):
    """Discovery ABC. Each provider knows how to enumerate its resources.

    ``discover()`` MUST be total: it catches its own SDK/network/auth errors and
    returns a :class:`DiscoveryResult`, NEVER raising.
    """

    source: ClassVar[DiscoverySource]
    required_aliases: ClassVar[tuple[str, ...]] = ()

    def __init__(
        self,
        *,
        provider_auth_aliases: dict[str, str] | None = None,
        secrets_resolver: SecretsResolver | None = None,
        region: str | None = None,
        api_base: str | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._aliases = provider_auth_aliases or {}
        self._resolver = secrets_resolver
        self._region = region
        self._api_base = api_base
        self._timeout_s = timeout_s

    @abstractmethod
    async def discover(self) -> DiscoveryResult:
        """Enumerate resources. Total — returns a result, never raises."""

    # ----- ABC-provided helpers -------------------------------------------- #
    @staticmethod
    def guard_endpoint(url: str | None) -> bool:
        """SSRF guard for an endpoint URL. False -> drop the candidate."""
        if not url:
            return False
        try:
            return is_safe_endpoint(url)
        except Exception:
            return False

    def _auth(self) -> Any:
        return auth_env(self._aliases, self._resolver)

    def _unavailable(self, msg: str) -> DiscoveryResult:
        return DiscoveryResult(
            source=self.source,
            status=DiscoveryStatus.PROVIDER_UNAVAILABLE,
            error=msg,
        )

    def _auth_failed(self, exc: BaseException) -> DiscoveryResult:
        # NEVER include a resolved secret — exc here is an alias-resolution
        # RuntimeError whose message carries NAMES only (by construction).
        return DiscoveryResult(
            source=self.source,
            status=DiscoveryStatus.AUTH_FAILED,
            error=str(exc),
        )

    def _offline(self, exc: BaseException) -> DiscoveryResult:
        return DiscoveryResult(
            source=self.source,
            status=DiscoveryStatus.OFFLINE,
            error=type(exc).__name__,
        )

    def _error(self, exc: BaseException) -> DiscoveryResult:
        return DiscoveryResult(
            source=self.source,
            status=DiscoveryStatus.ERROR,
            error=type(exc).__name__,
        )


__all__ = [
    "_INSTALL_HINT",
    "DiscoveredResource",
    "DiscoveryProvider",
    "DiscoveryResult",
    "DiscoverySource",
    "DiscoveryStatus",
    "ProviderNotInstalled",
    "WorkSpec",
    "auth_env",
    "guarded_call",
]
