"""Run one materials tolerance analysis natively on the controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ansible_collections.general_ludd.materials.plugins.module_utils.tolerance_model import (
    ToleranceModelError,
    action_arguments,
    evaluate_tolerance_model,
)


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
) -> dict[str, object]:
    """Execute the identical pure analysis in normal and check mode."""
    del check_mode
    operation, request = action_arguments(args)
    return {
        "changed": False,
        "result": evaluate_tolerance_model(operation, request),
    }


if TYPE_CHECKING:

    class _ControllerActionBase:
        _task: Any

        def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

else:
    from ansible.plugins.action import ActionBase as _ControllerActionBase


def _execute_task(task: Any) -> dict[str, object]:
    """Execute a task or reduce its bounded validation rejection."""
    try:
        return execute_action(
            dict(task.args),
            check_mode=bool(getattr(task, "check_mode", False)),
        )
    except ToleranceModelError as exc:
        failure: dict[str, object] = exc.as_result()
        return failure


class ActionModule(_ControllerActionBase):
    """Keep tolerance inputs and calculation on the Ansible controller."""

    TRANSFERS_FILES = False

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate only bounded validation failures into Ansible results."""
        result: dict[str, Any] = super().run(tmp, task_vars)
        result.update(_execute_task(self._task))
        return result


__all__ = ["ActionModule", "execute_action"]
