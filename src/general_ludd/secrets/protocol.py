"""Structural Protocol for secrets manager implementations.

Both :class:`~general_ludd.secrets.manager.SecretsManager` and
:class:`~general_ludd.secrets.env.EnvSecretsManager` satisfy this protocol
at runtime without any inheritance — typing only, zero behavior change.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SecretsManagerProtocol(Protocol):
    """Minimal interface shared by all secrets manager implementations.

    Any object that exposes ``resolve`` and ``list_aliases`` with the
    signatures below satisfies this protocol via structural subtyping.
    Use ``SecretsManagerProtocol`` as the type annotation at call sites
    instead of ``Any`` to get static and (with ``@runtime_checkable``)
    dynamic conformance checks.
    """

    def resolve(self, alias_name: str) -> str | None:
        """Return the secret value for *alias_name*, or ``None`` if absent.

        Raise :class:`~general_ludd.secrets.manager.SecretsUnavailableError`
        when the backend is unreachable / auth-failed (fail-closed).
        """
        ...

    def list_aliases(self) -> list[str]:
        """Return all currently registered alias names."""
        ...
