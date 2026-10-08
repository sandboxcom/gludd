"""Manage one controller-local native SearXNG runtime idempotently."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any, Protocol, cast

from general_ludd.searx.native import (
    NativeSearxRuntime,
    validate_namespace,
)

from ._searxng import ControllerSearxAction


class Runtime(Protocol):
    namespace: str
    instance_uri: str
    process_pid: int | None

    def start(self) -> bool: ...

    def stop(self) -> bool: ...

    def restart(self) -> bool: ...

    def is_running(self) -> bool: ...


RuntimeFactory = Callable[..., Runtime]
_RUNTIMES: dict[str, Runtime] = {}
_FORBIDDEN_GLUE = ("searxng_url", "project_path", "terraform_project_path")


def _runtime_result(
    runtime: Runtime | None,
    *,
    namespace: str,
    state: str,
    changed: bool,
    running: bool,
    check_mode: bool,
) -> dict[str, Any]:
    return {
        "changed": changed,
        "check_mode": check_mode,
        "instance_uri": runtime.instance_uri if runtime is not None else f"searx+python://{namespace}",
        "namespace": namespace,
        "process_pid": None,
        "running": running,
        "state": state,
        "transport": "native",
    }


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
    registry: MutableMapping[str, Runtime] | None = None,
    runtime_factory: RuntimeFactory | None = None,
) -> dict[str, Any]:
    """Apply a bounded lifecycle transition without a managed-host runtime."""
    for name in _FORBIDDEN_GLUE:
        if args.get(name) not in (None, ""):
            raise ValueError(f"{name} is not valid for native SearXNG lifecycle")

    state = args.get("state", "started")
    namespace = args.get("namespace", "gludd-travel")
    settings_path = args.get("settings_path") or None
    if state not in {"started", "stopped", "restarted", "status"}:
        raise ValueError("state must be started, stopped, restarted, or status")
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("namespace must be a non-empty string")
    namespace = validate_namespace(namespace)
    if settings_path is not None and not isinstance(settings_path, str):
        raise TypeError("settings_path must be a string")

    selected_registry = registry if registry is not None else _RUNTIMES
    runtime_builder = runtime_factory or cast(RuntimeFactory, NativeSearxRuntime)
    runtime = selected_registry.get(namespace)
    running = runtime.is_running() if runtime is not None else False

    if check_mode:
        changed = (
            (state == "started" and not running)
            or (state == "stopped" and running)
            or state == "restarted"
        )
        return _runtime_result(
            runtime,
            namespace=namespace,
            state=state,
            changed=changed,
            running=running,
            check_mode=True,
        )

    changed = False
    if state == "started":
        if runtime is None:
            runtime = runtime_builder(settings_path=settings_path, namespace=namespace)
            changed = runtime.start()
            selected_registry[namespace] = runtime
        elif not running:
            changed = runtime.start()
        running = True
    elif state == "stopped":
        if runtime is not None:
            changed = runtime.stop()
            selected_registry.pop(namespace, None)
        running = False
    elif state == "restarted":
        if runtime is None:
            runtime = runtime_builder(settings_path=settings_path, namespace=namespace)
            runtime.start()
            selected_registry[namespace] = runtime
        else:
            runtime.restart()
        changed = True
        running = True

    return _runtime_result(
        runtime,
        namespace=namespace,
        state=state,
        changed=changed,
        running=running,
        check_mode=False,
    )


class ActionModule(ControllerSearxAction):
    """Execute lifecycle work on the Ansible controller."""

    def _execute(
        self,
        args: dict[str, Any],
        *,
        check_mode: bool,
    ) -> dict[str, Any]:
        """Apply one native lifecycle transition."""
        return execute_action(args, check_mode=check_mode)


__all__ = ["ActionModule", "execute_action"]
