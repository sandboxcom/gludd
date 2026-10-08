"""Bounded controller-native binding to the official SearXNG WSGI app.

The collection invokes ``searx.webapp`` through its Flask test client.  Native
mode therefore owns no subprocess, listener, container, or managed-host Python
runtime.  HTTP remains available only through :class:`RemoteSearxAdapter`, an
explicit compatibility rollback selected by the operator.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import re
import secrets
import stat
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

_NAMESPACE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_OPTION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_IMPORT_LOCK = threading.RLock()
_MAX_QUERY_CHARS = 2048
_MAX_RESULTS = 100
_MAX_OPTIONS = 20
_MAX_PAGE = 100


class SearxError(RuntimeError):
    """Base failure for native and compatibility SearXNG transports."""


class SearxUnavailableError(SearxError):
    """The official pinned SearXNG package cannot provide its WSGI app."""


class SearxLifecycleError(SearxError):
    """A bounded runtime could not start, restart, or release resources."""


class SearxSearchError(SearxError):
    """A search did not return a valid bounded result payload."""


class _WsgiResponse(Protocol):
    status_code: int

    def get_json(self, *, silent: bool = False) -> object: ...


class _WsgiClient(Protocol):
    def get(self, path: str, **kwargs: Any) -> _WsgiResponse: ...

    def close(self) -> None: ...


class _WsgiApp(Protocol):
    def test_client(self) -> _WsgiClient: ...


class _RemoteConnector(Protocol):
    def health(self) -> dict[str, object]: ...

    def search(self, query: str, **kwargs: Any) -> Sequence[Any]: ...


ModuleLoader = Callable[[str], object]
ConnectorFactory = Callable[[dict[str, object]], _RemoteConnector]


def default_namespace() -> str:
    """Return a stable project-scoped resource namespace."""
    configured = os.environ.get("GLUDD_RESOURCE_NAMESPACE", "").strip()
    if configured:
        return validate_namespace(configured)
    project = str(Path.cwd().resolve()).encode("utf-8")
    digest = hashlib.sha256(project).hexdigest()[:12]
    return f"gludd-searx-{digest}"


def validate_namespace(namespace: str) -> str:
    """Validate one bounded component used in identities and temp paths."""
    if not isinstance(namespace, str) or _NAMESPACE_RE.fullmatch(namespace) is None:
        raise ValueError(
            "namespace must be 1-64 characters using only letters, digits, '.', '_', or '-'"
        )
    return namespace


def _close_client(client: _WsgiClient) -> None:
    close = getattr(client, "close", None)
    if callable(close):
        close()


def _bounded_options(values: Sequence[str] | None, *, name: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or len(values) > _MAX_OPTIONS:
        raise ValueError(f"{name} must contain at most {_MAX_OPTIONS} bounded names")
    result = tuple(values)
    if any(not isinstance(value, str) or _OPTION_RE.fullmatch(value) is None for value in result):
        raise ValueError(f"{name} contains an invalid name")
    return result


def validate_query(query: str, max_results: int) -> str:
    """Validate bounded search text and result count without allocating."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    if "\x00" in query or len(query) > _MAX_QUERY_CHARS:
        raise ValueError(
            f"query must be at most {_MAX_QUERY_CHARS} characters without NUL bytes"
        )
    if isinstance(max_results, bool) or not isinstance(max_results, int):
        raise ValueError(f"max_results must be between 1 and {_MAX_RESULTS}")
    if not 1 <= max_results <= _MAX_RESULTS:
        raise ValueError(f"max_results must be between 1 and {_MAX_RESULTS}")
    return query


def validate_search_inputs(
    *,
    categories: Sequence[str] | None,
    engines: Sequence[str] | None,
    language: str,
    safe_search: int,
    page: int,
    time_range: str | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Validate and normalize bounded options without starting SearXNG."""
    selected_categories = _bounded_options(categories, name="categories")
    selected_engines = _bounded_options(engines, name="engines")
    if not isinstance(language, str) or _OPTION_RE.fullmatch(language) is None:
        raise ValueError("language must be a bounded language identifier")
    if (
        isinstance(safe_search, bool)
        or not isinstance(safe_search, int)
        or safe_search not in {0, 1, 2}
    ):
        raise ValueError("safe_search must be 0, 1, or 2")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= _MAX_PAGE:
        raise ValueError(f"page must be between 1 and {_MAX_PAGE}")
    if time_range not in {None, "day", "week", "month", "year"}:
        raise ValueError("time_range must be day, week, month, or year")
    return selected_categories, selected_engines


class NativeSearxRuntime:
    """Own one direct controller binding to the upstream WSGI application."""

    def __init__(
        self,
        *,
        settings_path: str | Path | None = None,
        namespace: str | None = None,
        module_loader: ModuleLoader = importlib.import_module,
    ) -> None:
        self.namespace = validate_namespace(
            default_namespace() if namespace is None else namespace
        )
        self._configured_settings_path = (
            Path(settings_path).expanduser() if settings_path else None
        )
        self._module_loader = module_loader
        self._app: _WsgiApp | None = None
        self._client: _WsgiClient | None = None
        self._settings_dir: tempfile.TemporaryDirectory[str] | None = None
        self._lock = threading.RLock()
        self.last_error: SearxError | None = None

    @property
    def instance_uri(self) -> str:
        """Return a non-network identity for this controller resource."""
        return f"searx+python://{self.namespace}"

    @property
    def process_pid(self) -> None:
        """Native operation owns no child process."""
        return None

    @property
    def settings_path(self) -> Path:
        """Return an operator file or create an owner-only ephemeral config."""
        if self._configured_settings_path is None:
            directory = tempfile.TemporaryDirectory(
                prefix=f"gludd-searx-{self.namespace}-"
            )
            path = Path(directory.name) / "settings.yml"
            path.write_text(
                "use_default_settings: true\n"
                "search:\n  formats: [json]\n"
                "server:\n"
                f"  secret_key: {secrets.token_hex(32)}\n"
                "  limiter: false\n  image_proxy: false\n",
                encoding="utf-8",
            )
            path.chmod(0o600)
            self._settings_dir = directory
            self._configured_settings_path = path
        return self._configured_settings_path

    def _cleanup_owned_settings(self) -> None:
        directory = self._settings_dir
        if directory is None:
            return
        self._settings_dir = None
        self._configured_settings_path = None
        directory.cleanup()

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
                    "official SearXNG pinned package is unavailable (expected searx.webapp)"
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
            return int(client.get("/healthz").status_code) == 200
        except Exception:
            return False

    def start(self) -> bool:
        """Create a healthy binding; release generated settings on failure."""
        with self._lock:
            if self._client is not None:
                return False
            try:
                app = self._load_app()
                client = app.test_client()
                if not self._healthy(client):
                    _close_client(client)
                    raise SearxLifecycleError(
                        "native SearXNG health check failed during startup"
                    )
            except SearxError:
                self._cleanup_owned_settings()
                raise
            except Exception as exc:
                self._cleanup_owned_settings()
                raise SearxLifecycleError(
                    "could not create native SearXNG WSGI client"
                ) from exc
            self._app = app
            self._client = client
            self.last_error = None
            return True

    def restart(self) -> bool:
        """Swap in a healthy client before closing the active client."""
        with self._lock:
            if self._client is None:
                return self.start()
            if self._app is None:
                raise SearxLifecycleError(
                    "native SearXNG runtime has no application binding"
                )
            try:
                replacement = self._app.test_client()
            except Exception as exc:
                raise SearxLifecycleError(
                    "could not create replacement SearXNG WSGI client"
                ) from exc
            if not self._healthy(replacement):
                _close_client(replacement)
                raise SearxLifecycleError("replacement SearXNG health check failed")
            current = self._client
            self._client = replacement
            _close_client(current)
            self.last_error = None
            return True

    def stop(self) -> bool:
        """Release the WSGI client and any generated settings idempotently."""
        with self._lock:
            client = self._client
            if client is None:
                self._cleanup_owned_settings()
                return False
            self._client = None
            self._app = None
            try:
                _close_client(client)
            except Exception as exc:
                error = SearxLifecycleError("native SearXNG client cleanup failed")
                self.last_error = error
                raise error from exc
            finally:
                self._cleanup_owned_settings()
            return True

    def is_running(self) -> bool:
        """Return live health without allocating a resource."""
        with self._lock:
            return self._client is not None and self._healthy(self._client)

    @staticmethod
    def _validate_query(query: str, max_results: int) -> str:
        return validate_query(query, max_results)

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
        """Execute one bounded JSON request through the in-process WSGI app."""
        query = self._validate_query(query, max_results)
        selected_categories, selected_engines = validate_search_inputs(
            categories=categories,
            engines=engines,
            language=language,
            safe_search=safe_search,
            page=page,
            time_range=time_range,
        )
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
        if selected_engines:
            params["engines"] = ",".join(selected_engines)
        else:
            params["categories"] = ",".join(selected_categories or ("general",))
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
            error = SearxSearchError(
                f"native SearXNG returned status {response.status_code}"
            )
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
            dict(item)
            for item in payload["results"][:max_results]
            if isinstance(item, dict)
        ]
        self.last_error = None
        return safe_payload

    def __enter__(self) -> NativeSearxRuntime:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        self.stop()


class _CollectionRemoteConnector:
    """Adapt the shared collection HTTP client for rollback-only transport."""

    def __init__(self, config: dict[str, object]) -> None:
        from ansible_collections.general_ludd.agent.plugins.module_utils.searxng import (
            SearXNGClient,
        )

        raw_timeout = config["timeout"]
        if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
            raise ValueError("remote connector timeout must be numeric")
        self._client = SearXNGClient(
            base_url=str(config["base_url"]),
            timeout=int(raw_timeout),
        )

    def health(self) -> dict[str, object]:
        return cast(dict[str, object], self._client.health())

    def search(
        self,
        query: str,
        *,
        page: int,
        categories: str,
    ) -> Sequence[Any]:
        response = self._client.search(
            query,
            max_results=_MAX_RESULTS,
            categories=[item for item in categories.split(",") if item],
            page=page,
        )
        return cast(Sequence[Any], response.results)


def _serialise_remote_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return dict(result)
    if is_dataclass(result) and not isinstance(result, type):
        return asdict(result)
    raise SearxSearchError("remote SearXNG returned an invalid result item")


class RemoteSearxAdapter:
    """Explicit compatibility adapter for an operator-managed HTTP endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 10.0,
        connector_factory: ConnectorFactory | None = None,
    ) -> None:
        if not isinstance(base_url, str) or len(base_url) > 2048:
            raise ValueError("base_url is required for remote SearXNG transport")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            raise ValueError("base_url must be an absolute HTTP(S) URL without credentials")
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
            or timeout > 120
        ):
            raise ValueError("timeout must be between 0 and 120 seconds")
        self.base_url = base_url.rstrip("/")
        self.instance_uri = self.base_url
        self.process_pid = None
        factory = connector_factory or _CollectionRemoteConnector
        self._connector = factory(
            {"base_url": self.base_url, "timeout": float(timeout)}
        )
        self._started = False

    def start(self) -> bool:
        if self._started:
            return False
        if self._connector.health().get("ok") is not True:
            raise SearxLifecycleError(
                "remote SearXNG compatibility endpoint is unhealthy"
            )
        self._started = True
        return True

    def stop(self) -> bool:
        changed = self._started
        self._started = False
        return changed

    def is_running(self) -> bool:
        return self._started

    def search(
        self,
        query: str,
        *,
        categories: Sequence[str] | None = None,
        page: int = 1,
        max_results: int = 10,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        NativeSearxRuntime._validate_query(query, max_results)
        if not self._started:
            raise SearxLifecycleError("remote SearXNG adapter is not started")
        results = self._connector.search(
            query,
            page=page,
            categories=",".join(categories or ("general",)),
        )
        serialised = [
            _serialise_remote_result(result) for result in results[:max_results]
        ]
        return {
            "query": query,
            "results": serialised,
            "number_of_results": len(serialised),
        }


__all__ = [
    "NativeSearxRuntime",
    "RemoteSearxAdapter",
    "SearxError",
    "SearxLifecycleError",
    "SearxSearchError",
    "SearxUnavailableError",
    "default_namespace",
    "validate_namespace",
    "validate_query",
    "validate_search_inputs",
]
