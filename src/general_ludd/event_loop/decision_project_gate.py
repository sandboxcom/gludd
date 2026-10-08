"""Project-gate downgrade policy for task decision reconciliation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from general_ludd.schemas.todo import TodoStatus

logger = logging.getLogger(__name__)


async def apply_project_gate(
    loop: Any,
    decision_id: str,
    repo_root: str | None,
    status: TodoStatus,
) -> TodoStatus:
    """Downgrade completion when the bound project gate reports failure."""
    if repo_root is None:
        return status
    workspace = Path(repo_root)
    if not (workspace / "project.yml").is_file():
        return status
    from general_ludd.quality.project_gate import run_project_gate

    try:
        report = await loop._bounded_to_thread(run_project_gate, str(workspace))
    except Exception as exc:
        logger.warning(
            "Reconcile: project gate errored for decision %s "
            "(repo_root=%s): %s — leaving decision unchanged (fail-safe)",
            decision_id,
            repo_root,
            exc,
        )
        return status
    if not isinstance(report, dict) or report.get("passed"):
        return status
    checks = report.get("checks")
    summaries = (
        [
            str(check.get("summary") or f"{check.get('name', 'check')}: FAIL")
            for check in checks
            if isinstance(check, dict) and not check.get("passed", True)
        ]
        if isinstance(checks, list)
        else []
    )
    summary = "; ".join(summaries) if summaries else "project gate FAILED"
    logger.warning(
        "Reconcile: project gate FAILED for decision %s — "
        "downgrading complete -> needs_more_work: %s",
        decision_id,
        summary,
    )
    return TodoStatus.NEEDS_MORE_WORK
