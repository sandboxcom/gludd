"""D-17: Automated daemon-worker PSK rotation with versioned identities.

Provides:

* :class:`PSKIdentity` — a versioned, time-bounded key with expiry.
* :class:`PSKStore` — abstract key store; :class:`InMemoryPSKStore` for testing.
* :class:`PSKRotator` — rotate, promote, accept, rollback, and revoke PSK
  versions with a configurable overlap window so live two-worker rotation
  has no lost event.
* :func:`create_psk_rotator` — factory that reads ``GLUDD_PSK_ROTATION_*``
  env vars.

The rotator accepts CURRENT and OVERLAPPING keys during the overlap window,
then rejects the old key everywhere. Rollback is forward-only: it reinstates
the prior immutable version rather than mutating the failed generation.
"""

from __future__ import annotations

import abc
import enum
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class PSKIdentity:
    """An immutable, versioned PSK with a finite lifetime."""

    version: int
    key: str
    created_at: float
    expires_at: float = 0.0

    def __post_init__(self) -> None:
        if self.expires_at == 0.0:
            object.__setattr__(self, "expires_at", self.created_at + 3600)
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")

    def is_expired(self, now: float | None = None) -> bool:
        _now = now if now is not None else time.time()
        return _now >= self.expires_at

    def __repr__(self) -> str:
        return f"PSKIdentity(version={self.version}, created_at={self.created_at}, expires_at={self.expires_at})"


class PSKStore(abc.ABC):
    """Durable store for versioned PSK identities."""

    @abc.abstractmethod
    def save(self, identity: PSKIdentity) -> None: ...

    @abc.abstractmethod
    def load(self, version: int) -> PSKIdentity | None: ...

    @abc.abstractmethod
    def list_versions(self) -> list[int]: ...

    @abc.abstractmethod
    def delete(self, version: int) -> None: ...


class InMemoryPSKStore(PSKStore):
    """In-memory PSK store for testing and single-process use."""

    def __init__(self) -> None:
        self._keys: dict[int, PSKIdentity] = {}
        self._lock = threading.Lock()

    def save(self, identity: PSKIdentity) -> None:
        with self._lock:
            self._keys[identity.version] = identity

    def load(self, version: int) -> PSKIdentity | None:
        with self._lock:
            return self._keys.get(version)

    def list_versions(self) -> list[int]:
        with self._lock:
            return sorted(self._keys.keys())

    def delete(self, version: int) -> None:
        with self._lock:
            self._keys.pop(version, None)


class PSKRotationState(enum.StrEnum):
    IDLE = "idle"
    ROTATING = "rotating"
    OVERLAP = "overlap"
    ACTIVE = "active"
    ROLLBACK = "rollback"
    REVOKED = "revoked"


@dataclass
class PSKRotationResult:
    success: bool
    state: PSKRotationState
    new_version: int = 0
    new_key: str = ""
    prior_version: int = 0
    overlap_start: float | None = None
    overlap_end: float | None = None
    error: str | None = None


class PSKRotator:
    """Manages versioned PSK rotation with overlap windows and rollback."""

    _KEY_BYTES: Final[int] = 32

    def __init__(
        self,
        store: PSKStore,
        overlap_seconds: int = 300,
        identity_ttl_seconds: int = 3600,
        key_bytes: int = 32,
    ) -> None:
        self._store = store
        self.overlap_seconds = overlap_seconds
        self.identity_ttl_seconds = identity_ttl_seconds
        self._key_bytes = key_bytes
        self._lock = threading.RLock()
        self._active_version: int = 0

    def _store_max_version(self) -> int:
        versions = self._store.list_versions()
        return max(versions) if versions else 0

    def rotate(self) -> PSKRotationResult:
        with self._lock:
            store_versions = self._store.list_versions()
            new_version = 1 if not store_versions else max(store_versions) + 1
            prior_version = self._store_max_version()

            new_key = secrets.token_hex(self._key_bytes)
            now = time.time()
            identity = PSKIdentity(
                version=new_version,
                key=new_key,
                created_at=now,
                expires_at=now + self.identity_ttl_seconds,
            )
            self._store.save(identity)
            self._active_version = new_version

            overlap_start = now
            overlap_end = now + self.overlap_seconds

            return PSKRotationResult(
                success=True,
                state=PSKRotationState.ACTIVE,
                new_version=new_version,
                new_key=new_key,
                prior_version=prior_version,
                overlap_start=overlap_start if prior_version > 0 else None,
                overlap_end=overlap_end if prior_version > 0 else None,
            )

    def _prior_version(self) -> int:
        """Return the version immediately below the current active version."""
        versions = self._store.list_versions()
        current = self.current_version()
        below = [v for v in versions if v < current]
        return max(below) if below else 0

    def rollback(self) -> PSKRotationResult:
        with self._lock:
            prior = self._prior_version()
            if prior == 0:
                return PSKRotationResult(
                    success=False,
                    state=PSKRotationState.ROLLBACK,
                    error="no prior version to roll back to",
                )

            identity = self._store.load(prior)
            if identity is None or identity.is_expired():
                return PSKRotationResult(
                    success=False,
                    state=PSKRotationState.ROLLBACK,
                    error=f"prior version {prior} not found or expired",
                )

            current_active = self._active_version
            self._active_version = prior

            return PSKRotationResult(
                success=True,
                state=PSKRotationState.ROLLBACK,
                new_version=prior,
                new_key=identity.key,
                prior_version=current_active,
            )

    def accept_key(self, presented: str) -> bool:
        if not presented:
            return False

        with self._lock:
            active_version = self.current_version()
            active = self._store.load(active_version)
            if active is not None and hmac.compare_digest(presented, active.key):
                return True

            if self.overlap_seconds > 0:
                prior = self._prior_version()
                if prior > 0:
                    prior_identity = self._store.load(prior)
                    if prior_identity is not None and not prior_identity.is_expired():
                        now = time.time()
                        active_identity = self._store.load(active_version)
                        if active_identity is not None:
                            age = now - active_identity.created_at
                            if age <= self.overlap_seconds and hmac.compare_digest(presented, prior_identity.key):
                                return True

            return False

    def current_version(self) -> int:
        with self._lock:
            if self._active_version > 0:
                return self._active_version
            return self._store_max_version()

    def active_key(self) -> str:
        with self._lock:
            cv = self.current_version()
            if cv == 0:
                return ""
            identity = self._store.load(cv)
            if identity is None:
                return ""
            return identity.key

    def revoke_version(self, version: int) -> None:
        with self._lock:
            cv = self.current_version()
            if version == cv:
                raise ValueError(f"cannot revoke the active version ({version}); rotate first")
            self._store.delete(version)


def create_psk_rotator(store: PSKStore | None = None) -> PSKRotator:
    """Factory that reads ``GLUDD_PSK_ROTATION_*`` env vars for configuration."""
    overlap = int(os.environ.get("GLUDD_PSK_ROTATION_OVERLAP_SECONDS", "300"))
    ttl = int(os.environ.get("GLUDD_PSK_IDENTITY_TTL_SECONDS", "3600"))

    if overlap < 0:
        overlap = 0
    if ttl < 1:
        ttl = 3600

    return PSKRotator(
        store=store if store is not None else InMemoryPSKStore(),
        overlap_seconds=overlap,
        identity_ttl_seconds=ttl,
    )
