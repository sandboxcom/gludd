"""STS token lifecycle contracts — immutable data shapes for the token pipeline.

These are the API-boundary dataclasses consumed and produced by TokenMinter,
TokenStore, TokenRevoker, and the dispatch/validation stack. Every dataclass
is frozen (immutable) and carries a ``__post_init__`` that validates invariants
at construction time, so a badly-formed contract cannot propagate downstream.

Contracts
---------
- ``TokenRequest`` — caller-provided request to mint an STS token
- ``TokenGrant``  — output of a successful mint (secret_id masked in repr)
- ``TokenValidation`` — the result of checking a token's lifecycle state
- ``TokenRevocation`` — a revocation event record
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

_TERMINAL_STATES = frozenset({"cancelled", "cascade", "completed", "expired", "failed", "rotated", "timed_out"})


@dataclass(frozen=True)
class TokenRequest:
    """Immutable request to mint an STS token for a subagent.

    Attributes:
        agent_id: The child agent to create a token for (non-empty).
        parent_agent_id: The parent whose capability lattice constrains the child.
        project_id: Optional project scope for quota enforcement.
        scope_actions: Optional frozen sequence of requested action names.
        parent_role: The role within the parent's CapabilityLattice to
            evaluate against (default ``"admin"``).
        ttl_seconds: Token time-to-live in seconds (min 1).
        request_id: Optional idempotency key.
    """

    agent_id: str
    parent_agent_id: str
    project_id: str | None = None
    scope_actions: tuple[str, ...] | None = None
    parent_role: str = "admin"
    ttl_seconds: int = 3600
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        if not self.parent_agent_id.strip():
            raise ValueError("parent_agent_id must be a non-empty string")
        if self.ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")


@dataclass(frozen=True)
class TokenGrant:
    """Immutable result of a successful token mint.

    ``secret_id`` is present on the instance but its ``repr`` is masked so
    it never leaks into logs or the audit trail via ``str(grant)``.

    Attributes:
        token_id: Unique token identifier (convention: ``"tok-{agent_id}"``).
        agent_id: The agent this token was created for.
        parent_agent_id: The parent who authorized the token.
        role_id: OpenBao AppRole ``role_id``.
        secret_id: OpenBao AppRole ``secret_id`` (masked in repr).
        role_name: OpenBao AppRole name (``"agent-{agent_id}"``).
        scope_hash: Hash of the capability scope granted.
        scope_actions: Frozen sequence of actions granted.
        created_at: When the token was created (UTC-aware).
        expires_at: When the token's TTL elapses (UTC-aware).
        project_id: Optional project scope.
        hydration_count: Times the token has been revived from hibernation.
    """

    token_id: str
    agent_id: str
    parent_agent_id: str
    role_id: str
    secret_id: str = field(repr=False)
    role_name: str
    scope_hash: str
    scope_actions: tuple[str, ...]
    created_at: datetime
    expires_at: datetime
    project_id: str | None = None
    hydration_count: int = 0

    def __repr__(self) -> str:
        safe = {
            "token_id": self.token_id,
            "agent_id": self.agent_id,
            "parent_agent_id": self.parent_agent_id,
            "role_id": self.role_id,
            "secret_id": "***",
            "role_name": self.role_name,
            "scope_hash": self.scope_hash,
            "scope_actions": self.scope_actions,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "project_id": self.project_id,
            "hydration_count": self.hydration_count,
        }
        return f"TokenGrant({safe!r})"

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if now is None:
            now = datetime.now(UTC)
        return self.expires_at <= now

    def remaining_seconds(self, *, now: datetime | None = None) -> int:
        if now is None:
            now = datetime.now(UTC)
        delta = (self.expires_at - now).total_seconds()
        return max(0, int(delta))


@dataclass(frozen=True)
class TokenValidation:
    """Immutable result of checking a token's lifecycle state.

    Built by callers (e.g. ``TokenValidator``) or via the ``from_grant``
    factory that evaluates a ``TokenGrant`` against an expected scope hash.

    Attributes:
        token_id: The token being validated.
        valid: ``True`` iff the token is active AND scope hash matches.
        status: Lifecycle state (``"active"``, ``"expired"``, ``"revoked"``,
            ``"unknown"``).
        scope_hash_match: Whether the token's scope hash matches the expected value.
        remaining_seconds: If valid and active, seconds until expiry.
        reason: Human-readable explanation when ``valid`` is ``False``.
    """

    token_id: str
    valid: bool
    status: str
    scope_hash_match: bool
    remaining_seconds: int | None = None
    reason: str | None = None

    @classmethod
    def from_grant(
        cls,
        grant: TokenGrant | None,
        *,
        expected_scope_hash: str,
        now: datetime | None = None,
    ) -> TokenValidation:
        if grant is None:
            return cls(
                token_id="unknown",
                valid=False,
                status="unknown",
                scope_hash_match=False,
                reason="Token not found",
            )
        if now is None:
            now = datetime.now(UTC)

        scope_match = grant.scope_hash == expected_scope_hash

        if grant.is_expired(now=now):
            return cls(
                token_id=grant.token_id,
                valid=False,
                status="expired",
                scope_hash_match=scope_match,
                remaining_seconds=0,
                reason="TTL exceeded",
            )

        remaining = grant.remaining_seconds(now=now)

        if not scope_match:
            return cls(
                token_id=grant.token_id,
                valid=False,
                status="active",
                scope_hash_match=False,
                remaining_seconds=remaining,
                reason=f"Scope hash mismatch (expected {expected_scope_hash})",
            )

        return cls(
            token_id=grant.token_id,
            valid=True,
            status="active",
            scope_hash_match=True,
            remaining_seconds=remaining,
        )


@dataclass(frozen=True)
class TokenRevocation:
    """Immutable revocation event record.

    Represents the outcome of a token revocation — either terminal
    (agent death/completion) or cascade (parent removal tears down
    the delegation subtree).

    Attributes:
        token_id: The token that was revoked.
        agent_id: The agent whose token was revoked.
        parent_agent_id: The parent who authorized the token.
        revoked_at: When revocation occurred (UTC-aware).
        terminal_state: The reason for revocation; must be one of
            ``_TERMINAL_STATES``.
        cascade: Whether this revocation triggered child revocations.
        reason: Optional human-readable reason.
    """

    token_id: str
    agent_id: str
    parent_agent_id: str
    revoked_at: datetime
    terminal_state: str
    cascade: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.terminal_state not in _TERMINAL_STATES:
            raise ValueError(f"terminal_state must be one of {sorted(_TERMINAL_STATES)}, got {self.terminal_state!r}")


__all__ = [
    "_TERMINAL_STATES",
    "TokenGrant",
    "TokenRequest",
    "TokenRevocation",
    "TokenValidation",
]
