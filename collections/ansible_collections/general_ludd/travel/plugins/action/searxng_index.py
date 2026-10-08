"""Manage bounded travel search profiles on the Ansible controller."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_client import (
    SearXNGIndex,
    TravelIndexManager,
)

from ._searxng import ControllerSearxAction
from .searxng_search import execute_action as execute_search_action

SearchExecutor = Callable[..., dict[str, Any]]

_MANAGER = TravelIndexManager()


def _engines(value: object) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        parsed = [item.strip() for item in value.split(",") if item.strip()]
    elif isinstance(value, list) and all(isinstance(item, str) for item in value):
        parsed = list(value)
    else:
        raise TypeError("engines must be a comma-separated string or string list")
    if not parsed:
        raise ValueError("engines must contain at least one name")
    return parsed


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
    manager: TravelIndexManager | None = None,
    search_executor: SearchExecutor = execute_search_action,
) -> dict[str, Any]:
    """Execute an index operation without transferring code to managed hosts."""
    selected_manager = manager if manager is not None else _MANAGER
    name = args.get("name", "travel-meta")
    state = args.get("state", "present")
    if not isinstance(name, str):
        raise TypeError("name must be a string")
    if state not in {"present", "absent", "list", "query"}:
        raise ValueError("state must be present, absent, list, or query")

    if state == "list":
        return {
            "changed": False,
            "check_mode": check_mode,
            "indices": selected_manager.list_all(),
            "state": state,
        }

    selected_engines = _engines(args.get("engines"))
    candidate = SearXNGIndex(name=name, engines=selected_engines)

    if state == "present":
        exists = selected_manager.has(candidate.name)
        if check_mode:
            return {
                "changed": not exists,
                "check_mode": True,
                "name": candidate.name,
                "state": state,
            }
        result = selected_manager.create(candidate.name, candidate.engines)
        return {"changed": not bool(result["existed"]), "state": state, **result}

    if state == "absent":
        exists = selected_manager.has(candidate.name)
        if check_mode or not exists:
            return {
                "changed": exists,
                "check_mode": check_mode,
                "name": candidate.name,
                "state": state,
            }
        selected_manager.delete(candidate.name)
        return {"changed": True, "name": candidate.name, "state": state}

    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query is required when state=query")
    max_results = args.get("max_results", 10)
    index = selected_manager.get(candidate.name)
    search_args: dict[str, Any] = {
        "query": query,
        "engines": index["engines"],
        "max_results": max_results,
        "transport": args.get("transport", "native"),
        "namespace": args.get("namespace", "gludd-travel"),
    }
    for key in (
        "remote_url",
        "settings_path",
        "timeout",
        "language",
        "safe_search",
        "page",
        "time_range",
    ):
        if key in args:
            search_args[key] = args[key]

    if check_mode:
        search_executor(search_args, check_mode=True)
        return {
            "changed": False,
            "check_mode": True,
            "name": candidate.name,
            "query": query,
            "result_count": 0,
            "results": [],
            "state": state,
        }

    def backend(
        query_text: str,
        *,
        engines: list[str],
        max_results: int,
    ) -> list[dict[str, Any]]:
        response = search_executor(
            {**search_args, "query": query_text, "engines": engines, "max_results": max_results},
            check_mode=False,
        )
        results = response.get("results")
        if not isinstance(results, list) or any(
            not isinstance(result, dict) for result in results
        ):
            raise ValueError("controller SearXNG backend returned invalid results")
        return [dict(result) for result in results]

    results = selected_manager.query(
        candidate.name,
        query,
        max_results=max_results,
        search_backend=backend,
    )
    return {
        "changed": False,
        "check_mode": False,
        "engines": index["engines"],
        "name": candidate.name,
        "query": query,
        "result_count": len(results),
        "results": results,
        "state": state,
    }


class ActionModule(ControllerSearxAction):
    """Keep profile state and native SearXNG execution on the controller."""

    def _execute(
        self,
        args: dict[str, Any],
        *,
        check_mode: bool,
    ) -> dict[str, Any]:
        return execute_action(args, check_mode=check_mode)


__all__ = ["ActionModule", "execute_action"]
