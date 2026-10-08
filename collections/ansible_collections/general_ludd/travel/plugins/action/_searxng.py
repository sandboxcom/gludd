"""Shared fail-closed controller action boundary for SearXNG."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_runtime import (
    SearxError,
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


__all__ = ["ControllerSearxAction"]
