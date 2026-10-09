"""Search SearXNG natively on the controller, with explicit remote fallback."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from typing import Any, Protocol, cast

from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_runtime import (
    NativeSearxRuntime,
    RemoteSearxAdapter,
    validate_query,
    validate_search_inputs,
)

from ._searxng import ControllerSearxAction, validate_controller_transport
from .searxng_instance import _RUNTIMES

_TRAVEL_ENGINES: dict[str, tuple[str, ...]] = {
    "flights": ("google_flights", "google_travel"),
    "hotels": ("booking", "hotelscombined", "tripadvisor"),
    "events": ("google_events", "ticketmaster", "eventbrite"),
    "activities": ("tripadvisor", "wikivoyage", "google_maps"),
    "restaurants": ("yelp", "tripadvisor", "google_maps"),
}


class SearchRuntime(Protocol):
    @property
    def instance_uri(self) -> str: ...

    def start(self) -> bool: ...

    def stop(self) -> bool: ...

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]: ...


RuntimeFactory = Callable[..., SearchRuntime]
RemoteFactory = Callable[..., SearchRuntime]


def _remote_factory(*, base_url: str, timeout: float) -> SearchRuntime:
    return cast(SearchRuntime, RemoteSearxAdapter(base_url=base_url, timeout=timeout))


def _engines(value: object, category: str) -> Sequence[str] | None:
    if isinstance(value, str):
        parsed = tuple(item.strip() for item in value.split(",") if item.strip())
        return parsed or _TRAVEL_ENGINES.get(category)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return value
    if value is None:
        return _TRAVEL_ENGINES.get(category)
    raise TypeError("engines must be a comma-separated string or string list")


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
    registry: MutableMapping[str, SearchRuntime] | None = None,
    runtime_factory: RuntimeFactory | None = None,
    remote_factory: RemoteFactory = _remote_factory,
) -> dict[str, Any]:
    """Execute one read-only search through the selected controller transport."""
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string")
    transport, namespace, remote_url, timeout = validate_controller_transport(
        args,
        default_namespace="gludd-travel",
        default_timeout=10,
        forbidden_args={
            "searxng_url": (
                "searxng_url is retired; use transport=remote with remote_url"
            )
        },
    )

    category = args.get("category", "general")
    if not isinstance(category, str) or not category:
        raise ValueError("category must be a non-empty string")
    selected_engines = _engines(args.get("engines"), category)
    max_results = args.get("max_results", 10)
    safe_search = args.get("safe_search", 0)
    language = args.get("language", "en")
    page = args.get("page", 1)
    time_range = args.get("time_range")
    validate_query(query, max_results)
    validate_search_inputs(
        categories=(category,),
        engines=selected_engines,
        language=language,
        safe_search=safe_search,
        page=page,
        time_range=time_range,
    )
    settings_path = args.get("settings_path") or None
    if settings_path is not None and not isinstance(settings_path, str):
        raise TypeError("settings_path must be a string")

    if check_mode:
        return {
            "changed": False,
            "check_mode": True,
            "query": query,
            "result_count": 0,
            "results": [],
            "transport": transport,
        }

    owned_runtime = False
    if transport == "native":
        selected_registry = cast(
            MutableMapping[str, SearchRuntime],
            registry if registry is not None else _RUNTIMES,
        )
        existing = selected_registry.get(namespace)
        runtime = existing if existing is not None else None
        if runtime is None:
            runtime_builder = runtime_factory or cast(RuntimeFactory, NativeSearxRuntime)
            runtime = runtime_builder(settings_path=settings_path, namespace=namespace)
            runtime.start()
            owned_runtime = True
        instance_uri = runtime.instance_uri
    else:
        runtime = remote_factory(base_url=remote_url, timeout=float(timeout))
        runtime.start()
        owned_runtime = True
        instance_uri = runtime.instance_uri

    try:
        payload = runtime.search(
            query,
            categories=("general",) if selected_engines else (category,),
            engines=selected_engines,
            language=language,
            safe_search=safe_search,
            page=page,
            time_range=time_range,
            max_results=max_results,
        )
    finally:
        if owned_runtime:
            runtime.stop()

    results = payload.get("results", [])
    if not isinstance(results, list):
        raise ValueError("SearXNG response results must be a list")
    return {
        "changed": False,
        "check_mode": False,
        "instance_uri": instance_uri,
        "query": query,
        "result_count": len(results),
        "results": results,
        "transport": transport,
        "unresponsive_engines": payload.get("unresponsive_engines", []),
    }


class ActionModule(ControllerSearxAction):
    """Keep the upstream package and search execution on the controller."""

    def _execute(
        self,
        args: dict[str, Any],
        *,
        check_mode: bool,
    ) -> dict[str, Any]:
        """Execute one native or explicit remote search."""
        return execute_action(args, check_mode=check_mode)


__all__ = ["ActionModule", "execute_action"]
