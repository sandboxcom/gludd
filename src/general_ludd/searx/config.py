"""Generate a SearXNG settings.yml with gludd-safe defaults."""

from __future__ import annotations

import logging
import os
import secrets
import stat
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import cast

import yaml

logger = logging.getLogger(__name__)

DEFAULT_SEARX_SETTINGS = {
    "use_default_settings": True,
    "search": {
        "safe_search": 0,
        "autocomplete": "",
        "default_lang": "en",
        "formats": ["html", "json"],
    },
    "server": {
        "bind_address": "127.0.0.1",
        "port": 8888,
        "secret_key": "",
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

    def __init__(
        self,
        base_dir: str | None = None,
        *,
        namespace: str | None = None,
    ) -> None:
        """Initialize a secure, project-namespaced settings directory."""
        if base_dir is None:
            from general_ludd.searx.native import default_namespace

            selected_namespace = namespace or default_namespace()
            if not selected_namespace or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                for char in selected_namespace
            ):
                raise ValueError("namespace contains unsafe path characters")
            self.base_dir = Path("~/.gludd/searx").expanduser() / selected_namespace
        else:
            self.base_dir = Path(base_dir).expanduser()

    @staticmethod
    def _existing_secret(output_path: Path) -> str | None:
        if not output_path.exists():
            return None
        if output_path.is_symlink():
            raise OSError("refusing to replace a symlinked SearXNG settings file")
        mode = output_path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise OSError("SearXNG settings path is not a regular file")
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise PermissionError("SearXNG settings file must be owner-only writable")
        try:
            loaded = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return None
        if not isinstance(loaded, dict):
            return None
        server = loaded.get("server")
        if not isinstance(server, dict):
            return None
        secret = server.get("secret_key")
        if isinstance(secret, str) and len(secret) >= 32:
            return secret
        return None

    def generate(self) -> str:
        """Atomically write owner-only settings and return their path."""
        settings = deepcopy(DEFAULT_SEARX_SETTINGS)

        port = int(os.environ.get("GLUDD_SEARX_PORT", SEARX_PORT_DEFAULT))
        server_conf = cast("dict[str, object]", settings["server"])
        server_conf["port"] = port

        self.base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.base_dir.chmod(0o700)

        output_path = self.base_dir / "settings.yml"
        server_conf["secret_key"] = self._existing_secret(output_path) or secrets.token_urlsafe(48)
        rendered = yaml.safe_dump(settings, default_flow_style=False, sort_keys=True)
        if output_path.exists() and output_path.read_text(encoding="utf-8") == rendered:
            output_path.chmod(0o600)
            return str(output_path)

        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.base_dir,
                prefix=".settings-",
                suffix=".yml",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(rendered)
                temporary.flush()
                os.fsync(temporary.fileno())
            Path(temporary_name).chmod(0o600)
            os.replace(temporary_name, output_path)
            output_path.chmod(0o600)
        finally:
            if temporary_name:
                Path(temporary_name).unlink(missing_ok=True)

        logger.info("SearXNG settings written to %s", output_path)
        return str(output_path)
