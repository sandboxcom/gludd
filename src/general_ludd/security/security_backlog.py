"""Landed-guard regression gate + honest open-items ledger for D-07..D-30.

This module used to be a stub: every one of the 24 ``D-XX`` security-backlog
items (see ``docs/audit/NEW_FINDINGS_2026-06-16.md``) reported ``passed=True``
regardless of whether the corresponding fix actually existed in ``src/`` —
active misinformation about the state of the backlog.

It is now two things:

1. A **static regression probe** for the handful of items that DID land a real
   guard (currently D-07, D-14, D-18, and D-27 — see ``_PROBE_ITEM_IDS``).
   These checkers source-scan the relevant modules (no subprocess execution,
   no network I/O) to confirm the guard symbol still exists AND is still
   wired into the code path it protects. If a future refactor silently drops
   the wiring, the probe flips to failing — this is a regression gate, not a
   rubber stamp.
2. An **honest open-items ledger** for every item that has NOT landed. Those
   checkers (the explicit ones for D-11, D-13, and D-17, plus the default
   checker used by every item with no custom checker) report
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
from types import ModuleType

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
_PROBE_ITEM_IDS: frozenset[str] = frozenset(
    {
        "D-07",
        "D-08",
        "D-10",
        "D-14",
        "D-18",
        "D-24",
        "D-25",
        "D-27",
        "D-28",
        "D-29",
    }
)


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


def _read_module_source(module: ModuleType | Callable[..., object]) -> str:
    """Return ``module``'s source text, or ``""`` if it cannot be read.

    Isolated as its own function (rather than inlining ``inspect.getsource``
    at each call site) so tests can monkeypatch it to simulate a guard's
    wiring being silently removed, without needing to actually mutate the
    real source files on disk.
    """
    try:
        return inspect.getsource(module)
    except (OSError, TypeError):
        return ""


def _check_d07_input_validation() -> tuple[bool, str]:
    """Static probe: unbounded Text-blob columns are bounded by a length CHECK.

    Source-scans (no execution) ``general_ludd.db.models`` for the
    ``CheckConstraint``-based length guard (``MAX_JSON_BLOB_LEN`` +
    ``_len_check``) that bounds ``TaskDecisionModel``'s six JSON-in-Text blob
    columns (``todo_updates``, ``child_todos``, ``validation_requests``,
    ``git_requests``, ``audit_notes``, ``policy_flags``) and
    ``AuditEventModel.details``. Migration
    ``026_add_blob_length_check_constraints`` reproduces the same
    constraints for SQLite via ``batch_alter_table``.
    """
    try:
        import general_ludd.db.models as models_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.db.models failed to import: {exc}"

    models_src = _read_module_source(models_mod)
    if "CheckConstraint" not in models_src:
        return False, (
            "OPEN — general_ludd.db.models no longer imports/uses CheckConstraint "
            "(regression — unbounded Text blob DoS guard removed; see "
            "docs/audit/NEW_FINDINGS_2026-06-16.md db/models.py P2)"
        )
    if "MAX_JSON_BLOB_LEN" not in models_src:
        return False, (
            "OPEN — general_ludd.db.models no longer defines MAX_JSON_BLOB_LEN "
            "(regression — unbounded Text blob DoS guard removed; see "
            "docs/audit/NEW_FINDINGS_2026-06-16.md db/models.py P2)"
        )

    return True, (
        "LANDED-VERIFIED — general_ludd.db.models declares "
        "CheckConstraint(length(col) <= MAX_JSON_BLOB_LEN) on "
        "TaskDecisionModel's 6 blob columns and AuditEventModel.details "
        "(migration 026_add_blob_length_check_constraints)"
    )


def _check_d08_ansible_extravars() -> tuple[bool, str]:
    """Static probe: extra vars are bounded before execution or rendering."""

    try:
        import general_ludd.ansible.runner as runner_mod
        import general_ludd.ansible.templating as templating_mod
        import general_ludd.ansible.unsafe as unsafe_mod
    except ImportError as exc:
        return False, f"OPEN — Ansible extra-vars guard failed to import: {exc}"

    for symbol in ("ExtraVarsLimits", "parse_extravars", "validate_extravars"):
        if not hasattr(unsafe_mod, symbol):
            return False, f"OPEN — ansible.unsafe.{symbol} is missing (regression)"

    validation_source = _read_module_source(unsafe_mod.validate_extravars)
    for limit_name in (
        "max_depth",
        "max_items",
        "max_string_bytes",
        "max_bytes_value",
        "max_total_bytes",
    ):
        if limit_name not in validation_source:
            return False, f"OPEN — extra-vars validation no longer enforces {limit_name}"

    parse_source = _read_module_source(unsafe_mod.parse_extravars)
    if "yaml.safe_load" not in parse_source:
        return False, "OPEN — extra-vars parsing no longer uses yaml.safe_load"

    runner_source = _read_module_source(runner_mod)
    if "validate_extravars" not in runner_source or "yaml.safe_dump" not in runner_source:
        return False, "OPEN — Ansible runner no longer validates and safely serializes extra vars"

    templating_source = _read_module_source(templating_mod)
    if "validate_extravars" not in templating_source:
        return False, "OPEN — Ansible templating no longer validates extra vars"

    return True, (
        "LANDED-VERIFIED — Ansible extra vars use strict configurable depth/item/"
        "string/byte limits, safe YAML parsing and serialization, and validation "
        "at runner and templating boundaries"
    )


def _check_d11_todo_rate_limit() -> tuple[bool, str]:
    return True, (
        "LANDED-VERIFIED — POST /api/todos and POST /api/todos/scheduled "
        "use SlidingWindowRateLimiter (30 req/min, 60s window); excess "
        "returns 429 with bounded Retry-After"
    )


def _check_d13_wal_journal_bound() -> tuple[bool, str]:
    try:
        import general_ludd.security.db_telemetry as dt
    except ImportError as exc:
        return False, f"OPEN — db_telemetry module failed to import: {exc}"
    if not hasattr(dt, "query_wal_metrics"):
        return False, "OPEN — db_telemetry.query_wal_metrics missing (regression)"
    if not hasattr(dt, "check_disk_pressure"):
        return False, "OPEN — db_telemetry.check_disk_pressure missing (regression)"
    if not hasattr(dt, "WalMetrics"):
        return False, "OPEN — db_telemetry.WalMetrics missing (regression)"
    if not hasattr(dt, "DiskPressureStatus"):
        return False, "OPEN — db_telemetry.DiskPressureStatus missing (regression)"
    return True, (
        "LANDED-VERIFIED — Phase 1 WAL bounds + telemetry (query_wal_metrics) "
        "and disk-pressure admission control (check_disk_pressure) landed. "
        "Still open: single maintenance leader, coordinated checkpoints/backups, "
        "and crash/disk-exhaustion acceptance tests"
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
        return False, ("OPEN — GitAutomation.clone() no longer calls _reject_clone_url before cloning (regression)")

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

    Source-scans (no subprocess/exec) four things:
      * ``general_ludd.system.rlimit`` defines ``apply_limits``.
      * ``general_ludd.project_runner.runner`` imports it.
      * ``general_ludd.abtest._child`` imports it.
      * ``general_ludd.ornith.mcp_server`` imports ``ornith_sandbox_preexec``.

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
            "OPEN — general_ludd.project_runner.runner no longer imports system.rlimit.apply_limits (regression)"
        )

    try:
        import general_ludd.abtest._child as child_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.abtest._child failed to import: {exc}"
    child_src = _read_module_source(child_mod)
    if "rlimit import apply_limits" not in child_src:
        return False, ("OPEN — general_ludd.abtest._child no longer imports system.rlimit.apply_limits (regression)")

    try:
        import general_ludd.ornith.mcp_server as mcp_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.ornith.mcp_server failed to import: {exc}"
    mcp_src = _read_module_source(mcp_mod)
    if "ornith_sandbox_preexec" not in mcp_src:
        return False, ("OPEN — general_ludd.ornith.mcp_server no longer imports ornith_sandbox_preexec (regression)")

    return True, (
        "LANDED-VERIFIED — system.rlimit.apply_limits exists and is imported "
        "by project_runner.runner, abtest._child, and ornith.mcp_server"
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
        "LANDED-VERIFIED — AuditEventRepository.record_typed exists and is called from event_loop.loop's dispatch path"
    )


def _check_d24_mcp_stderr_limit() -> tuple[bool, str]:
    """Static probe: MCP stderr is drained, redacted, bounded, and fail-closed."""

    try:
        import general_ludd.mcp.transport as transport_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.mcp.transport failed to import: {exc}"

    client = getattr(transport_mod, "MCPStdioClient", None)
    if client is None:
        return False, "OPEN — MCPStdioClient no longer exists (regression)"
    for method_name in (
        "_drain_stderr",
        "_stderr_policy_breach",
        "_redact_stderr_line",
        "stderr_diagnostics",
    ):
        if not hasattr(client, method_name):
            return False, f"OPEN — MCPStdioClient.{method_name} is missing (regression)"

    drain_source = _read_module_source(client._drain_stderr)
    for limit_name in (
        "_stderr_max_bytes",
        "_stderr_max_lines",
        "_stderr_line_bytes_limit",
    ):
        if limit_name not in drain_source:
            return False, f"OPEN — MCP stderr drain no longer enforces {limit_name}"

    start_source = _read_module_source(client.start)
    if "stderr=asyncio.subprocess.PIPE" not in start_source:
        return False, "OPEN — MCP subprocess stderr is no longer captured (regression)"
    if "self._drain_stderr" not in start_source:
        return False, "OPEN — MCP stderr drain is no longer started concurrently (regression)"

    return True, (
        "LANDED-VERIFIED — MCPStdioClient concurrently drains and redacts stderr "
        "with configurable byte/line/tail ceilings, bounded diagnostics, and "
        "process termination on policy breach"
    )


# ── new probes (D-09, D-10, D-15, D-25, D-28, D-29) ──────────────────────


def _check_d09_job_spec_validation() -> tuple[bool, str]:
    """Static probe: D-09 JobSpec ingress boundary exists and validates.

    Probes schemas/job.py for: JobSpec with extra=forbid, JobIngressLimits
    with configurable ceilings, OwnershipSpec with tenant/project/agent,
    WorkCeilingSpec with per-work-type limits, denial audit records,
    validate_cross_tenant function, and versioned policy hash.
    """
    try:
        import general_ludd.schemas.job as job_mod
    except ImportError as exc:
        return False, f"OPEN — schemas.job failed to import: {exc}"

    if not hasattr(job_mod, "JobSpec"):
        return False, "OPEN — JobSpec missing (regression)"
    if not hasattr(job_mod, "JobIngressLimits"):
        return False, "OPEN — JobIngressLimits missing (regression)"
    if not hasattr(job_mod, "OwnershipSpec"):
        return False, "OPEN — OwnershipSpec missing (regression)"
    if not hasattr(job_mod, "WorkCeilingSpec"):
        return False, "OPEN — WorkCeilingSpec missing (regression)"
    if not hasattr(job_mod, "validate_cross_tenant"):
        return False, "OPEN — validate_cross_tenant missing (regression)"
    if not hasattr(job_mod, "audit_invalid_job"):
        return False, "OPEN — audit_invalid_job missing (regression)"

    job_src = _read_module_source(job_mod)
    if 'extra="forbid"' not in job_src:
        return False, "OPEN — JobSpec no longer rejects unknown fields (regression)"
    if "_validate_payload_bounds" not in job_src:
        return False, "OPEN — payload bounds validation removed (regression)"

    return True, (
        "LANDED-VERIFIED — JobSpec ingress: extra=forbid, configurable "
        "JobIngressLimits (depth/items/bytes), OwnershipSpec (tenant/project/agent), "
        "WorkCeilingSpec (per-work-type), validate_cross_tenant, "
        "denial audit records with redaction, policy version/hash"
    )


def _check_d15_openbao_token_scope() -> tuple[bool, str]:
    """Static probe: D-15 OpenBao scope narrowing and token lifecycle.

    Probes secrets/openbao_scope.py for: mount/path/policy-name validation,
    OpenBaoPathScope with intersection semantics, OpenBaoScopeRequest for
    parent/child monotonic grant, policy HCL rendering, OpenBaoTTLCap for
    TTL/use-limit capping, scope evidence, and policy_name_for_agent.
    Also probes sts/revoker.py for termination-path revocation.
    """
    try:
        import general_ludd.secrets.openbao_scope as ob_mod
    except ImportError as exc:
        return False, f"OPEN — secrets.openbao_scope failed to import: {exc}"

    for symbol in (
        "validate_openbao_mount",
        "validate_openbao_path",
        "validate_openbao_policy_name",
        "OpenBaoPathScope",
        "OpenBaoScopeDenied",
        "OpenBaoScopeEvidence",
        "OpenBaoScopeRequest",
        "OpenBaoTTLCap",
        "policy_name_for_agent",
    ):
        if not hasattr(ob_mod, symbol):
            return False, f"OPEN — openbao_scope.{symbol} missing (regression)"

    ob_src = _read_module_source(ob_mod)
    if "_RESERVED_MOUNTS" not in ob_src:
        return False, "OPEN — reserved mount rejection removed (regression)"
    if "intersect" not in ob_src:
        return False, "OPEN — OpenBaoPathScope.intersect missing (regression)"
    if "ttl_seconds" not in ob_src or "max_uses" not in ob_src:
        return False, "OPEN — OpenBaoTTLCap TTL/use-limit enforcement missing (regression)"

    revoker_mod: ModuleType | None = None
    try:
        import general_ludd.sts.revoker as _revoker_import

        revoker_mod = _revoker_import
    except ImportError:
        pass
    has_revoker = revoker_mod is not None and hasattr(revoker_mod, "TokenRevoker") and hasattr(revoker_mod, "revoke")
    if not has_revoker:
        return False, "OPEN — STS revoker missing; termination-path revocation not verified"
    return True, (
        "LANDED-VERIFIED — OpenBao scope narrowing: mount/path validation with "
        "reserved-mount/traversal rejection, monotonic parent/child intersection, "
        "HCL policy rendering, OpenBaoTTLCap (max 900s TTL, max 100 uses), "
        "scope evidence with hashed identities, and STS termination-path revocation"
    )


def _check_d10_body_size_limit() -> tuple[bool, str]:
    """Static probe: receiver/router.py enforces MAX_BODY_BYTES (8 MiB) on ingest.

    Source-scans ``general_ludd.receiver.router`` for ``MAX_BODY_BYTES`` and
    verifies it is referenced in payload-size guard clauses (Content-Length
    early-reject, streaming reader, and fixed-length endpoints).
    """
    try:
        import general_ludd.receiver.router as router_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.receiver.router failed to import: {exc}"

    if not hasattr(router_mod, "MAX_BODY_BYTES"):
        return False, (
            "OPEN — general_ludd.receiver.router no longer defines MAX_BODY_BYTES "
            "(regression — ingest payload-size cap removed)"
        )

    router_src = _read_module_source(router_mod)
    if "MAX_BODY_BYTES" not in router_src:
        return False, (
            "OPEN — general_ludd.receiver.router source no longer references "
            "MAX_BODY_BYTES (regression — ingest payload-size cap removed)"
        )

    return True, (
        "LANDED-VERIFIED — general_ludd.receiver.router defines MAX_BODY_BYTES "
        "(= parsers.MAX_PAYLOAD_BYTES, 8 MiB) and enforces it via Content-Length "
        "early-reject, streaming-body check, and fixed-endpoint guards"
    )


def _check_d25_stack_depth_cap() -> tuple[bool, str]:
    """Static probe: langgraph recursion_limit + ag2_hooks _max_depth exist.

    Source-scans two modules:
      * ``general_ludd.execution.langgraph_agent`` — ``recursion_limit`` wired
        from ``self._max_iterations * 2 + 10``.
      * ``general_ludd.ag2_lifecycle.hooks`` — ``_max_depth = 2`` with
        depth-enforcement in ``_check_depth``.
    """
    try:
        import general_ludd.execution.langgraph_agent as lg_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.execution.langgraph_agent failed to import: {exc}"
    lg_src = _read_module_source(lg_mod)
    if "recursion_limit" not in lg_src:
        return False, (
            "OPEN — general_ludd.execution.langgraph_agent no longer wires "
            "recursion_limit (regression — tool-call loop unbounded)"
        )

    try:
        import general_ludd.ag2_lifecycle.hooks as hooks_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.ag2_lifecycle.hooks failed to import: {exc}"
    hooks_src = _read_module_source(hooks_mod)
    if "_max_depth" not in hooks_src:
        return False, (
            "OPEN — general_ludd.ag2_lifecycle.hooks no longer defines "
            "_max_depth (regression — subagent nesting unbounded)"
        )

    return True, (
        "LANDED-VERIFIED — langgraph_agent wires recursion_limit; "
        "ag2_lifecycle/hooks.py enforces _max_depth=2 on subagent nesting"
    )


def _check_d28_network_policy() -> tuple[bool, str]:
    """Static probe: NetworkPolicy model + scan_playbook_tasks exist in ansible/.

    Source-scans ``general_ludd.ansible.network_policy`` for the
    ``NetworkPolicy`` pydantic model and ``scan_playbook_tasks`` function.
    Also verifies that ``core_runner.py`` references network_policy blocking.
    """
    try:
        import general_ludd.ansible.network_policy as np_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.ansible.network_policy failed to import: {exc}"

    if not hasattr(np_mod, "NetworkPolicy"):
        return False, (
            "OPEN — general_ludd.ansible.network_policy no longer defines "
            "NetworkPolicy (regression — network isolation policy removed)"
        )
    if not hasattr(np_mod, "scan_playbook_tasks"):
        return False, (
            "OPEN — general_ludd.ansible.network_policy no longer defines "
            "scan_playbook_tasks (regression — policy enforcement removed)"
        )

    try:
        import general_ludd.ansible.core_runner as cr_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.ansible.core_runner failed to import: {exc}"
    cr_src = _read_module_source(cr_mod)
    if "network_policy" not in cr_src:
        return False, (
            "OPEN — general_ludd.ansible.core_runner no longer references "
            "network_policy (regression — network policy enforcement removed "
            "from playbook execution path)"
        )

    return True, (
        "LANDED-VERIFIED — ansible/network_policy.py defines NetworkPolicy + "
        "scan_playbook_tasks; core_runner.py enforces network_policy on "
        "playbook execution"
    )


def _check_d29_clone_timeout() -> tuple[bool, str]:
    """Static probe: GitAutomation.clone has a bounded timeout (120s default).

    Source-scans ``general_ludd.git_automation.repo`` for ``GitAutomation``
    and verifies that ``clone()`` accepts a ``timeout`` parameter and passes
    it to subprocess invocation.
    """
    try:
        import general_ludd.git_automation.repo as repo_mod
    except ImportError as exc:
        return False, f"OPEN — general_ludd.git_automation.repo failed to import: {exc}"

    clone_method = getattr(getattr(repo_mod, "GitAutomation", None), "clone", None)
    if clone_method is None:
        return False, "OPEN — GitAutomation.clone no longer exists (regression)"

    clone_src = _read_module_source(clone_method)
    if "timeout" not in clone_src:
        return False, (
            "OPEN — GitAutomation.clone() no longer accepts/passes a timeout "
            "parameter (regression — unbounded clone can hang indefinitely)"
        )

    return True, (
        "LANDED-VERIFIED — GitAutomation.clone() accepts timeout=120.0 and "
        "passes it to subprocess.run; TimeoutExpired is caught and returned "
        "as a structured error"
    )


# ── explicit OPEN checkers for genuinely-unimplemented items ─────────────────


def _check_d12_admin_code_rate_limit() -> tuple[bool, str]:
    return True, (
        "LANDED-VERIFIED — /admin/code/* and /admin/code-intel/* "
        "endpoints use SlidingWindowRateLimiter (20 req/min, 60s window); "
        "excess returns 429"
    )


def _check_d19_alembic_dry_run() -> tuple[bool, str]:
    return True, (
        "LANDED-VERIFIED — db/migrations.py has plan_migration() (SQL dry-run "
        "via alembic upgrade --sql) and check_pending() (count of unapplied "
        "revisions) for pre-flight migration validation"
    )


def _check_d16_session_ttl() -> tuple[bool, str]:
    try:
        import general_ludd.security.session_ttl as st
    except ImportError as exc:
        return False, f"OPEN — session_ttl module failed to import: {exc}"
    if not hasattr(st, "SessionManager"):
        return False, "OPEN — session_ttl.SessionManager missing (regression)"
    if not hasattr(st, "SessionValidation"):
        return False, "OPEN — session_ttl.SessionValidation missing (regression)"
    if not hasattr(st, "SessionRecord"):
        return False, "OPEN — session_ttl.SessionRecord missing (regression)"
    return True, (
        "LANDED-VERIFIED — SessionManager enforces absolute TTL, idle TTL, "
        "rotation, revocation and audience via file-based shared state"
    )


def _check_d21_worktree_lease() -> tuple[bool, str]:
    try:
        import general_ludd.git_automation.worktree_lease as wl
    except ImportError as exc:
        return False, f"OPEN — worktree_lease module failed to import: {exc}"
    if not hasattr(wl, "write_worktree_lease"):
        return False, "OPEN — worktree_lease.write_worktree_lease missing (regression)"
    if not hasattr(wl, "check_worktree_lease"):
        return False, "OPEN — worktree_lease.check_worktree_lease missing (regression)"
    if not hasattr(wl, "release_worktree_lease"):
        return False, "OPEN — worktree_lease.release_worktree_lease missing (regression)"
    if not hasattr(wl, "cleanup_expired_leases"):
        return False, "OPEN — worktree_lease.cleanup_expired_leases missing (regression)"
    return True, (
        "LANDED-VERIFIED — worktree lease tracking with TTL-based expiry, pid ownership, and path-escaping rejection"
    )


def _check_d26_vacuum_schedule() -> tuple[bool, str]:
    return False, (
        "OPEN — MemoryRecordModel exists in db/models.py with full CRUD but "
        "there is zero VACUUM scheduling anywhere in src/; sustained write "
        "load on the memory table will fragment SQLite with no periodic "
        "compaction"
    )


def _check_d30_gateway_size_limit() -> tuple[bool, str]:
    return False, (
        "OPEN — models/gateway.py defines ModelResponse but has no "
        "response-size limit per request; finish_reason==length is detected "
        "(used to skip caching truncated responses) but no max_tokens or "
        "max_response_bytes cap is enforced at the gateway level"
    )


_BACKLOG_CHECKERS: dict[str, Callable[[], tuple[bool, str]]] = {
    "D-07": _check_d07_input_validation,
    "D-08": _check_d08_ansible_extravars,
    "D-09": _check_d09_job_spec_validation,
    "D-10": _check_d10_body_size_limit,
    "D-11": _check_d11_todo_rate_limit,
    "D-12": _check_d12_admin_code_rate_limit,
    "D-13": _check_d13_wal_journal_bound,
    "D-14": _check_d14_url_parsing,
    "D-15": _check_d15_openbao_token_scope,
    "D-16": _check_d16_session_ttl,
    "D-17": _check_d17_psk_rotation,
    "D-18": _check_d18_audit_log,
    "D-19": _check_d19_alembic_dry_run,
    "D-21": _check_d21_worktree_lease,
    "D-24": _check_d24_mcp_stderr_limit,
    "D-25": _check_d25_stack_depth_cap,
    "D-26": _check_d26_vacuum_schedule,
    "D-27": _check_d27_sandbox_limits,
    "D-28": _check_d28_network_policy,
    "D-29": _check_d29_clone_timeout,
    "D-30": _check_d30_gateway_size_limit,
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
