"""Native, in-process integration with the official SearXNG Python package.

The local path imports the upstream ``searx.webapp`` WSGI application and
invokes its documented ``/search`` API through the application's own WSGI test
client.  No loopback socket, subprocess, or raw URL is involved.  Remote HTTP
is kept as a separate, explicitly selected compatibility adapter.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import stat
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from general_ludd.searx.errors import (
    SearxError,
    SearxLifecycleError,
    SearxSearchError,
    SearxUnavailableError,
)
from general_ludd.searx.remote import RemoteSearxAdapter

_NAMESPACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_IMPORT_LOCK = threading.RLock()
_MAX_QUERY_CHARS = 2048
_MAX_RESULTS = 100


class _WsgiResponse(Protocol):
    status_code: int

    def get_json(self, *, silent: bool = False) -> object: ...


class _WsgiClient(Protocol):
    def get(self, path: str, **kwargs: Any) -> _WsgiResponse: ...

    def close(self) -> None: ...


class _WsgiApp(Protocol):
    def test_client(self) -> _WsgiClient: ...


ModuleLoader = Callable[[str], object]


def default_namespace() -> str:
    """Return a stable project-scoped resource namespace."""
    configured = os.environ.get("GLUDD_RESOURCE_NAMESPACE", "").strip()
    if configured:
        return configured
    project = str(Path.cwd().resolve()).encode("utf-8")
    digest = hashlib.sha256(project).hexdigest()[:12]
    return f"gludd-searx-{digest}"


def validate_namespace(namespace: str) -> str:
    """Validate and return one bounded resource namespace."""
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise ValueError(
            "namespace must be 1-64 characters using only letters, digits, '.', '_', or '-'"
        )
    return namespace


def _close_client(client: _WsgiClient) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


class NativeSearxRuntime:
    """Own one direct WSGI binding to the upstream SearXNG application."""

    def __init__(
        self,
        *,
        settings_path: str | Path | None = None,
        namespace: str | None = None,
        module_loader: ModuleLoader = importlib.import_module,
    ) -> None:
        """Configure a lazy direct binding to the upstream WSGI application."""
        self.namespace = validate_namespace(
            default_namespace() if namespace is None else namespace
        )
        self._configured_settings_path = Path(settings_path).expanduser() if settings_path else None
        self._module_loader = module_loader
        self._app: _WsgiApp | None = None
        self._client: _WsgiClient | None = None
        self._lock = threading.RLock()
        self.last_error: SearxError | None = None

    @property
    def instance_uri(self) -> str:
        """Return a non-network identity for this in-process instance."""
        return f"searx+python://{self.namespace}"

    @property
    def process_pid(self) -> None:
        """Native operation owns no child process."""
        return None

    @property
    def settings_path(self) -> Path:
        """Return the configured path, generating a namespaced config lazily."""
        if self._configured_settings_path is None:
            from general_ludd.searx.config import SearXConfig

            generated = SearXConfig(namespace=self.namespace).generate()
            self._configured_settings_path = Path(generated)
        return self._configured_settings_path

    def _validated_settings_path(self) -> Path:
        path = self.settings_path
        if path.is_symlink():
            raise SearxLifecycleError("SearXNG settings path must not be a symlink")
        try:
            info = path.stat()
        except OSError as exc:
            raise SearxLifecycleError("SearXNG settings file is unavailable") from exc
        if not stat.S_ISREG(info.st_mode):
            raise SearxLifecycleError("SearXNG settings path must be a regular file")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise SearxLifecycleError("SearXNG settings file must be owner-only writable")
        return path.resolve()

    def _load_app(self) -> _WsgiApp:
        settings = self._validated_settings_path()
        with _IMPORT_LOCK:
            previous = os.environ.get("SEARXNG_SETTINGS_PATH")
            os.environ["SEARXNG_SETTINGS_PATH"] = str(settings)
            try:
                module = self._module_loader("searx.webapp")
            except (ImportError, ModuleNotFoundError) as exc:
                raise SearxUnavailableError(
                    "official SearXNG source package is unavailable (expected import searx.webapp)"
                ) from exc
            except Exception as exc:
                raise SearxLifecycleError("official SearXNG application import failed") from exc
            finally:
                if previous is None:
                    os.environ.pop("SEARXNG_SETTINGS_PATH", None)
                else:
                    os.environ["SEARXNG_SETTINGS_PATH"] = previous

        app = getattr(module, "app", None)
        if app is None or not callable(getattr(app, "test_client", None)):
            raise SearxUnavailableError("searx.webapp does not expose the expected WSGI app")
        return cast(_WsgiApp, app)

    @staticmethod
    def _healthy(client: _WsgiClient) -> bool:
        try:
            response = client.get("/healthz")
        except Exception:
            return False
        return int(response.status_code) == 200

    def start(self) -> bool:
        """Start the native binding; return whether state changed."""
        with self._lock:
            if self._client is not None:
                return False
            app = self._load_app()
            try:
                client = app.test_client()
            except Exception as exc:
                raise SearxLifecycleError("could not create native SearXNG WSGI client") from exc
            if not self._healthy(client):
                _close_client(client)
                raise SearxLifecycleError("native SearXNG health check failed during startup")
            self._app = app
            self._client = client
            self.last_error = None
            return True

    def restart(self) -> bool:
        """Replace the WSGI client only after its health check succeeds."""
        with self._lock:
            if self._client is None:
                return self.start()
            app = self._app
            if app is None:
                raise SearxLifecycleError("native SearXNG runtime has no application binding")
            try:
                replacement = app.test_client()
            except Exception as exc:
                raise SearxLifecycleError("could not create replacement SearXNG WSGI client") from exc
            if not self._healthy(replacement):
                _close_client(replacement)
                raise SearxLifecycleError("replacement SearXNG health check failed")
            current = self._client
            self._client = replacement
            _close_client(current)
            self.last_error = None
            return True

    def stop(self) -> bool:
        """Release the owned WSGI client; return whether state changed."""
        with self._lock:
            client = self._client
            if client is None:
                return False
            self._client = None
            self._app = None
            try:
                _close_client(client)
            except Exception as exc:
                error = SearxLifecycleError("native SearXNG client cleanup failed")
                self.last_error = error
                raise error from exc
            return True

    def is_running(self) -> bool:
        """Return whether the runtime has a live, healthy WSGI binding."""
        with self._lock:
            return self._client is not None and self._healthy(self._client)

    @staticmethod
    def _validate_query(query: str, max_results: int) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if "\x00" in query or len(query) > _MAX_QUERY_CHARS:
            raise ValueError(f"query must be at most {_MAX_QUERY_CHARS} characters without NUL bytes")
        if isinstance(max_results, bool) or not 1 <= max_results <= _MAX_RESULTS:
            raise ValueError(f"max_results must be between 1 and {_MAX_RESULTS}")
        return query

    def search(
        self,
        query: str,
        *,
        categories: Sequence[str] | None = None,
        engines: Sequence[str] | None = None,
        language: str = "en",
        safe_search: int = 0,
        page: int = 1,
        time_range: str | None = None,
        max_results: int = 10,
    ) -> dict[str, Any]:
        """Execute the supported JSON API directly through the WSGI app."""
        query = self._validate_query(query, max_results)
        if safe_search not in {0, 1, 2}:
            raise ValueError("safe_search must be 0, 1, or 2")
        if isinstance(page, bool) or page < 1:
            raise ValueError("page must be a positive integer")
        if time_range not in {None, "day", "week", "month", "year"}:
            raise ValueError("time_range must be day, week, month, or year")
        client = self._client
        if client is None:
            raise SearxLifecycleError("native SearXNG runtime is not started")

        params: dict[str, str | int] = {
            "q": query,
            "format": "json",
            "pageno": page,
            "language": language,
            "safesearch": safe_search,
        }
        if engines:
            params["engines"] = ",".join(engines)
        else:
            params["categories"] = ",".join(categories or ("general",))
        if time_range:
            params["time_range"] = time_range

        try:
            response = client.get(
                "/search",
                query_string=params,
                headers={"Accept": "application/json"},
            )
        except Exception as exc:
            error = SearxSearchError("native SearXNG search call failed")
            self.last_error = error
            raise error from exc
        if not 200 <= int(response.status_code) < 300:
            error = SearxSearchError(f"native SearXNG returned status {response.status_code}")
            self.last_error = error
            raise error
        try:
            payload = response.get_json(silent=False)
        except Exception as exc:
            error = SearxSearchError("native SearXNG returned invalid JSON")
            self.last_error = error
            raise error from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            error = SearxSearchError("native SearXNG returned an invalid result schema")
            self.last_error = error
            raise error

        safe_payload = dict(payload)
        safe_payload["results"] = [
            dict(item) for item in payload["results"][:max_results] if isinstance(item, dict)
        ]
        self.last_error = None
        return safe_payload

    def __enter__(self) -> NativeSearxRuntime:
        """Start and return this runtime for context-managed use."""
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Release the owned WSGI client on every context exit."""
        del exc_type, exc, traceback
        self.stop()


__all__ = [
    "NativeSearxRuntime",
    "RemoteSearxAdapter",
    "SearxError",
    "SearxLifecycleError",
    "SearxSearchError",
    "SearxUnavailableError",
    "default_namespace",
    "validate_namespace",
]
