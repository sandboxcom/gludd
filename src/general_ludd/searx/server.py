"""Lifecycle management for a local SearXNG subprocess (or external instance)."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

import httpx

from general_ludd.searx.config import SEARX_PORT_DEFAULT

logger = logging.getLogger(__name__)

_SEARXNG_COMMANDS = ("searxng-run", ["python", "-m", "searxng.runner"])


class SearXServer:
    """Manage a local SearXNG subprocess or delegate to an external instance."""

    def __init__(
        self,
        port: int | None = None,
        settings_path: str | None = None,
        external_url: str | None = None,
    ) -> None:
        """Initialize with port, settings path, and optional external URL."""
        self.port = port or int(os.environ.get("GLUDD_SEARX_PORT", SEARX_PORT_DEFAULT))
        self.settings_path = Path(settings_path or "~/.gludd/searx/settings.yml").expanduser()
        self.external_url = external_url
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> bool:
        """Start the local subprocess and wait (bounded) until it is healthy."""
        if self.external_url is not None:
            return True
        if self._process is not None:
            try:
                return self.is_running()
            except Exception:
                self._process = None

        env = os.environ.copy()
        env["SEARXNG_SETTINGS_PATH"] = str(self.settings_path)

        for cmd in _SEARXNG_COMMANDS:
            executable = cmd if isinstance(cmd, list) else [cmd]
            try:
                self._process = subprocess.Popen(
                    executable,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                break
            except FileNotFoundError:
                continue

        if self._process is None:
            logger.error("SearXNG executable not found — tried %s", _SEARXNG_COMMANDS)
            return False

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                logger.error("SearXNG process exited prematurely (code %s)", self._process.returncode)
                self._process = None
                return False
            if self._health_check():
                self.port = self._detect_bound_port()
                return True
            time.sleep(0.2)

        if self._process.poll() is None:
            self.stop()
        logger.error("SearXNG did not become healthy within 10s")
        return False

    def stop(self) -> None:
        """Terminate the subprocess (escalating to kill after a 5s grace)."""
        if self._process is None:
            return
        self._process.terminate()
        try:
            self._process.wait(5.0)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None

    def is_running(self) -> bool:
        """Return True when the instance answers its health check."""
        if self.external_url is not None:
            return self._health_check()
        if self._process is None:
            return False
        if self._process.poll() is not None:
            self._process = None
            return False
        return self._health_check()

    def get_instance_url(self) -> str:
        """Return the external or local base URL for this instance."""
        if self.external_url:
            return self.external_url
        return f"http://127.0.0.1:{self.port}"

    def ensure_started(self) -> bool:
        """Start the instance if it is not already healthy."""
        if self.is_running():
            return True
        return self.start()

    def _health_check(self) -> bool:
        try:
            url = f"http://127.0.0.1:{self.port}/"
            resp = httpx.get(url, timeout=2.0, follow_redirects=False)
            return 200 <= resp.status_code < 300
        except Exception:
            return False

    def _detect_bound_port(self) -> int:
        return self.port
