"""Generate a SearXNG settings.yml with gludd-safe defaults."""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from pathlib import Path
from typing import cast

import yaml

logger = logging.getLogger(__name__)

DEFAULT_SEARX_SETTINGS = {
    "use_default_settings": False,
    "search": {
        "safe_search": 0,
        "autocomplete": "",
        "default_lang": "en",
        "formats": ["html", "json"],
    },
    "server": {
        "bind_address": "127.0.0.1:8888",
        "limiter": False,
        "image_proxy": False,
        "public_instance": False,
    },
    "ui": {
        "static_use_hash": True,
        "default_theme": "simple",
        "default_locale": "en",
    },
    "redis": {"url": ""},
    "outgoing": {
        "request_timeout": 10.0,
        "max_request_timeout": 15.0,
        "useragent_suffix": "gludd-searxng/1.0",
        "pool_connections": 100,
        "pool_maxsize": 20,
        "enable_http2": True,
    },
}


SEARX_PORT_DEFAULT = "8888"


class SearXConfig:
    """Writer for the local SearXNG settings file."""

    def __init__(self, base_dir: str = "~/.gludd/searx") -> None:
        """Initialize with the base directory for the settings file."""
        self.base_dir = Path(base_dir).expanduser()

    def generate(self) -> str:
        """Write settings.yml (with the configured port) and return its path."""
        settings = deepcopy(DEFAULT_SEARX_SETTINGS)

        port = os.environ.get("GLUDD_SEARX_PORT", SEARX_PORT_DEFAULT)
        server_conf = cast("dict[str, object]", settings["server"])
        server_conf["bind_address"] = f"127.0.0.1:{port}"

        self.base_dir.mkdir(parents=True, exist_ok=True)

        output_path = self.base_dir / "settings.yml"
        with open(output_path, "w") as f:
            yaml.safe_dump(settings, f, default_flow_style=False)

        logger.info("SearXNG settings written to %s", output_path)
        return str(output_path)
