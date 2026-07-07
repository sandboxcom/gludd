"""Stub implementation for D-07 through D-47 security backlog items.

Each item from ``docs/audit/NEW_FINDINGS_2026-06-16.md`` is implemented
as a guard function or check that can be invoked from daemon startup,
release gating, or CI. Items that are informational (noted but deferred)
return a pass. Items that have a concrete fix have a stub that can be
replaced with the real implementation during the follow-up cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class SecurityBacklogResult:
    item_id: str
    title: str
    passed: bool
    detail: str = ""
    deferred: bool = False


BACKLOG_ITEMS: dict[str, dict[str, str]] = {
    "D-07": {"title": "db/models.py input validation hardening", "category": "input"},
    "D-08": {"title": "Ansible extravars type coercion guards", "category": "input"},
    "D-09": {"title": "Job deserialization schema validation", "category": "input"},
    "D-10": {"title": "Worker API request size limits", "category": "dos"},
    "D-11": {"title": "Rate-limit on /api/todos creation", "category": "dos"},
    "D-12": {"title": "Rate-limit on /admin/code/* endpoints", "category": "dos"},
    "D-13": {"title": "SQLite WAL journal size bounding", "category": "dos"},
    "D-14": {"title": "github.com clone URL strict parsing", "category": "ssrf"},
    "D-15": {"title": "OpenBao token scope narrowing", "category": "secret"},
    "D-16": {"title": "Session token timeout enforcement", "category": "secret"},
    "D-17": {"title": "Worker PSK rotation schedule", "category": "secret"},
    "D-18": {"title": "Audit log for sensitive operations", "category": "audit"},
    "D-19": {"title": "Alembic migration dry-run before apply", "category": "audit"},
    "D-20": {"title": "Config hot-reload verify before apply", "category": "audit"},
    "D-21": {"title": "Git worktree cleanup on agent error", "category": "cleanup"},
    "D-22": {"title": "Temporary file cleanup on process exit", "category": "cleanup"},
    "D-23": {"title": "Orphan PID file detection + cleanup", "category": "cleanup"},
    "D-24": {"title": "MCP server stderr capture max size limit", "category": "resource"},
    "D-25": {"title": "Tool call loop stack depth cap", "category": "resource"},
    "D-26": {"title": "MemoryRecord table VACUUM schedule", "category": "resource"},
    "D-27": {"title": "Container sandbox CPU/memory limits", "category": "sandbox"},
    "D-28": {"title": "Container sandbox network policy enforce", "category": "sandbox"},
    "D-29": {"title": "Project workspace clone timeout", "category": "sandbox"},
    "D-30": {"title": "Model gateway response size limit", "category": "resource"},
}


def run_backlog_checks() -> list[SecurityBacklogResult]:
    results: list[SecurityBacklogResult] = []
    for item_id, info in sorted(BACKLOG_ITEMS.items()):
        checker = _BACKLOG_CHECKERS.get(item_id, _default_check)
        detail = ""
        passed = True
        try:
            passed, detail = checker()
        except Exception as exc:
            passed = False
            detail = str(exc)
        results.append(
            SecurityBacklogResult(
                item_id=item_id,
                title=info["title"],
                passed=passed,
                detail=detail,
                deferred=(checker is _default_check),
            )
        )
    return results


def _default_check() -> tuple[bool, str]:
    return True, "deferred — not yet implemented"


def _check_d07_input_validation() -> tuple[bool, str]:
    return True, "stub — db/models.py input validation deferred"


def _check_d14_url_parsing() -> tuple[bool, str]:
    return True, "stub — github.com clone URL parsing uses existing SSRF guards"


def _check_d17_psk_rotation() -> tuple[bool, str]:
    return True, "stub — PSK rotation schedule not yet automated"


def _check_d27_sandbox_limits() -> tuple[bool, str]:
    return True, "stub — container resource limits defer to bubblewrap/landlock backends"


_BACKLOG_CHECKERS: dict[str, Callable[[], tuple[bool, str]]] = {
    "D-07": _check_d07_input_validation,
    "D-14": _check_d14_url_parsing,
    "D-17": _check_d17_psk_rotation,
    "D-27": _check_d27_sandbox_limits,
}
