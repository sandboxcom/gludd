"""Lease-scoped Azure credentials from an operator-configured OpenBao role."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from threading import RLock
from typing import Protocol, runtime_checkable

from general_ludd.azure.accelerator_credentials import (
    AzureAcceleratorCredentials,
    _canonical_uuid,
    build_azure_accelerator_credentials,
)
from general_ludd.secrets.openbao_scope import validate_openbao_mount

_ROLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_LEASE_SUFFIX = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}")


@runtime_checkable
class _LeaseSystem(Protocol):
    """Structural boundary for exact OpenBao lease revocation."""

    def revoke_lease(self, lease_id: str) -> object: ...


@runtime_checkable
class _OpenBaoClient(Protocol):
    """Structural boundary for the public OpenBao client operations used here."""

    sys: _LeaseSystem

    def read(self, path: str) -> object: ...


class AzureOpenBaoCredentialLeaseError(RuntimeError):
    """Fixed-context OpenBao issuance/revocation failure without secret data."""

    def __init__(self, operation: str) -> None:
        """Initialize the censored failure for one bounded operation."""
        super().__init__(f"OpenBao Azure credential lease failed: {operation}")
        self.operation = operation


class AzureCredentialLeaseState(StrEnum):
    """Content-free state emitted during an exact credential lease lifecycle."""

    REQUESTED = "requested"
    ACQUIRED = "acquired"
    REVOKE_STARTED = "revoke_started"
    REVOKED = "revoked"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AzureCredentialLeaseEvent:
    """Safe event carrying lifecycle state and bounded duration only."""

    state: AzureCredentialLeaseState
    lease_duration_seconds: int = 0
    source: str = "openbao-azure"


@dataclass(frozen=True, slots=True)
class AzureAcceleratorCredentialLease:
    """One dynamic service-principal credential and its hidden exact lease ID."""

    credentials: AzureAcceleratorCredentials
    lease_duration_seconds: int
    renewable: bool
    _lease_id: str = field(repr=False)


def _discard_trace(_event: AzureCredentialLeaseEvent) -> None:
    return None


class OpenBaoAzureCredentialLeaseSource:
    """Issue and exactly revoke one preconfigured OpenBao Azure role lease."""

    def __init__(
        self,
        *,
        client: _OpenBaoClient,
        subscription_id: str,
        tenant_id: str,
        mount: str = "azure",
        role_name: str = "gludd-accelerator",
        max_lease_seconds: int = 900,
        trace_sink: Callable[[AzureCredentialLeaseEvent], None] = _discard_trace,
    ) -> None:
        """Initialize an exact OpenBao role and bounded lease policy."""
        if not callable(getattr(client, "read", None)) or not callable(
            getattr(getattr(client, "sys", None), "revoke_lease", None)
        ):
            raise ValueError("client must expose OpenBao read and exact lease revocation")
        validated_mount = validate_openbao_mount(mount)
        if _ROLE_NAME.fullmatch(role_name) is None:
            raise ValueError("OpenBao Azure role name is not a safe identifier")
        if (
            isinstance(max_lease_seconds, bool)
            or not isinstance(max_lease_seconds, int)
            or not 1 <= max_lease_seconds <= 86_400
        ):
            raise ValueError("max_lease_seconds must be in 1..86400")
        if not callable(trace_sink):
            raise ValueError("trace_sink must be callable")
        self._client = client
        self._subscription_id = _canonical_uuid(subscription_id)
        self._tenant_id = _canonical_uuid(tenant_id)
        self._mount = validated_mount
        self._role_name = role_name
        self._max_lease_seconds = max_lease_seconds
        self._trace_sink = trace_sink
        self._active: dict[int, AzureAcceleratorCredentialLease] = {}
        self._releasing: set[int] = set()
        self._lock = RLock()

    @property
    def active_lease_count(self) -> int:
        """Return a secret-free count of exact leases still owned by this source."""
        with self._lock:
            return len(self._active)

    def _emit(
        self,
        state: AzureCredentialLeaseState,
        lease_duration_seconds: int = 0,
    ) -> None:
        try:
            self._trace_sink(
                AzureCredentialLeaseEvent(
                    state=state,
                    lease_duration_seconds=lease_duration_seconds,
                )
            )
        except Exception:
            raise AzureOpenBaoCredentialLeaseError("trace") from None

    def _lease_id(self, response: object) -> str | None:
        if not isinstance(response, Mapping):
            return None
        value = response.get("lease_id")
        if not isinstance(value, str):
            return None
        prefix = f"{self._mount}/creds/{self._role_name}/"
        if not value.startswith(prefix):
            return None
        suffix = value.removeprefix(prefix)
        if _LEASE_SUFFIX.fullmatch(suffix) is None:
            return None
        return value

    def _parse(
        self,
        response: object,
    ) -> tuple[str, int, bool, AzureAcceleratorCredentials]:
        if not isinstance(response, Mapping):
            raise ValueError
        lease_id = self._lease_id(response)
        duration = response.get("lease_duration")
        renewable = response.get("renewable")
        data = response.get("data")
        if (
            lease_id is None
            or isinstance(duration, bool)
            or not isinstance(duration, int)
            or not 1 <= duration <= self._max_lease_seconds
            or not isinstance(renewable, bool)
            or not isinstance(data, Mapping)
            or set(data) != {"client_id", "client_secret"}
        ):
            raise ValueError
        client_id = data.get("client_id")
        client_secret = data.get("client_secret")
        if not isinstance(client_id, str) or not isinstance(client_secret, str):
            raise ValueError
        credentials = build_azure_accelerator_credentials(
            client_id=client_id,
            client_secret=client_secret,
            subscription_id=self._subscription_id,
            tenant_id=self._tenant_id,
            expected_subscription_id=self._subscription_id,
        )
        return lease_id, duration, renewable, credentials

    def _revoke_without_widening(self, lease_id: str) -> None:
        self._client.sys.revoke_lease(lease_id)

    def acquire(self) -> AzureAcceleratorCredentialLease:
        """Issue one bounded lease using public ``hvac.Client.read`` semantics."""
        self._emit(AzureCredentialLeaseState.REQUESTED)
        response: object = None
        lease_id: str | None = None
        try:
            response = self._client.read(
                f"{self._mount}/creds/{self._role_name}"
            )
            lease_id = self._lease_id(response)
            lease_id, duration, renewable, credentials = self._parse(response)
            lease = AzureAcceleratorCredentialLease(
                credentials=credentials,
                lease_duration_seconds=duration,
                renewable=renewable,
                _lease_id=lease_id,
            )
            with self._lock:
                self._active[id(lease)] = lease
            try:
                self._emit(AzureCredentialLeaseState.ACQUIRED, duration)
            except AzureOpenBaoCredentialLeaseError:
                with self._lock:
                    self._active.pop(id(lease), None)
                with suppress(Exception):
                    self._revoke_without_widening(lease_id)
                raise
            return lease
        except AzureOpenBaoCredentialLeaseError:
            raise
        except Exception:
            if lease_id is not None:
                with suppress(Exception):
                    self._revoke_without_widening(lease_id)
            with suppress(AzureOpenBaoCredentialLeaseError):
                self._emit(AzureCredentialLeaseState.FAILED)
            raise AzureOpenBaoCredentialLeaseError("acquire") from None

    def release(self, lease: AzureAcceleratorCredentialLease) -> None:
        """Revoke only an exact lease previously returned by this source."""
        if not isinstance(lease, AzureAcceleratorCredentialLease):
            raise ValueError("lease must be AzureAcceleratorCredentialLease")
        lease_key = id(lease)
        with self._lock:
            owned = self._active.get(lease_key)
            if owned is not lease or lease_key in self._releasing:
                return
            self._releasing.add(lease_key)
        trace_failure: AzureOpenBaoCredentialLeaseError | None = None
        try:
            try:
                self._emit(
                    AzureCredentialLeaseState.REVOKE_STARTED,
                    lease.lease_duration_seconds,
                )
            except AzureOpenBaoCredentialLeaseError as exc:
                trace_failure = exc
            try:
                self._revoke_without_widening(lease._lease_id)
            except Exception:
                with suppress(AzureOpenBaoCredentialLeaseError):
                    self._emit(AzureCredentialLeaseState.FAILED)
                raise AzureOpenBaoCredentialLeaseError("revoke") from None
            with self._lock:
                self._active.pop(lease_key, None)
            try:
                self._emit(
                    AzureCredentialLeaseState.REVOKED,
                    lease.lease_duration_seconds,
                )
            except AzureOpenBaoCredentialLeaseError as exc:
                trace_failure = trace_failure or exc
            if trace_failure is not None:
                raise trace_failure
        finally:
            with self._lock:
                self._releasing.discard(lease_key)


__all__ = (
    "AzureAcceleratorCredentialLease",
    "AzureCredentialLeaseEvent",
    "AzureCredentialLeaseState",
    "AzureOpenBaoCredentialLeaseError",
    "OpenBaoAzureCredentialLeaseSource",
)
