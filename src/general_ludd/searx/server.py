"""Lifecycle facade for native SearXNG and explicit remote compatibility."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.searx.config import SEARX_PORT_DEFAULT
from general_ludd.searx.native import NativeSearxRuntime, RemoteSearxAdapter, SearxError


class _SearxBackend(Protocol):
    @property
    def instance_uri(self) -> str: ...

    @property
    def process_pid(self) -> int | None: ...

    def start(self) -> bool: ...

    def stop(self) -> bool: ...

    def is_running(self) -> bool: ...

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


class SearXServer:
    """Own the primary in-process integration or an explicit remote adapter."""

    def __init__(
        self,
        port: int | None = None,
        settings_path: str | None = None,
        external_url: str | None = None,
        *,
        namespace: str | None = None,
        native_runtime: _SearxBackend | None = None,
        remote_adapter: _SearxBackend | None = None,
    ) -> None:
        """Select native mode unless an external URL is explicitly supplied."""
        self.port = port or int(os.environ.get("GLUDD_SEARX_PORT", SEARX_PORT_DEFAULT))
        self.settings_path = Path(settings_path).expanduser() if settings_path else None
        self.external_url = external_url.rstrip("/") if external_url else None
        self._backend: _SearxBackend
        if self.external_url is not None:
            self._backend = remote_adapter or RemoteSearxAdapter(base_url=self.external_url)
        else:
            self._backend = native_runtime or cast(
                _SearxBackend,
                NativeSearxRuntime(
                    settings_path=self.settings_path,
                    namespace=namespace,
                ),
            )

        # Kept solely to reap a process created by pre-native callers during a
        # rolling upgrade.  New local starts never assign or spawn this field.
        self._process: subprocess.Popen[bytes] | None = None
        self.last_error: SearxError | None = None

    @property
    def transport(self) -> str:
        """Return the selected integration transport."""
        return "remote" if self.external_url is not None else "native"

    @property
    def process_pid(self) -> int | None:
        """Return no PID for native mode and any backend-owned PID otherwise."""
        return self._backend.process_pid

    def start(self) -> bool:
        """Start the selected integration, returning health as a boolean."""
        try:
            self._backend.start()
        except SearxError as exc:
            self.last_error = exc
            return False
        self.last_error = None
        return self._backend.is_running()

    def stop(self) -> None:
        """Release native resources and reap any legacy rolling-upgrade child."""
        try:
            self._backend.stop()
        except SearxError as exc:
            self.last_error = exc

        process = self._process
        if process is None:
            return
        process.terminate()
        try:
            process.wait(5.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        self._process = None

    def is_running(self) -> bool:
        """Return backend health without creating a new resource."""
        try:
            return self._backend.is_running()
        except SearxError as exc:
            self.last_error = exc
            return False

    def get_instance_url(self) -> str:
        """Return a remote URL or the native non-network instance URI."""
        return self._backend.instance_uri

    def ensure_started(self) -> bool:
        """Idempotently ensure that the selected backend is healthy."""
        if self.is_running():
            return True
        return self.start()

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        """Search through the selected backend and propagate typed failures."""
        return self._backend.search(query, **kwargs)

    def _health_check(self) -> bool:
        """Compatibility alias for existing callers."""
        return self.is_running()

    def _detect_bound_port(self) -> int:
        """Compatibility accessor; native mode does not bind the port."""
        return self.port
