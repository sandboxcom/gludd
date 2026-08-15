"""Secret migration: move secrets from config files and env vars into OpenBao/Vault.

The migration process:
1. Scan model profiles for credential_alias and api_base_alias fields
2. Resolve each alias from environment variables
3. Write the resolved value into OpenBao KV v2
4. Register a SecretAlias so future reads come from Vault
5. Scrub inline secrets from YAML config files
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from general_ludd.secrets.env import EnvSecretsManager
from general_ludd.secrets.manager import SecretAlias

if TYPE_CHECKING:
    from general_ludd.secrets.manager import SecretsManager

logger = logging.getLogger(__name__)


def migrate_profile_secrets(
    mgr: SecretsManager,
    profiles: list[dict[str, object]],
) -> dict[str, object]:
    """Resolve profile credential aliases and write them into the vault backend."""
    aliases_to_migrate: list[tuple[str, str, str]] = []
    skipped: list[str] = []

    # S-1: resolve alias values through the fail-closed EnvSecretsManager
    # allowlist rather than reading os.environ directly. An attacker-controlled
    # credential_alias like GLUDD_AUTH_PSK / PATH / AWS_SECRET_ACCESS_KEY therefore
    # resolves to None (and is skipped) instead of being copied into the vault;
    # legitimate *_API_KEY / *_API_BASE aliases still migrate.
    env_resolver = EnvSecretsManager()

    for profile in profiles:
        profile_id = str(profile.get("model_profile_id", "unknown"))
        for alias_field in ("credential_alias", "api_base_alias"):
            raw_alias = profile.get(alias_field)
            if not raw_alias or not isinstance(raw_alias, str):
                continue
            alias_name: str = raw_alias
            value = env_resolver.resolve(alias_name)
            if value is None:
                skipped.append(alias_name)
                logger.debug("Skipping %s: not found in environment", alias_name)
                continue
            vault_path = f"model-profiles/{profile_id}/{alias_field}"
            aliases_to_migrate.append((alias_name, vault_path, value))

    migrated_count = 0
    migrated_aliases: list[str] = []

    for alias_name, vault_path, value in aliases_to_migrate:
        try:
            mgr.write_secret(vault_path, {"value": value})
            mgr.register_alias(SecretAlias(alias_name, vault_path))
            migrated_count += 1
            migrated_aliases.append(alias_name)
            logger.info("Migrated secret %s to vault path %s", alias_name, vault_path)
        except Exception as exc:
            # Log only the exception TYPE, never str(exc): a vault/backend error
            # message can embed the secret value being written (e.g. "value
            # 'sk-...' rejected"), which would leak it into logs.
            logger.warning("Failed to migrate %s: %s", alias_name, type(exc).__name__)
            skipped.append(alias_name)

    return {
        "migrated": migrated_count,
        "aliases": migrated_aliases,
        "skipped": skipped,
    }


def scrub_inline_secrets(
    config_path: Path,
    secret_fields: list[str] | None = None,
) -> list[str]:
    """Remove inline secret values from a YAML config file; return scrubbed field names."""
    if secret_fields is None:
        secret_fields = [
            "api_key",
            "secret_key",
            "access_token",
            "external_token",
            "password",
            "private_key",
        ]

    if not config_path.exists():
        return []

    with open(config_path) as f:
        lines = f.readlines()

    scrubbed_fields: list[str] = []
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        removed = False
        for field in secret_fields:
            prefix = f"{field}:"
            if stripped.startswith(prefix) or stripped.startswith(f"# {prefix}"):
                value_part = stripped.split(":", 1)[1].strip()
                if (
                    value_part
                    and not value_part.startswith("$")
                    and value_part not in ("null", "None", "~", "true", "false", "")
                ):
                    scrubbed_fields.append(field)
                    removed = True
                    break
        if not removed:
            new_lines.append(line)

    if scrubbed_fields:
        config_path.write_text("".join(new_lines))
        logger.info(
            "Scrubbed %d secret fields from %s: %s",
            len(scrubbed_fields),
            config_path,
            scrubbed_fields,
        )

    return scrubbed_fields
