"""Fail-closed validation-request admission for reviewed task decisions."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)

_VALIDATION_COMMAND = re.compile(r"make [A-Za-z0-9_.-]{1,128}\Z")
_MAX_VALIDATION_COMMANDS = 16
_DEFAULT_VALIDATION_TIMEOUT_SECONDS = 300.0
_MAX_VALIDATION_TIMEOUT_SECONDS = 600.0


def validation_decision_key(decision_id: str) -> str:
    """Keep validation dispatch idempotency distinct from completed delivery."""
    return f"validation:{decision_id}"


def _validation_config(loop: Any) -> Mapping[str, object] | None:
    """Return the explicitly enabled configuration; absence stays default-off."""
    raw_config = getattr(loop, "config", None)
    config = raw_config if isinstance(raw_config, Mapping) else {}
    candidate = config.get("validation_requests")
    if not isinstance(candidate, Mapping) or candidate.get("enabled") is not True:
        return None
    return candidate


def _parse_validation_requests(decision: Any) -> list[str] | None:
    """Decode one bounded request without interpreting its prose as instructions."""
    try:
        requests = json.loads(getattr(decision, "validation_requests", None) or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        not isinstance(requests, list)
        or len(requests) > 1
        or any(not isinstance(request, str) or not request.strip() for request in requests)
    ):
        return None
    return requests


def _validation_commands(config: Mapping[str, object]) -> tuple[str, ...] | None:
    """Admit only the make-only command grammar enforced by the playbook."""
    commands = config.get("commands")
    if (
        not isinstance(commands, list)
        or not 1 <= len(commands) <= _MAX_VALIDATION_COMMANDS
        or any(
            not isinstance(command, str)
            or _VALIDATION_COMMAND.fullmatch(command) is None
            for command in commands
        )
    ):
        return None
    return tuple(commands)


def _validation_timeout(config: Mapping[str, object]) -> float | None:
    """Return one finite worker timeout constrained to its deployed ceiling."""
    raw_timeout = config.get("timeout_seconds", _DEFAULT_VALIDATION_TIMEOUT_SECONDS)
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (int, float)):
        return None
    timeout = float(raw_timeout)
    if not math.isfinite(timeout) or not 0.0 < timeout <= _MAX_VALIDATION_TIMEOUT_SECONDS:
        return None
    return timeout


def _validation_worktree(repo_root: str | None) -> str | None:
    """Resolve the trusted project binding; reviewer text never supplies a path."""
    if repo_root is None:
        return None
    try:
        worktree = Path(repo_root).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError):
        return None
    return str(worktree) if worktree.is_dir() else None


async def apply_requested_validation(
    loop: Any,
    decision: Any,
    todo: Any,
    decision_id: str,
    repo_root: str | None,
    status: TodoStatus,
) -> TodoStatus | None:
    """Dispatch one trusted validation job or return a fail-closed status.

    ``None`` means that a TaskReturn was durably persisted and the todo is back
    in its ordinary result-review lifecycle. No completion transition is then
    allowed for the requesting decision.
    """
    if status is not TodoStatus.COMPLETE:
        return status
    config = _validation_config(loop)
    if config is None:
        return status
    requests = _parse_validation_requests(decision)
    if requests is None:
        return TodoStatus.BLOCKED
    if not requests:
        return status
    worktree_path = _validation_worktree(repo_root)
    commands = _validation_commands(config)
    if worktree_path is None or commands is None:
        return TodoStatus.NEEDS_MORE_WORK
    timeout_seconds = _validation_timeout(config)
    if timeout_seconds is None:
        return TodoStatus.BLOCKED
    try:
        persisted = await loop._dispatch_validate_job(
            todo,
            decision_id=decision_id,
            worktree_path=worktree_path,
            test_commands=commands,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        logger.warning(
            "Validation dispatch failed for todo %s",
            todo.todo_id,
            exc_info=True,
        )
        return TodoStatus.BLOCKED
    if not persisted:
        return TodoStatus.BLOCKED
    loop._ledger_add(loop._applied_decisions, validation_decision_key(decision_id))
    return None
