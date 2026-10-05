"""Ansible controller action for the packaged E2E generation pipeline."""

from __future__ import annotations

import subprocess
from pathlib import Path
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
from ansible_collections.general_ludd.e2e_test_gen.plugins.module_utils.pipeline_runner import (
    PipelineExecutionError,
    run_pipeline,
)


class ActionModule(ActionBase):
    """Run a bounded collection script from its installed Galaxy root."""

    TRANSFERS_FILES = False

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute the requested operation and return its structured artifact."""
        result = super().run(tmp, task_vars)
        arguments = dict(self._task.args)
        operation = arguments.pop("operation", "")
        if not isinstance(operation, str) or not operation:
            return {**result, "failed": True, "msg": "operation must be a non-empty string"}
        collection_root = Path(__file__).resolve().parents[2]
        try:
            pipeline_result = run_pipeline(collection_root, operation, arguments)
        except (OSError, PipelineExecutionError, subprocess.TimeoutExpired, TypeError, ValueError) as exc:
            return {**result, "failed": True, "msg": str(exc)}
        return {
            **result,
            **pipeline_result,
            "changed": operation == "write_tests",
        }
