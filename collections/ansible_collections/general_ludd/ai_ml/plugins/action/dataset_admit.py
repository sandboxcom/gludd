"""Validate and document a local CSV dataset on the Ansible controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ansible_collections.general_ludd.ai_ml.plugins.module_utils.dataset_admission import (
    DatasetAdmissionError,
    ValidationRunner,
    action_arguments,
    admit_dataset,
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


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
    validation_runner: ValidationRunner | None = None,
) -> dict[str, object]:
    """Execute one bounded admission without transferring code to a host."""
    selected = action_arguments(args)
    if validation_runner is None:
        result = cast(dict[str, object], admit_dataset(**selected))
    else:
        result = cast(
            dict[str, object],
            admit_dataset(**selected, validation_runner=validation_runner),
        )
    result["check_mode"] = check_mode
    return result


class ActionModule(_ActionBase):
    """Keep local dataset bytes and the Frictionless runtime on the controller."""

    TRANSFERS_FILES = False

    def _execute(
        self,
        args: dict[str, Any],
        *,
        check_mode: bool,
    ) -> dict[str, object]:
        return execute_action(args, check_mode=check_mode)

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate bounded validation failures into stable Ansible results."""
        result = super().run(tmp, task_vars)
        arguments = dict(self._task.args)
        check_mode = bool(getattr(self._task, "check_mode", False))
        try:
            admission = self._execute(arguments, check_mode=check_mode)
        except DatasetAdmissionError as exc:
            admission = exc.as_result()
        result.update(admission)
        return result


__all__ = ["ActionModule", "execute_action"]
