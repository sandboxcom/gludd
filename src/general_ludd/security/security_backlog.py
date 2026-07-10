"""Landed-guard regression gate + honest open-items ledger for D-07..D-30.

This module used to be a stub: every one of the 24 ``D-XX`` security-backlog
items (see ``docs/audit/NEW_FINDINGS_2026-06-16.md``) reported ``passed=True``
regardless of whether the corresponding fix actually existed in ``src/`` —
active misinformation about the state of the backlog.

It is now two things:

1. A **static regression probe** for the handful of items that DID land a real
   guard (currently D-14, D-18, and D-27 — see ``_PROBE_ITEM_IDS``). These
   checkers source-scan the relevant modules (no subprocess execution, no
   network I/O) to confirm the guard symbol still exists AND is still wired
   into the code path it protects. If a future refactor silently drops the
   wiring, the probe flips to failing — this is a regression gate, not a
   rubber stamp.
2. An **honest open-items ledger** for every item that has NOT landed. Those
   checkers (the explicit ones for D-07, D-11, D-13, and D-17, plus the
   default checker used by every item with no custom checker) report
   ``status="OPEN"`` and a message describing what is actually missing. OPEN
   is informational — it is not a failure of the module under test, it is a
   backlog item that has not been picked up yet.

``run_backlog_checks()`` returns one :class:`SecurityBacklogResult` per item
with ``status`` in ``{"LANDED-VERIFIED", "OPEN"}``. Run
``python -m general_ludd.security.security_backlog`` for a human-readable
status table; see :func:`_main` for exit-code semantics (a LANDED probe that
starts failing is the only thing that turns the gate red — OPEN items never
do, since they are not a regression, they are a known gap).

NUMBERING COLLISION TRAP: the ``D-07``..``D-30`` IDs here map to
``docs/audit/NEW_FINDINGS_2026-06-16.md`` and are a DISJOINT numbering scheme
from ``TASKS.md`` "Phase D" (which uses ``D-#1``, ``D-#2``, ``D-AB-5``, etc.
for an unrelated, already-closed 2026-07-08 finding tranche). A bare ``D-07``
or ``D-14`` is ambiguous between the two documents — always qualify with the
source doc when cross-referencing.
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass

STATUS_LANDED = "LANDED-VERIFIED"
STATUS_OPEN = "OPEN"


@dataclass
class SecurityBacklogResult:
    item_id: str
    title: str
    passed: bool
    detail: str = ""
    deferred: bool = False
    status: str = ""

    def __post_init__(self) -> None:
        # Derive status from passed when the caller didn't supply one
        # explicitly, so existing call sites that only ever set
        # item_id/title/passed/detail/deferred keep working unchanged.
        if not self.status:
            self.status = STATUS_LANDED if self.passed else STATUS_OPEN


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

# Items with a real static regression probe against landed code. Only these
# can turn the __main__ gate non-zero (see _main) — everything else is an
# honest OPEN item, not a thing under active regression test.
_PROBE_ITEM_IDS: frozenset[str] = frozenset({"D-14", "D-18", "D-27"})


def run_backlog_checks() -> list[SecurityBacklogResult]:
    results: list[SecurityBacklogResult] = []
    for item_id, info in sorted(BACKLOG_ITEMS.items()):
        checker = _BACKLOG_CHECKERS.get(item_id, _default_check)
        detail = ""
        passed = False
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
    return False, "OPEN — not yet implemented"


def _read_module_source(module: object) -> str:
    """Return ``module``'s source text, or ``""`` if it cannot be read.

    Isolated as its own function (rather than inlining ``inspect.getsource``
    at each call site) so tests can monkeypatch it to simulate a guard's
    wiring being silently removed, without needing to actually mutate the
    real source files on disk.
    """
    try:
        return inspect.getsource(module)  # type: ignore[arg-type]
    except (OSError, TypeError):
        return ""


def _check_d07_input_validation() -> tuple[bool, str]:
    return False, (
        "OPEN — db/models.py TaskDecisionModel.todo_updates/child_todos/"
        "validation_requests and AuditEventModel.details have no "
        "CheckConstraint(length(col) <= N); unbounded Text blob DoS remains "
        "reachable (see docs/audit/NEW_FINDINGS_2026-06-16.md db/models.py P2)"
    )


def _check_d11_todo_rate_limit() -> tuple[bool, str]:
    return False, (
        "OPEN — POST /api/todos IS auth-gated (daemon.py _is_public is "
        "method-aware: a mutating method on a public path still requires the "
        "PSK), but no per-request-rate limiter exists on todo creation — an "
        "authenticated caller can still flood-create todos with no cap "
        "(routers/web_search.py has a SlidingWindowRateLimiter; routers/todos.py "
        "has none)"
    )


def _check_d13_wal_journal_bound() -> tuple[bool, str]:
    return False, (
        "OPEN — db/session.py run_wal_pragmas() sets PRAGMA journal_mode=WAL "
        "(and busy_timeout/synchronous/etc.) but no PRAGMA journal_size_limit "
        "exists anywhere in src/ — the WAL file can grow unbounded under "
        "sustained write load"
    )


def _check_d14_url_parsing() -> tuple[bool, str]:
    """Static probe: the clone-path SSRF/RCE guards exist and are wired in.

    Source-scans (no execution — no clone is ever run) two things:
      * ``general_ludd.git_automation.repo`` defines both
        ``_reject_clone_url`` and ``reject_unsafe_repo_url``, and
        ``GitAutomation.clone`` still calls ``_reject_clone_url`` before
        touching the network.
      * ``general_ludd.projects.manager`` (the unauthenticated
        materialize-checkout entrypoint reachable from POST /admin/projects
        and DB restore) still references ``reject_unsafe_repo_url`` on its
        clone path.
    """
    try:
        import general_ludd.git_automation.repo as repo_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.git_automation.repo failed to import: {exc}"

    if not hasattr(repo_mod, "_reject_clone_url"):
        return False, "OPEN — _reject_clone_url no longer defined in git_automation.repo (regression)"
    if not hasattr(repo_mod, "reject_unsafe_repo_url"):
        return False, "OPEN — reject_unsafe_repo_url no longer defined in git_automation.repo (regression)"

    clone_method = getattr(getattr(repo_mod, "GitAutomation", None), "clone", None)
    if clone_method is None:
        return False, "OPEN — GitAutomation.clone no longer exists (regression)"
    clone_src = _read_module_source(clone_method)
    if "_reject_clone_url(" not in clone_src:
        return False, (
            "OPEN — GitAutomation.clone() no longer calls _reject_clone_url "
            "before cloning (regression)"
        )

    try:
        import general_ludd.projects.manager as manager_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.projects.manager failed to import: {exc}"
    manager_src = _read_module_source(manager_mod)
    if "reject_unsafe_repo_url" not in manager_src:
        return False, (
            "OPEN — general_ludd.projects.manager no longer references "
            "reject_unsafe_repo_url on the materialize-checkout clone path "
            "(regression)"
        )

    return True, (
        "LANDED-VERIFIED — GitAutomation.clone() calls _reject_clone_url; "
        "projects.manager materialize-checkout path calls "
        "reject_unsafe_repo_url before any git clone"
    )


def _check_d17_psk_rotation() -> tuple[bool, str]:
    return False, (
        "OPEN — no automated rotation schedule exists for the daemon<->worker "
        "pre-shared key; PSK is a static long-lived secret today"
    )


def _check_d27_sandbox_limits() -> tuple[bool, str]:
    """Static probe: RLIMIT-based resource caps exist and are wired into the runners.

    Source-scans (no subprocess/exec) three things:
      * ``general_ludd.system.rlimit`` defines ``apply_limits``.
      * ``general_ludd.project_runner.runner`` imports it.
      * ``general_ludd.abtest._child`` imports it.

    This is the actual landed guard: real container/cgroup sandboxing (the
    D-27 title) has NOT landed, but the RLIMIT_AS/RLIMIT_CPU best-effort caps
    that stand in for it on both bounded-execution paths have, so we verify
    those specifically rather than claiming the (unimplemented) cgroup story.
    """
    try:
        import general_ludd.system.rlimit as rlimit_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.system.rlimit failed to import: {exc}"
    if not hasattr(rlimit_mod, "apply_limits"):
        return False, "OPEN — general_ludd.system.rlimit.apply_limits no longer defined (regression)"

    try:
        import general_ludd.project_runner.runner as runner_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.project_runner.runner failed to import: {exc}"
    runner_src = _read_module_source(runner_mod)
    if "rlimit import apply_limits" not in runner_src:
        return False, (
            "OPEN — general_ludd.project_runner.runner no longer imports "
            "system.rlimit.apply_limits (regression)"
        )

    try:
        import general_ludd.abtest._child as child_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.abtest._child failed to import: {exc}"
    child_src = _read_module_source(child_mod)
    if "rlimit import apply_limits" not in child_src:
        return False, (
            "OPEN — general_ludd.abtest._child no longer imports "
            "system.rlimit.apply_limits (regression)"
        )

    return True, (
        "LANDED-VERIFIED — system.rlimit.apply_limits exists and is imported "
        "by both project_runner.runner and abtest._child"
    )


def _check_d18_audit_log() -> tuple[bool, str]:
    """Static probe: audit logging for sensitive operations is wired end-to-end.

    Source-scans (no execution) two things:
      * ``general_ludd.db.repository.AuditEventRepository`` defines
        ``record_typed`` (the typed entry point for the ``AuditEventType``
        taxonomy).
      * ``general_ludd.event_loop.loop`` — the central dispatch path
        mutating operations flow through — still calls
        ``self._audit_repo.record_typed(...)``.
    """
    try:
        import general_ludd.db.repository as repo_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.db.repository failed to import: {exc}"

    audit_repo_cls = getattr(repo_mod, "AuditEventRepository", None)
    if audit_repo_cls is None:
        return False, "OPEN — AuditEventRepository no longer defined in db/repository.py (regression)"
    if not hasattr(audit_repo_cls, "record_typed"):
        return False, "OPEN — AuditEventRepository.record_typed no longer defined (regression)"

    try:
        import general_ludd.event_loop.loop as loop_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.event_loop.loop failed to import: {exc}"
    loop_src = _read_module_source(loop_mod)
    if "_audit_repo.record_typed(" not in loop_src:
        return False, (
            "OPEN — event_loop.loop no longer calls _audit_repo.record_typed(...) "
            "(regression — audit logging wiring removed from the dispatch path)"
        )

    return True, (
        "LANDED-VERIFIED — AuditEventRepository.record_typed exists and is "
        "called from event_loop.loop's dispatch path"
    )


_BACKLOG_CHECKERS: dict[str, Callable[[], tuple[bool, str]]] = {
    "D-07": _check_d07_input_validation,
    "D-11": _check_d11_todo_rate_limit,
    "D-13": _check_d13_wal_journal_bound,
    "D-14": _check_d14_url_parsing,
    "D-17": _check_d17_psk_rotation,
    "D-18": _check_d18_audit_log,
    "D-27": _check_d27_sandbox_limits,
}


def _main() -> int:
    """Print a status table for every backlog item; exit non-zero on regression.

    Exit code is non-zero ONLY when a :data:`_PROBE_ITEM_IDS` item (a
    real, previously-landed static probe) reports ``passed=False`` — that
    is a regression in code that was already fixed. Every other item
    reporting ``OPEN`` is informational (a known gap, not a newly-broken
    guarantee) and never fails the gate.
    """
    results = run_backlog_checks()
    regressed: list[str] = []

    print(f"{'ID':6} {'STATUS':16} TITLE")
    print("-" * 88)
    for r in results:
        print(f"{r.item_id:6} {r.status:16} {r.title}")
        if r.detail:
            print(f"       {r.detail}")
        if r.item_id in _PROBE_ITEM_IDS and not r.passed:
            regressed.append(r.item_id)
    print("-" * 88)

    landed = sum(1 for r in results if r.status == STATUS_LANDED)
    open_count = sum(1 for r in results if r.status == STATUS_OPEN)
    print(f"TOTAL={len(results)} LANDED-VERIFIED={landed} OPEN={open_count}")

    if regressed:
        print(f"GATE: FAIL — landed guard probe(s) regressed: {', '.join(sorted(regressed))}")
        return 1
    print("GATE: PASS (OPEN items are informational backlog, not gate failures)")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
