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
from dataclasses import dataclass

from general_ludd.security.permissions import PermissionSpec

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

    def __init__(self, clock: time.time | None = None) -> None:
        # ``clock`` is injectable for deterministic tests; defaults to wall time.
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


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "STSClaim",
    "STSRegistry",
]
