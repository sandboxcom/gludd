"""Fail-closed admission for the production sandbox dispatch boundary.

This module deliberately owns no policy rules.  It binds an already-resolved,
immutable profile to the existing runtime-attestation evaluator and durable
store, then returns only sealed allow decisions to a dispatch caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn, Protocol

from general_ludd.security.policy.profiles import ResolvedSandboxProfile
from general_ludd.security.sandboxes.attestation import (
    AttestationIntegrityError,
    NamespaceEvidence,
    RuntimeSandboxObservation,
    SandboxAttestationEvent,
    evaluate_runtime_attestation,
)


class SandboxAttestationAppender(Protocol):
    """Small structural seam shared by the database store and focused tests."""

    async def append(self, event: SandboxAttestationEvent) -> SandboxAttestationEvent: ...


@dataclass(frozen=True)
class SandboxDispatchIdentity:
    """Bounded identities copied from one work item before backend admission."""

    project_id: str
    work_item_id: str
    agent_id: str
    tenant_id: str
    correlation_id: str


@dataclass(frozen=True)
class SandboxDispatchLease:
    """An allowed backend handle pinned to the policy event that admitted it."""

    backend: object
    handle: object
    attestation: SandboxAttestationEvent


class SandboxDispatchDenied(RuntimeError):
    """Raised only after a durable denial event has been committed."""

    def __init__(self, event: SandboxAttestationEvent) -> None:
        self.event = event
        missing = ", ".join(event.missing_guarantees) or "unspecified"
        super().__init__(
            f"Sandbox dispatch denied ({event.reason_code}); missing guarantees: {missing}"
        )


def unavailable_observation(
    resolved: ResolvedSandboxProfile,
    *,
    backend: str | None = None,
) -> RuntimeSandboxObservation:
    """Return conservative typed evidence when no trustworthy probe exists.

    The effective backend is retained when it is one of the profile's approved
    candidates.  Unknown or unsupported backends are represented by the first
    requested candidate and ``applied=False``; no guarantee is inferred from a
    binary name, handle, or backend success flag.
    """

    preferences = resolved.policy.backend.preference
    effective = backend if backend in preferences else preferences[0]
    return RuntimeSandboxObservation(
        applied=False,
        backend=effective,
        backend_version="unavailable",
        guarantees=frozenset(),
        namespaces=NamespaceEvidence(),
        filesystem_mounts=(),
        uid=0,
        gid=0,
    )


class DurableSandboxDispatchGuard:
    """Persist a policy decision and expose only integrity-checked allows."""

    def __init__(
        self,
        *,
        resolved: ResolvedSandboxProfile,
        store: SandboxAttestationAppender,
    ) -> None:
        self._resolved = resolved
        self._store = store

    @property
    def resolved(self) -> ResolvedSandboxProfile:
        """The immutable policy version pinned for this dispatch attempt."""

        return self._resolved

    async def attest(
        self,
        identity: SandboxDispatchIdentity,
        observation: RuntimeSandboxObservation,
    ) -> SandboxAttestationEvent:
        """Commit allow/deny evidence before returning or raising."""

        draft = evaluate_runtime_attestation(
            resolved=self._resolved,
            observation=observation,
            project_id=identity.project_id,
            work_item_id=identity.work_item_id,
            agent_id=identity.agent_id,
            tenant_id=identity.tenant_id,
            correlation_id=identity.correlation_id,
        )
        sealed = await self._store.append(draft)
        if sealed.sequence <= 0 or not sealed.verify_integrity():
            raise AttestationIntegrityError(
                "sandbox dispatch store returned an unsealed attestation"
            )
        if sealed.decision == "deny":
            raise SandboxDispatchDenied(sealed)
        return sealed

    async def deny_unavailable(
        self,
        identity: SandboxDispatchIdentity,
        *,
        backend: str | None = None,
    ) -> NoReturn:
        """Durably deny when selection, application, or observation is absent."""

        await self.attest(
            identity,
            unavailable_observation(self._resolved, backend=backend),
        )
        raise AttestationIntegrityError("unavailable sandbox was unexpectedly admitted")


__all__ = [
    "DurableSandboxDispatchGuard",
    "SandboxAttestationAppender",
    "SandboxDispatchDenied",
    "SandboxDispatchIdentity",
    "SandboxDispatchLease",
    "unavailable_observation",
]
