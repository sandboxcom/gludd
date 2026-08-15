"""Environment-variable-backed secrets manager for non-OpenBao deployments."""

from __future__ import annotations

import os
import re

# S-1 (fail-closed allowlist): EnvSecretsManager previously returned
# os.environ.get(alias) for ANY name, so a caller (or a hot-reloaded model
# profile whose credential_alias is attacker-controlled) could exfiltrate
# arbitrary process env vars — the daemon's pre-shared key, cloud provider
# keys, AWS_*, etc.
# Resolution from the ambient environment is now restricted to:
#   1. names the caller explicitly registered (via overrides / set())
#   2. names matching a recognized secret/credential naming convention
#   3. names in an explicit extra allow-set passed by the caller
# Anything else (e.g. the daemon pre-shared key, PATH, HOME) resolves to None.
#
# The suffix/prefix patterns below cover every legitimate api-key / api-base
# alias used by the model gateway (e.g. OPENAI_API_KEY, ZAI_API_KEY,
# ZAI_BASE_URL, *_API_BASE) and the infra routers (slurm_api_url,
# slurm_auth_token), while NOT matching the pre-shared key var.
_ALLOWLIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"_API_KEY$", re.IGNORECASE),
    re.compile(r"_API_BASE$", re.IGNORECASE),
    re.compile(r"_BASE_URL$", re.IGNORECASE),
    re.compile(r"_API_URL$", re.IGNORECASE),
    re.compile(r"_AUTH_TOKEN$", re.IGNORECASE),
    re.compile(r"^GLUDD_SECRET_", re.IGNORECASE),
)

# Explicit env-var name aliases: lowercase gateway alias → uppercase env var name.
# Handles cases where the gateway's canonical alias differs from the user-exported
# var name (e.g. gateway uses "zai_api_base" but user exports "ZAI_BASE_URL").
_ENV_VAR_ALIASES: dict[str, str] = {
    "zai_api_base": "ZAI_BASE_URL",
}


class EnvSecretsManager:
    """Resolve secrets from environment variables or an explicit overrides dict.

    Lookup order:
    1. ``_overrides[alias]`` (always allowed — explicitly registered)
    2. ``os.environ[alias]`` **only if** ``alias`` is allowlisted
       (recognized credential naming convention, or in ``allow``)
    3. ``None``
    """

    def __init__(
        self,
        overrides: dict[str, str] | None = None,
        allow: set[str] | None = None,
    ) -> None:
        """Initialize with explicit overrides and an extra env allow-set."""
        self._overrides: dict[str, str] = overrides or {}
        # Explicit extra allow-set of env-var names a caller vouches for.
        self._allow: set[str] = set(allow or set())

    def set(self, alias: str, value: str) -> None:
        """Register an explicit override value for one alias."""
        self._overrides[alias] = value

    def allow_env(self, *names: str) -> None:
        """Register ambient env-var names that resolve() may read from os.environ."""
        self._allow.update(names)

    def _is_allowlisted(self, alias_name: str) -> bool:
        if alias_name in self._allow:
            return True
        return any(p.search(alias_name) for p in _ALLOWLIST_PATTERNS)

    def resolve(self, alias_name: str, project_id: str | None = None) -> str | None:
        """Resolve one alias from overrides, then allowlisted env vars, else None."""
        # Explicitly-registered overrides are always honored.
        if alias_name in self._overrides:
            return self._overrides[alias_name]
        # Ambient env is only consulted for allowlisted names. This is the
        # S-1 fix: the pre-shared key / arbitrary process env vars no longer leak.
        if self._is_allowlisted(alias_name):
            val = os.environ.get(alias_name)
            if val is None:
                # Fallback 1: user may have exported the uppercase form (e.g.
                # ZAI_API_KEY) while the gateway uses the lowercase alias (e.g.
                # zai_api_key). Only try if the upper name also passes the
                # allowlist — never let uppercase bypass the security gate.
                upper = alias_name.upper()
                if upper != alias_name and self._is_allowlisted(upper):
                    val = os.environ.get(upper)
            if val is None and alias_name in _ENV_VAR_ALIASES:
                # Fallback 2: explicit alias mapping (e.g. zai_api_base →
                # ZAI_BASE_URL). Allowlist-checked before reading.
                env_var = _ENV_VAR_ALIASES[alias_name]
                if self._is_allowlisted(env_var):
                    val = os.environ.get(env_var)
            return val
        return None

    def list_aliases(self) -> list[str]:
        """Return the sorted list of explicitly registered override aliases."""
        keys = set(self._overrides.keys())
        return sorted(keys)
