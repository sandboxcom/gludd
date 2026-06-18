"""Load .secrets/llm_keys.env safely. NEVER prints key values."""
from __future__ import annotations

import os
from pathlib import Path


def _parse_dotenv(path: Path) -> dict[str, str]:
    """Minimal dotenv parser: KEY=VALUE lines, ignores comments and blank lines."""
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip('"').strip("'")
    return result


def load_llm_keys(repo_root: Path | None = None) -> dict[str, str] | None:
    """Load LLM keys from .secrets/llm_keys.env.

    Returns a dict with keys: zai_api_key, zai_api_base, model.
    Returns None if the file is absent or ZAI_API_KEY is not set.
    Never logs or prints key values.
    """
    if repo_root is None:
        repo_root = Path(__file__).parent.parent.parent.parent
    env_file = repo_root / ".secrets" / "llm_keys.env"
    env: dict[str, str] = {}
    if env_file.exists():
        env = _parse_dotenv(env_file)

    # ZAI_API_KEY: prefer file, fall back to os.environ
    key = env.get("ZAI_API_KEY") or os.environ.get("ZAI_API_KEY")
    if not key:
        return None

    # ZAI_BASE_URL / ZAI_API_BASE: accept both names
    base = (
        env.get("ZAI_API_BASE")
        or env.get("ZAI_BASE_URL")
        or os.environ.get("ZAI_API_BASE")
        or os.environ.get("ZAI_BASE_URL")
        or "https://open.bigmodel.cn/api/paas/v4"
    )
    model = env.get("ZAI_MODEL") or os.environ.get("ZAI_MODEL") or "glm-5.1"
    return {"zai_api_key": key, "zai_api_base": base, "model": model}
