"""Triage one controller-local JUnit report without transferring its bytes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ansible_collections.general_ludd.git_release.plugins.module_utils.pipeline_triage import (
    PipelineTriageError,
    action_arguments,
    triage_junit_report,
)

if TYPE_CHECKING:

    class _ActionBase:
        _task: Any

        def run(self, *args: Any, **kwargs: Any) -> dict[str, Any]: ...

else:
    from ansible.plugins.action import ActionBase as _ActionBase


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
) -> dict[str, object]:
    """Execute the same read-only triage in normal and check mode."""
    del check_mode
    selected = action_arguments(args)
    triage = triage_junit_report(**selected)
    return {"changed": False, "triage": triage}


class ActionModule(_ActionBase):
    """Keep the report and hardened XML parser on the controller."""

    TRANSFERS_FILES = False

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate only bounded, content-free rejections for Ansible."""
        result = super().run(tmp, task_vars)
        task = self._task
        try:
            triage = execute_action(
                dict(task.args),
                check_mode=bool(getattr(task, "check_mode", False)),
            )
        except PipelineTriageError as exc:
            triage = exc.as_result()
        result.update(triage)
        return result


__all__ = ["ActionModule", "execute_action"]
