"""Execute one fail-closed Azure Log Analytics query on the controller."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from ansible.utils.display import Display
from ansible_collections.general_ludd.azure.plugins.module_utils.log_analytics_admission import (
    ClientFactory,
    CredentialFactory,
    LogAnalyticsAdmissionError,
    action_arguments,
    execute_log_analytics_query,
)

if TYPE_CHECKING:

    class _ActionBase:
        _task: Any

else:
    from ansible.plugins.action import ActionBase as _ActionBase

_emit_progress = Display().vvvv


def execute_action(
    args: dict[str, object],
    *,
    check_mode: bool = False,
    credential_factory: CredentialFactory | None = None,
    client_factory: ClientFactory | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Validate locally and suppress credential or network work in check mode."""
    selected = action_arguments(args)
    if check_mode:
        selected["validate_only"] = True
    return cast(
        dict[str, object],
        execute_log_analytics_query(
            **selected,
            credential_factory=credential_factory,
            client_factory=client_factory,
            progress=progress,
        ),
    )


class ActionModule(_ActionBase):
    """Keep query text, credentials, and LogsQueryClient on the controller."""

    TRANSFERS_FILES = False

    def _execute(
        self,
        args: dict[str, object],
        *,
        check_mode: bool,
    ) -> dict[str, object]:
        return execute_action(
            args,
            check_mode=check_mode,
            progress=_emit_progress,
        )

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate bounded failures without returning provider or query text."""
        self._task.no_log = True
        result = cast(dict[str, Any], cast(Any, super()).run(tmp, task_vars))
        result.pop("invocation", None)
        arguments = dict(self._task.args)
        check_mode = bool(getattr(self._task, "check_mode", False))
        try:
            _emit_progress("azure_log_analytics: validate controller action")
            query_result = self._execute(arguments, check_mode=check_mode)
        except (LogAnalyticsAdmissionError, TypeError, ValueError) as exc:
            error = (
                exc
                if isinstance(exc, LogAnalyticsAdmissionError)
                else LogAnalyticsAdmissionError(str(exc))
            )
            query_result = error.as_result()
        result.update(query_result)
        return result


__all__ = ["ActionModule", "execute_action"]
