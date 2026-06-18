"""Secrets loader for the dogfood E2E harness.

Reads .secrets/llm_keys.env from the repo root.
NEVER prints or logs secret values.

Returns a dict with normalized keys for the gludd gateway, or None when the
file is absent or ZAI_API_KEY is not found (caller skips gracefully).
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Parse a simple KEY=VALUE dotenv file. Ignores blank lines and #comments."""
    result: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip surrounding quotes if present (both single and double)
        if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
            value = value[1:-1]
        result[key] = value
    return result


def load_llm_keys(repo_root: Path | str | None = None) -> dict[str, str] | None:
    """Load LLM API keys from .secrets/llm_keys.env.

    Resolution order for ZAI_API_KEY:
      1. .secrets/llm_keys.env (ZAI_API_KEY)
      2. os.environ ZAI_API_KEY

    Resolution order for base URL:
      1. .secrets/llm_keys.env ZAI_API_BASE or ZAI_BASE_URL
      2. os.environ ZAI_API_BASE or ZAI_BASE_URL
      3. Hard-coded default

    Returns None if no key can be resolved (caller should skip the live test).
    NEVER logs or prints the key value.
    """
    if repo_root is None:
        # Walk up from this file to the repo root (contains pyproject.toml)
        candidate = Path(__file__).resolve()
        for _ in range(10):
            candidate = candidate.parent
            if (candidate / "pyproject.toml").exists():
                repo_root = candidate
                break
        else:
            repo_root = Path.cwd()
    repo_root = Path(repo_root)

    env: dict[str, str] = {}
    secrets_file = repo_root / ".secrets" / "llm_keys.env"
    if secrets_file.exists():
        try:
            env = _parse_dotenv(secrets_file)
        except Exception:
            env = {}

    # Resolve the API key
    key = env.get("ZAI_API_KEY") or os.environ.get("ZAI_API_KEY") or ""
    if not key:
        return None  # no key → caller skips

    # Resolve the base URL (accept both alias names)
    base = (
        env.get("ZAI_API_BASE")
        or env.get("ZAI_BASE_URL")
        or os.environ.get("ZAI_API_BASE")
        or os.environ.get("ZAI_BASE_URL")
        or "https://open.bigmodel.cn/api/paas/v4"
    )

    model = env.get("ZAI_MODEL") or os.environ.get("ZAI_MODEL") or "glm-5.1"

    return {
        "zai_api_key": key,
        "zai_api_base": base,
        "model": model,
    }
