from __future__ import annotations

import importlib.util
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _expand_user(path: str) -> Path:
    return Path(path).expanduser().resolve()


def ensure_searx_installed() -> bool:
    try:
        spec = importlib.util.find_spec("searxng")
    except (ImportError, ValueError, ModuleNotFoundError):
        spec = None

    if spec is not None:
        logger.info("searxng already available")
        return True

    logger.info("searxng not found — attempting uv pip install")
    try:
        result = subprocess.run(
            ["uv", "pip", "install", "searxng"],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Failed to run uv pip install searxng: %s", exc)
        return False

    if result.returncode != 0:
        logger.warning(
            "uv pip install searxng exited %d: %s",
            result.returncode,
            result.stderr.strip() or result.stdout.strip(),
        )
        return False

    try:
        spec = importlib.util.find_spec("searxng")
    except (ImportError, ValueError, ModuleNotFoundError):
        spec = None

    if spec is not None:
        logger.info("searxng installed and importable")
        return True

    logger.warning("searxng installed but still not importable")
    return False


def ensure_searx_initialized(base_dir: str = "~/.gludd/searx") -> bool:
    from general_ludd.searx.config import SearXConfig

    resolved = _expand_user(base_dir)
    logger.info("Initializing SearXNG config at %s", resolved)
    try:
        SearXConfig(str(resolved)).generate()
    except Exception as exc:
        logger.warning("SearXConfig.generate failed: %s", exc)
        return False

    config_file = resolved / "settings.yml"
    if config_file.is_file():
        logger.info("SearXNG config exists at %s", config_file)
        return True

    logger.warning("SearXNG config not found after generate at %s", config_file)
    return False
