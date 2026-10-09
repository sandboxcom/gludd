"""Execute one bounded OpenAPI query on the Ansible controller."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from ansible_collections.general_ludd.infrastructure.plugins.module_utils.openapi_registration import (
    Fetcher,
    OpenAPIRegistrationError,
    action_arguments,
    execute_openapi_query,
)

if TYPE_CHECKING:

    class _ActionBase:
        _task: Any

        def run(
            self,
            tmp: str | None = None,
            task_vars: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            raise NotImplementedError

    def _emit_progress(_message: str) -> None: ...

else:
    from ansible.plugins.action import ActionBase as _ActionBase
    from ansible.utils.display import Display

    _emit_progress = Display().vvvv


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
    fetcher: Fetcher | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Run validation locally and suppress network I/O in check mode."""
    selected = action_arguments(args)
    if check_mode:
        selected["validate_only"] = True
    if fetcher is not None:
        selected["fetcher"] = fetcher
    if progress is not None:
        selected["progress"] = progress
    result = cast(dict[str, object], execute_openapi_query(**selected))
    if check_mode:
        result["check_mode"] = True
    return result


class ActionModule(_ActionBase):
    """Keep contracts, credentials, and HTTPS execution on the controller."""

    TRANSFERS_FILES = False

    def _execute(
        self,
        args: dict[str, Any],
        *,
        check_mode: bool,
    ) -> dict[str, object]:
        return execute_action(args, check_mode=check_mode, progress=_emit_progress)

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate bounded failures without leaking headers or response bodies."""
        _emit_progress("openapi_query: initialize bounded controller action")
        result = super().run(tmp, task_vars)
        arguments = dict(self._task.args)
        check_mode = bool(getattr(self._task, "check_mode", False))
        try:
            _emit_progress("openapi_query: validate digest-bound contract")
            query_result = self._execute(arguments, check_mode=check_mode)
            _emit_progress("openapi_query: bounded controller action complete")
        except (OpenAPIRegistrationError, TypeError, ValueError) as exc:
            error = exc if isinstance(exc, OpenAPIRegistrationError) else OpenAPIRegistrationError(str(exc))
            query_result = error.as_result()
        result.update(query_result)
        return result


__all__ = ["ActionModule", "execute_action"]
