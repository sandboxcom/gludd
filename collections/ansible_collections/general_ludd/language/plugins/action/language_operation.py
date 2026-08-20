"""Run a language operation on the authenticated Gludd daemon."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    class ActionBase:
        """Typed facade for Ansible's dynamically exported action base."""

        _task: Any

        def run(
            self,
            tmp: str | None = None,
            task_vars: dict[str, Any] | None = None,
        ) -> dict[str, Any]: ...

else:
    from ansible.plugins.action import ActionBase
from ansible_collections.general_ludd.language.plugins.module_utils.core import (
    LanguageClient,
    LanguageServiceError,
)

ClientFactory = Callable[..., LanguageClient]


def execute_action(
    args: dict[str, Any],
    *,
    client_factory: ClientFactory = LanguageClient,
) -> dict[str, Any]:
    """Execute validated action arguments through one reusable client."""
    operation = args.get("operation")
    payload = args.get("payload", {})
    daemon_url = args.get("daemon_url", "http://localhost:8000")
    psk = args.get("psk")
    timeout = args.get("timeout", 30)
    if not isinstance(operation, str) or not operation.strip():
        raise ValueError("operation must be a non-empty string")
    if not isinstance(payload, dict):
        raise TypeError("payload must be a dictionary")
    if not isinstance(daemon_url, str) or not daemon_url.strip():
        raise ValueError("daemon_url must be a non-empty string")
    if not isinstance(psk, str) or not psk.strip():
        raise ValueError("psk must be a non-empty string")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ValueError("timeout must be a positive integer")

    client = client_factory(daemon_url=daemon_url, psk=psk, timeout=timeout)
    return {
        "changed": False,
        "failed": False,
        "result": client.execute(operation, payload),
    }


class ActionModule(ActionBase):
    """Keep analysis on the controller and return data for remote artifact writes."""

    TRANSFERS_FILES = False

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = super().run(tmp, task_vars)
        try:
            result.update(execute_action(dict(self._task.args)))
        except (LanguageServiceError, TypeError, ValueError) as exc:
            result.update({"changed": False, "failed": True, "msg": str(exc)})
        return result


__all__ = ["ActionModule", "execute_action"]
