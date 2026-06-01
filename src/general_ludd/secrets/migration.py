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
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def migrate_profile_secrets(
    mgr: Any,
    profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    aliases_to_migrate: list[tuple[str, str, str]] = []
    skipped: list[str] = []

    for profile in profiles:
        profile_id = str(profile.get("model_profile_id", "unknown"))
        for alias_field in ("credential_alias", "api_base_alias"):
            raw_alias = profile.get(alias_field)
            if not raw_alias or not isinstance(raw_alias, str):
                continue
            alias_name: str = raw_alias
            value = os.environ.get(alias_name)
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
            from general_ludd.secrets.manager import SecretAlias

            mgr.register_alias(SecretAlias(alias_name, vault_path))
            migrated_count += 1
            migrated_aliases.append(alias_name)
            logger.info("Migrated secret %s to vault path %s", alias_name, vault_path)
        except Exception as exc:
            logger.warning("Failed to migrate %s: %s", alias_name, exc)
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
