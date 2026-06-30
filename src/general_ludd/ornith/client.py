"""OrnithClient: MCP client adapter for the ornith self-improving coding agent."""

from __future__ import annotations

import pathlib
from typing import Any, cast

_MAX_ITERATIONS_CAP = 50
_STS_TTL_SECONDS = 300
_VALID_ARTIFACT_KINDS = frozenset({"playbook", "module", "plugin", "rego"})


class OrnithClient:
    """Client adapter that calls the ornith MCP server.

    All operations are gated by ``permission_spec`` (capability check), mint
    a short-lived STS token via ``sts_registry``, forward the call to the MCP
    ``transport``, and record an audit row on success.
    """

    def __init__(
        self,
        mcp_socket_path: pathlib.Path,
        permission_spec: Any,
        sts_registry: Any,
        audit_recorder: Any = None,
        transport: Any = None,
    ) -> None:
        self._mcp_socket_path = mcp_socket_path
        self._permission_spec = permission_spec
        self._sts_registry = sts_registry
        self._audit_recorder = audit_recorder
        self._transport = transport

    def solve(
        self,
        task_description: str,
        repo_context_path: str,
        max_iterations: int = 10,
        target_files: list[str] | None = None,
    ) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        if max_iterations > _MAX_ITERATIONS_CAP:
            raise ValueError(
                f"max_iterations {max_iterations} exceeds cap {_MAX_ITERATIONS_CAP}"
            )
        if not self._permission_spec.has_capability("agent:ornith", "solve"):
            raise PermissionError(
                "permission spec lacks agent:ornith/solve capability"
            )

        token = self._sts_registry.mint(
            "agent:ornith",
            {"resource": "agent:ornith", "actions": ["solve"]},
            _STS_TTL_SECONDS,
        )

        arguments: dict[str, Any] = {
            "task_description": task_description,
            "repo_context_path": repo_context_path,
            "max_iterations": max_iterations,
            "target_files": target_files if target_files is not None else [],
            "sts_token": token,
        }

        result = cast("dict[str, Any]", self._transport.call_tool("ornith_solve", arguments))

        self._record_audit(
            actor="agent:ornith",
            task=task_description,
            outcome=result.get("outcome", "success"),
            sts_token=token,
            iterations_used=result.get("iterations_used"),
            tokens_consumed=result.get("tokens_consumed"),
        )

        return result

    def improve(
        self,
        target_artifact_path: str,
        feedback_yaml: str,
        artifact_kind: str,
    ) -> dict[str, Any]:
        result: dict[str, Any] | None = None
        if artifact_kind not in _VALID_ARTIFACT_KINDS:
            raise ValueError(
                f"invalid artifact_kind {artifact_kind!r}; "
                f"expected one of {sorted(_VALID_ARTIFACT_KINDS)}"
            )
        if not self._permission_spec.has_capability("agent:ornith", "improve"):
            raise PermissionError(
                "permission spec lacks agent:ornith/improve capability"
            )

        token = self._sts_registry.mint(
            "agent:ornith",
            {"resource": "agent:ornith", "actions": ["improve"]},
            _STS_TTL_SECONDS,
        )

        arguments: dict[str, Any] = {
            "target_artifact_path": target_artifact_path,
            "feedback_yaml": feedback_yaml,
            "artifact_kind": artifact_kind,
            "sts_token": token,
        }

        result = cast("dict[str, Any]", self._transport.call_tool("ornith_improve", arguments))

        self._record_audit(
            actor="agent:ornith",
            target_artifact_path=target_artifact_path,
            artifact_kind=artifact_kind,
            outcome=result.get("outcome", "success"),
            sts_token=token,
        )

        return result

    def _record_audit(self, **kwargs: Any) -> None:
        if self._audit_recorder is None:
            return
        try:
            self._audit_recorder.record(**kwargs)
        except Exception:
            return
