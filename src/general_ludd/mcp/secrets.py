"""MCP secret resolution: resolve env_aliases from Vault/OpenBao at runtime.

Ensures MCP server credentials are never stored as plaintext in YAML config.
Instead, YAML config uses `env_aliases` to reference credential names that
are resolved from the secrets manager (Vault/OpenBao) when the server starts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from general_ludd.mcp.config import MCPServerConfig

logger = logging.getLogger(__name__)

SENSITIVE_ENV_KEYS = frozenset({
    "API_KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_TOKEN",
    "AUTH_TOKEN",
    "CREDENTIAL",
})


def resolve_mcp_env(
    config: MCPServerConfig,
    secrets_mgr: Any,
) -> dict[str, str]:
    """Resolve env_aliases from secrets manager and merge with static env.

    Returns a dict of environment variables ready for the MCP subprocess.
    Secrets that cannot be resolved are silently skipped (logged as warning).
    """
    resolved = dict(config.env)

    for env_var, alias_name in config.env_aliases.items():
        value = secrets_mgr.resolve(alias_name)
        if value is not None:
            resolved[env_var] = value
            logger.debug(
                "Resolved MCP env alias %s -> %s for server %s",
                alias_name,
                env_var,
                config.server_id,
            )
        else:
            logger.warning(
                "Could not resolve MCP env alias %s for server %s",
                alias_name,
                config.server_id,
            )

    return resolved


def scrub_mcp_config(config_path: Path) -> list[str]:
    """Remove plaintext secrets from MCP YAML config env sections.

    Detects values in the `env:` dict that look like secrets (keys containing
    TOKEN, KEY, SECRET, PASSWORD, etc.) and removes them.

    Returns list of scrubbed env var names.
    """
    if not config_path.exists():
        return []

    with open(config_path) as f:
        lines = f.readlines()

    scrubbed: list[str] = []
    new_lines: list[str] = []
    in_env_block = False
    current_env_indent = 0

    for line in lines:
        stripped = line.lstrip()

        if stripped.startswith("env:"):
            in_env_block = True
            current_env_indent = len(line) - len(stripped)
            new_lines.append(line)
            continue

        if in_env_block:
            line_indent = len(line) - len(stripped)
            if line_indent <= current_env_indent and stripped and not stripped.startswith("#"):
                in_env_block = False
            else:
                for sensitive in SENSITIVE_ENV_KEYS:
                    if sensitive in stripped.split(":")[0].upper():
                        value_part = stripped.split(":", 1)
                        if len(value_part) > 1:
                            val = value_part[1].strip()
                            if val and val not in ("null", "None", "~", '""', "''"):
                                scrubbed.append(stripped.split(":")[0].strip())
                                line = ""
                                break

        if line:
            new_lines.append(line)

    if scrubbed:
        config_path.write_text("".join(new_lines))
        logger.info(
            "Scrubbed %d secret env vars from %s",
            len(scrubbed),
            config_path,
        )

    return scrubbed
