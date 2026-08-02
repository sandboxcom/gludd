"""Short-lived security tokens that carry a scoped :class:`PermissionSpec`.

STS = "security token service" — a mint/resolve/revoke registry for tokens that
bind an ``agent_type`` to a (possibly narrower-than-default) spec for a bounded
lifetime. The daemon consults this when an ``Authorization: Bearer <sts>``
header is presented on a request, reconstructing a SecretsManager scoped to the
token's spec for the duration of that request.

Design:
- Tokens are opaque random strings (``secrets.token_urlsafe``); the registry is
  the only source of truth for what a token means.
- An STS spec is always a NARROWING of a default spec — never a widening. The
  ``issue()`` factory accepts an explicit spec so the caller (the daemon's
  /admin/sts/issue endpoint, the worker's job-pickup path) is forced to
  enumerate exactly what the token can do.
- Tokens expire (default 1h). ``resolve()`` returns ``None`` for unknown /
  expired / revoked tokens so callers fail-closed.
"""

from __future__ import annotations

import secrets as _py_secrets
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, cast

from general_ludd.security.permissions import (
    Capability,
    PermissionDeniedError,
    PermissionSpec,
    PermissionSpecParser,
    union_denied,
)

DEFAULT_TTL_SECONDS: int = 3600


@dataclass(frozen=True)
class STSClaim:
    """The resolved view of a presented STS token.

    Attributes:
        token_id: The opaque token string the caller presented.
        agent_type: The agent type this token acts as (``build`` / ``primary``
            / ``subagent`` / a custom type).
        spec: The exact :class:`PermissionSpec` the token carries — this is
            what SecretsManager is reconstructed with.
        issued_at: Epoch seconds when the token was minted.
        expires_at: Epoch seconds after which ``resolve`` returns ``None``.
    """

    token_id: str
    agent_type: str
    spec: PermissionSpec
    issued_at: float
    expires_at: float


class STSRegistry:
    """In-process mint/resolve/revoke for STS tokens.

    A persistent/cluster-wide registry would back this with the DB; for the
    single-worker daemon an in-memory dict is sufficient and keeps the surface
    small. Concurrent access is guarded by the GIL — ``dict.pop`` and
    ``__setitem__`` are atomic in CPython.
    """

    def __init__(self, clock: Any = None) -> None:
        # ``clock`` is injectable for deterministic tests; defaults to wall time.
        # Typed as Any because ``time.time`` is a function, not a type.
        self._claims: dict[str, STSClaim] = {}
        self._clock = clock or time.time

    def issue(
        self,
        agent_type: str,
        spec: PermissionSpec,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> str:
        """Mint a token binding ``agent_type`` to ``spec`` for ``ttl_seconds``."""
        now = self._clock()
        token_id = _py_secrets.token_urlsafe(32)
        self._claims[token_id] = STSClaim(
            token_id=token_id,
            agent_type=agent_type,
            spec=spec,
            issued_at=now,
            expires_at=now + float(ttl_seconds),
        )
        return token_id

    def resolve(self, token_id: str) -> STSClaim | None:
        """Return the live :class:`STSClaim` for ``token_id`` or ``None``.

        ``None`` is returned for unknown tokens, expired tokens, and tokens
        that have been revoked. Expired tokens are also evicted lazily on
        resolution so the registry cannot leak dead entries.
        """
        claim = self._claims.get(token_id)
        if claim is None:
            return None
        if self._clock() >= claim.expires_at:
            # Lazy expiry: drop the dead token so it cannot be re-resolved.
            self._claims.pop(token_id, None)
            return None
        return claim

    def revoke(self, token_id: str) -> bool:
        """Forget ``token_id``. Returns ``True`` if a live claim was dropped."""
        return self._claims.pop(token_id, None) is not None

    def purge_expired(self) -> int:
        """Drop every expired claim. Returns the number purged."""
        now = self._clock()
        dead = [tid for tid, c in self._claims.items() if now >= c.expires_at]
        for tid in dead:
            self._claims.pop(tid, None)
        return len(dead)


@dataclass(frozen=True)
class StsToken:
    """A short-lived, scoped token minted by :class:`StsIssuer`."""

    token_id: str = field(repr=False)
    issuer_agent_id: str
    subject_agent_id: str
    spec: PermissionSpec
    issued_at: float
    expires_at: float
    last_used_at: float | None = None
    use_count: int = 0


class StsIssuer:
    """Mints :class:`StsToken` instances scoped to a subset of an issuer spec."""

    def __init__(self, clock: Any = time.time) -> None:
        self._clock = clock
        self._tokens: dict[str, StsToken] = {}

    def issue(
        self,
        issuer_spec: PermissionSpec,
        subject_spec_request: PermissionSpec,
        issuer_id: str,
        subject_id: str,
        ttl_seconds: int,
    ) -> StsToken:
        if not PermissionSpecParser.is_subset(subject_spec_request, issuer_spec):
            raise PermissionDeniedError(
                "subject spec requests capabilities not held by issuer"
            )
        # C-SEC-1b: TTL is monotonically NON-INCREASING down a delegation chain.
        # Clamp against BOTH the issuer's ceiling AND the subject spec's own
        # ceiling (which a prior intersection may already have lowered), so a
        # token minted from an already-narrowed spec can never outlive it.
        effective_max = min(
            issuer_spec.max_sts_ttl_seconds,
            subject_spec_request.max_sts_ttl_seconds,
        )
        capped_ttl = min(ttl_seconds, effective_max)
        # C-SEC-1: denials propagate through delegation. The child token carries
        # the UNION of the subject request's denials and the issuer's denials so
        # a carve-out (deny X) survives the hop and is enforced on the child. The
        # stored spec's own TTL ceiling is clamped so a further hop cannot raise
        # it back up.
        merged_denied = union_denied(
            subject_spec_request.denied, issuer_spec.denied
        )
        stored_spec = replace(
            subject_spec_request,
            denied=merged_denied,
            max_sts_ttl_seconds=effective_max,
        )
        now = self._clock()
        token = StsToken(
            token_id=uuid.uuid4().hex,
            issuer_agent_id=issuer_id,
            subject_agent_id=subject_id,
            spec=stored_spec,
            issued_at=now,
            expires_at=now + float(capped_ttl),
        )
        self._tokens[token.token_id] = token
        return token

    def validate(self, token: StsToken, required_capability: Capability) -> bool:
        if self._clock() >= token.expires_at:
            return False
        # C-SEC-1: a denial beats any positive grant. Check EVERY requested
        # action against the token spec's denied list FIRST — a denied action
        # fails validation even when a same-resource capability would allow it.
        for action in required_capability.actions:
            if token.spec.is_denied(required_capability.resource, action):
                return False
        cap = token.spec.capability_for(required_capability.resource)
        if cap is None:
            return False
        return set(required_capability.actions).issubset(set(cap.actions))

    def record_use(self, token_id: str) -> None:
        token = self._tokens.get(token_id)
        if token is None:
            return
        self._tokens[token_id] = replace(
            token,
            use_count=token.use_count + 1,
            last_used_at=self._clock(),
        )

    def get_token(self, token_id: str) -> StsToken | None:
        """Return the token if it exists AND has not expired, else ``None``.

        Fail-closed: an expired token resolves to ``None`` (matching
        :meth:`STSRegistry.resolve` semantics). The entry is retained so a
        subsequent :meth:`revoke` still reports the drop.
        """
        token = self._tokens.get(token_id)
        if token is None or self._clock() >= token.expires_at:
            return None
        return token

    def list_active(self) -> list[StsToken]:
        """Return all non-expired tokens (lazy-evicting expired ones)."""
        now = self._clock()
        live: list[StsToken] = []
        dead: list[str] = []
        for tid, tok in self._tokens.items():
            if now >= tok.expires_at:
                dead.append(tid)
            else:
                live.append(tok)
        for tid in dead:
            self._tokens.pop(tid, None)
        return live

    def revoke(self, token_id: str) -> bool:
        """Immediately revoke a token. Returns True if a live token was dropped."""
        return self._tokens.pop(token_id, None) is not None


class StsAuditLog:
    """In-memory audit log for STS issuance, use, and expiry events."""

    def __init__(self) -> None:
        self._events: list[dict[str, object]] = []
        self._token_agents: dict[str, tuple[str, str]] = {}

    def record_issue(self, token: StsToken) -> None:
        self._token_agents[token.token_id] = (
            token.issuer_agent_id,
            token.subject_agent_id,
        )
        self._events.append(
            {
                "event": "issued",
                "token_id": token.token_id,
                "issuer_agent_id": token.issuer_agent_id,
                "subject_agent_id": token.subject_agent_id,
                "capability": None,
                "target": None,
                "at": token.issued_at,
            }
        )

    def record_use(
        self, token_id: str, capability: Capability, target: str
    ) -> None:
        issuer_id, subject_id = self._token_agents.get(token_id, (None, None))
        self._events.append(
            {
                "event": "used",
                "token_id": token_id,
                "issuer_agent_id": issuer_id,
                "subject_agent_id": subject_id,
                "capability": capability.resource,
                "target": target,
                "at": time.time(),
            }
        )

    def record_expiry(self, token_id: str) -> None:
        issuer_id, subject_id = self._token_agents.get(token_id, (None, None))
        self._events.append(
            {
                "event": "expired",
                "token_id": token_id,
                "issuer_agent_id": issuer_id,
                "subject_agent_id": subject_id,
                "capability": None,
                "target": None,
                "at": time.time(),
            }
        )

    def query(
        self,
        agent_id: str | None = None,
        since: float | None = None,
        capability: str | None = None,
    ) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        for ev in self._events:
            if agent_id is not None and ev.get(
                "subject_agent_id"
            ) != agent_id and ev.get("issuer_agent_id") != agent_id:
                continue
            if since is not None and cast(float, ev.get("at", 0.0)) < since:
                continue
            if (
                capability is not None
                and ev.get("capability") != capability
            ):
                continue
            results.append(ev)
        return results


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "STSClaim",
    "STSRegistry",
    "StsAuditLog",
    "StsIssuer",
    "StsToken",
]
