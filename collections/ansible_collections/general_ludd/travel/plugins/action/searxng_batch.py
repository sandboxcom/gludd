"""Execute a bounded batch of SearXNG searches on the controller."""

from __future__ import annotations

import re
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_runtime import (
    NativeSearxRuntime,
    RemoteSearxAdapter,
    validate_query,
    validate_search_inputs,
)

from ._searxng import ControllerSearxAction, validate_controller_transport
from .searxng_instance import _RUNTIMES

_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
_MAX_REQUESTS = 32
_MAX_AGGREGATE_RESULTS = 500
_REQUEST_KEYS = frozenset(
    {
        "id",
        "query",
        "categories",
        "engines",
        "language",
        "safe_search",
        "page",
        "time_range",
        "max_results",
    }
)
_FORBIDDEN_GLUE = {
    name: f"{name} is not valid for SearXNG batch execution"
    for name in ("searxng_url", "project_path", "terraform_project_path")
}
_ACTION_KEYS = frozenset(
    {"requests", "transport", "remote_url", "namespace", "settings_path", "timeout"}
)


class BatchRuntime(Protocol):
    """Small runtime surface shared by native and explicit remote transports."""

    instance_uri: str
    process_pid: int | None

    def start(self) -> bool: ...

    def stop(self) -> bool: ...

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


RuntimeFactory = Callable[..., BatchRuntime]
RemoteFactory = Callable[..., BatchRuntime]


@dataclass(frozen=True, slots=True)
class _Request:
    identifier: str
    query: str
    categories: tuple[str, ...]
    engines: tuple[str, ...]
    language: str
    safe_search: int
    page: int
    time_range: str | None
    max_results: int


def _remote_factory(*, base_url: str, timeout: float) -> BatchRuntime:
    return cast(BatchRuntime, RemoteSearxAdapter(base_url=base_url, timeout=timeout))


def _names(value: object, *, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parsed = tuple(item.strip() for item in value.split(",") if item.strip())
        if not parsed:
            raise ValueError(f"{name} must contain at least one bounded name")
        return parsed
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not all(isinstance(item, str) for item in value):
            raise TypeError(f"{name} must be a string or string list")
        return tuple(cast(Sequence[str], value))
    raise TypeError(f"{name} must be a string or string list")


def _request(raw: object) -> _Request:
    if not isinstance(raw, dict):
        raise TypeError("each request must be a mapping")
    unknown = set(raw).difference(_REQUEST_KEYS)
    if unknown:
        raise ValueError(f"request contains unsupported keys: {', '.join(sorted(unknown))}")
    identifier = raw.get("id")
    if not isinstance(identifier, str) or _ID_RE.fullmatch(identifier) is None:
        raise ValueError(
            "request id must be 1-64 characters beginning with a letter or underscore"
        )
    query = raw.get("query")
    max_results = raw.get("max_results", 10)
    validate_query(query, max_results)
    categories = _names(raw.get("categories"), name="categories") or ("general",)
    engines = _names(raw.get("engines"), name="engines")
    language = raw.get("language", "en")
    safe_search = raw.get("safe_search", 0)
    page = raw.get("page", 1)
    time_range = raw.get("time_range")
    validate_search_inputs(
        categories=categories,
        engines=engines,
        language=language,
        safe_search=safe_search,
        page=page,
        time_range=time_range,
    )
    return _Request(
        identifier=identifier,
        query=cast(str, query),
        categories=categories,
        engines=engines,
        language=cast(str, language),
        safe_search=cast(int, safe_search),
        page=cast(int, page),
        time_range=cast(str | None, time_range),
        max_results=cast(int, max_results),
    )


def _requests(value: object) -> tuple[_Request, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_REQUESTS:
        raise ValueError(f"requests must contain between 1 and {_MAX_REQUESTS} items")
    requests = tuple(_request(raw) for raw in value)
    identifiers = [request.identifier for request in requests]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("request ids must be unique")
    if sum(request.max_results for request in requests) > _MAX_AGGREGATE_RESULTS:
        raise ValueError(
            f"aggregate max_results must not exceed {_MAX_AGGREGATE_RESULTS}"
        )
    return requests


def _transport(args: dict[str, Any]) -> tuple[str, str, str | None, float]:
    return validate_controller_transport(
        args,
        default_namespace="gludd-searx-consumers",
        default_timeout=30,
        forbidden_args=_FORBIDDEN_GLUE,
    )


def _response(payload: object, *, max_results: int) -> tuple[dict[str, Any], int]:
    if not isinstance(payload, dict):
        raise ValueError("SearXNG response must be a mapping")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("SearXNG response results must be a list")
    if len(results) > max_results:
        raise ValueError(f"per-query result count exceeds {max_results}")
    if not all(isinstance(item, dict) for item in results):
        raise ValueError("SearXNG response results must contain only mappings")
    safe_payload = dict(payload)
    safe_payload["results"] = [dict(item) for item in results]
    number_of_results = safe_payload.get("number_of_results", len(safe_payload["results"]))
    if isinstance(number_of_results, bool) or not isinstance(number_of_results, int):
        raise ValueError("SearXNG number_of_results must be an integer")
    if number_of_results < 0:
        raise ValueError("SearXNG number_of_results must not be negative")
    safe_payload["number_of_results"] = number_of_results
    return (
        {
            "changed": False,
            "failed": False,
            "status": 200,
            "json": safe_payload,
        },
        len(safe_payload["results"]),
    )


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
    registry: MutableMapping[str, BatchRuntime] | None = None,
    runtime_factory: RuntimeFactory | None = None,
    remote_factory: RemoteFactory = _remote_factory,
) -> dict[str, Any]:
    """Validate, execute, and atomically return one bounded search batch."""
    unknown_args = set(args).difference(_ACTION_KEYS, _FORBIDDEN_GLUE)
    if unknown_args:
        raise ValueError(
            f"batch contains unsupported arguments: {', '.join(sorted(unknown_args))}"
        )
    requests = _requests(args.get("requests"))
    transport, namespace, remote_url, timeout = _transport(args)
    raw_settings_path = args.get("settings_path")
    if raw_settings_path is not None and not isinstance(raw_settings_path, str):
        raise TypeError("settings_path must be a string")
    settings_path = raw_settings_path or None
    if check_mode:
        check_responses = {
            request.identifier: {
                "changed": False,
                "failed": False,
                "skipped": True,
                "status": 0,
                "json": {
                    "query": request.query,
                    "results": [],
                    "number_of_results": 0,
                },
            }
            for request in requests
        }
        return {
            "changed": False,
            "check_mode": True,
            "query_count": len(requests),
            "result_count": 0,
            "responses": check_responses,
            "transport": transport,
        }

    owned_runtime = False
    if transport == "native":
        selected_registry = cast(
            MutableMapping[str, BatchRuntime],
            registry if registry is not None else _RUNTIMES,
        )
        runtime = selected_registry.get(namespace)
        if runtime is None:
            runtime_builder = runtime_factory or cast(RuntimeFactory, NativeSearxRuntime)
            runtime = runtime_builder(settings_path=settings_path, namespace=namespace)
            runtime.start()
            owned_runtime = True
    else:
        runtime = remote_factory(base_url=remote_url, timeout=timeout)
        runtime.start()
        owned_runtime = True

    responses: dict[str, dict[str, Any]] = {}
    result_count = 0
    try:
        for request in requests:
            payload = runtime.search(
                request.query,
                categories=request.categories,
                engines=request.engines,
                language=request.language,
                safe_search=request.safe_search,
                page=request.page,
                time_range=request.time_range,
                max_results=request.max_results,
            )
            response, count = _response(payload, max_results=request.max_results)
            result_count += count
            responses[request.identifier] = response
    finally:
        if owned_runtime:
            runtime.stop()

    return {
        "changed": False,
        "check_mode": False,
        "instance_uri": runtime.instance_uri,
        "process_pid": None,
        "query_count": len(requests),
        "result_count": result_count,
        "responses": responses,
        "transport": transport,
    }


class ActionModule(ControllerSearxAction):
    """Keep every query in the batch on the Ansible controller."""

    def _execute(
        self,
        args: dict[str, Any],
        *,
        check_mode: bool,
    ) -> dict[str, Any]:
        return execute_action(args, check_mode=check_mode)


__all__ = ["ActionModule", "execute_action"]
