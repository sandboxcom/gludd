"""Environment-variable-backed secrets manager for non-OpenBao deployments."""

from __future__ import annotations

import os


class EnvSecretsManager:
    """Resolve secrets from environment variables or an explicit overrides dict.

    Lookup order:
    1. ``_overrides[alias]``
    2. ``os.environ[alias]``
    3. ``None``
    """

    def __init__(self, overrides: dict[str, str] | None = None) -> None:
        self._overrides: dict[str, str] = overrides or {}

    def set(self, alias: str, value: str) -> None:
        self._overrides[alias] = value

    def resolve(self, alias_name: str) -> str | None:
        if alias_name in self._overrides:
            return self._overrides[alias_name]
        return os.environ.get(alias_name)

    def list_aliases(self) -> list[str]:
        keys = set(self._overrides.keys())
        return sorted(keys)
