"""Probe and initialize the official SearXNG source distribution."""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _expand_user(path: str) -> Path:
    return Path(path).expanduser().resolve()


def ensure_searx_installed() -> bool:
    """Return whether the official source distribution is importable.

    Upstream installs the distribution named ``searxng`` but its Python package
    is ``searx``.  Gludd deliberately does not run ``pip install searxng`` at
    runtime because the public index project with that name is not the official
    SearXNG server distribution.
    """
    try:
        spec = importlib.util.find_spec("searx.webapp")
    except (ImportError, ValueError, ModuleNotFoundError):
        spec = None

    if spec is not None:
        logger.info("official SearXNG Python package is available as searx.webapp")
        return True

    logger.warning(
        "official SearXNG source package is unavailable; install it from "
        "https://github.com/searxng/searxng and verify import searx.webapp"
    )
    return False


def ensure_searx_initialized(
    base_dir: str | None = None,
    *,
    namespace: str | None = None,
) -> bool:
    """Create a secure namespaced upstream settings file when absent."""
    from general_ludd.searx.config import SearXConfig

    config = SearXConfig(base_dir, namespace=namespace)
    resolved = config.base_dir.resolve()
    logger.info("Initializing SearXNG config at %s", resolved)
    try:
        config.generate()
    except Exception as exc:
        logger.warning("SearXConfig.generate failed: %s", exc)
        return False

    config_file = resolved / "settings.yml"
    if config_file.is_file():
        logger.info("SearXNG config exists at %s", config_file)
        return True

    logger.warning("SearXNG config not found after generate at %s", config_file)
    return False
