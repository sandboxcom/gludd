"""Shared fail-closed controller action boundary for SearXNG."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_runtime import (
    SearxError,
    validate_namespace,
)

if TYPE_CHECKING:

    class _ActionBase:
        _task: Any

        def run(
            self,
            tmp: str | None = None,
            task_vars: dict[str, Any] | None = None,
        ) -> dict[str, Any]: ...

else:
    from ansible.plugins.action import ActionBase as _ActionBase


class ControllerSearxAction(_ActionBase):
    """Translate one controller action into a stable Ansible result."""

    TRANSFERS_FILES = False

    def _execute(self, args: dict[str, Any], *, check_mode: bool) -> dict[str, Any]:
        """Execute the plugin-specific operation."""
        raise NotImplementedError

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run on the controller and convert expected failures for Ansible."""
        result = super().run(tmp, task_vars)
        try:
            result.update(
                self._execute(
                    dict(self._task.args),
                    check_mode=bool(getattr(self._task, "check_mode", False)),
                )
            )
        except (OSError, SearxError, TypeError, ValueError) as exc:
            result.update({"changed": False, "failed": True, "msg": str(exc)})
        return result


def validate_controller_transport(
    args: dict[str, Any],
    *,
    default_namespace: str,
    default_timeout: float,
    forbidden_args: Mapping[str, str] | None = None,
) -> tuple[str, str, str | None, float]:
    """Validate the transport fields shared by every controller action."""
    for name, message in (forbidden_args or {}).items():
        if args.get(name) not in (None, ""):
            raise ValueError(message)
    transport = args.get("transport", "native")
    if transport not in {"native", "remote"}:
        raise ValueError("transport must be native or remote")
    namespace = args.get("namespace", default_namespace)
    if not isinstance(namespace, str):
        raise TypeError("namespace must be a string")
    namespace = validate_namespace(namespace)
    remote_url = args.get("remote_url")
    if transport == "native" and remote_url not in (None, ""):
        raise ValueError("remote_url is only valid with transport=remote")
    if transport == "remote" and (
        not isinstance(remote_url, str) or not remote_url.strip()
    ):
        raise ValueError("remote_url is required with transport=remote")
    timeout = args.get("timeout", default_timeout)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= 120
    ):
        raise ValueError("timeout must be between 0 and 120 seconds")
    return transport, namespace, cast(str | None, remote_url), float(timeout)


__all__ = ["ControllerSearxAction", "validate_controller_transport"]
