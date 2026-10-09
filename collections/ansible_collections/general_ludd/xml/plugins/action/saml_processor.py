"""Admit one signed SAML assertion on the Ansible controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ansible_collections.general_ludd.xml.plugins.module_utils.saml_admission import (
    Clock,
    SAMLAdmissionError,
    VerifierFactory,
    action_arguments,
    admit_saml_assertion,
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

else:
    from ansible.plugins.action import ActionBase as _ActionBase


def execute_action(
    args: dict[str, Any],
    *,
    check_mode: bool = False,
    verifier_factory: VerifierFactory | None = None,
    now: Clock | None = None,
) -> dict[str, object]:
    """Run the pure admission boundary locally in normal or check mode."""
    selected = action_arguments(args)
    if verifier_factory is not None:
        selected["verifier_factory"] = verifier_factory
    if now is not None:
        selected["now"] = now
    result = cast(dict[str, object], admit_saml_assertion(**selected))
    if check_mode:
        result["check_mode"] = True
    return result


class ActionModule(_ActionBase):
    """Keep XML, trust anchors, signature verification, and claims local."""

    TRANSFERS_FILES = False

    def run(
        self,
        tmp: str | None = None,
        task_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Translate bounded failures without exposing assertion or certificate data."""
        result = super().run(tmp, task_vars)
        try:
            admitted = execute_action(
                dict(self._task.args),
                check_mode=bool(getattr(self._task, "check_mode", False)),
            )
        except (SAMLAdmissionError, TypeError, ValueError) as exc:
            error = exc if isinstance(exc, SAMLAdmissionError) else SAMLAdmissionError(str(exc))
            admitted = error.as_result()
        result.update(admitted)
        return result


__all__ = ["ActionModule", "execute_action"]
