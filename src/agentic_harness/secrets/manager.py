"""Secrets management with OpenBao and hvac."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SecretAlias:
    def __init__(self, alias: str, path: str, mount: str = "secret") -> None:
        self.alias = alias
        self.path = path
        self.mount = mount


class SecretsManager:
    def __init__(
        self,
        client: Any = None,
        aliases: dict[str, SecretAlias] | None = None,
    ) -> None:
        self._client = client
        self._aliases = aliases or {}

    def register_alias(self, alias: SecretAlias) -> None:
        self._aliases[alias.alias] = alias

    def resolve(self, alias_name: str) -> str | None:
        alias = self._aliases.get(alias_name)
        if alias is None:
            return None
        if self._client is None:
            logger.warning("No secrets client configured for alias %s", alias_name)
            return None
        try:
            result = self._client.secrets.kv.v2.read_secret_version(
                path=alias.path, mount_point=alias.mount
            )
            if result and "data" in result and "data" in result["data"]:
                return str(result["data"]["data"].get("value", ""))
        except Exception as exc:
            logger.error("Failed to resolve secret alias %s: %s", alias_name, exc)
        return None

    def list_aliases(self) -> list[str]:
        return list(self._aliases.keys())
