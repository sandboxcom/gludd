"""Tests for the D-07..D-30 security backlog landed-guard regression gate.

Replaces the old all-pass stub tests. This module now asserts:
  * static probes (D-07, D-14, D-18, D-27) actually FAIL when the guard they
    verify is removed (proving the gate is a real regression check, not a
    rubber stamp) and PASS against the real, currently-landed code;
  * the explicit OPEN items (D-11, D-13, D-17) honestly report OPEN,
    never a fabricated pass;
  * every item with no custom checker reports OPEN + deferred;
  * ``__main__``'s exit-code semantics: a probe regression is the only thing
    that turns the gate red, OPEN items never do.
"""

from __future__ import annotations

import importlib

import pytest

import general_ludd.security.security_backlog as sb
from general_ludd.security.security_backlog import (
    BACKLOG_ITEMS,
    STATUS_LANDED,
    STATUS_OPEN,
    SecurityBacklogResult,
    run_backlog_checks,
)

_EXPLICIT_OPEN_IDS: frozenset[str] = frozenset()
_PROBE_IDS = frozenset(
    {
        "D-07",
        "D-08",
        "D-09",
        "D-10",
        "D-11",
        "D-12",
        "D-13",
        "D-14",
        "D-15",
        "D-16",
        "D-17",
        "D-18",
        "D-19",
        "D-20",
        "D-21",
        "D-22",
        "D-23",
        "D-24",
        "D-25",
        "D-26",
        "D-27",
        "D-28",
        "D-29",
        "D-30",
    }
)


class TestSecurityBacklogResult:
    def test_fields(self) -> None:
        r = SecurityBacklogResult(
            item_id="D-07",
            title="input validation",
            passed=True,
            detail="done",
            deferred=False,
        )
        assert r.item_id == "D-07"
        assert r.title == "input validation"
        assert r.passed is True
        assert r.detail == "done"
        assert r.deferred is False

    def test_deferred_defaults_false(self) -> None:
        r = SecurityBacklogResult(item_id="D-08", title="test", passed=True)
        assert r.deferred is False
        assert r.detail == ""

    def test_status_derived_landed_when_passed(self) -> None:
        r = SecurityBacklogResult(item_id="D-14", title="t", passed=True)
        assert r.status == STATUS_LANDED

    def test_status_derived_open_when_not_passed(self) -> None:
        r = SecurityBacklogResult(item_id="D-07", title="t", passed=False)
        assert r.status == STATUS_OPEN

    def test_status_explicit_override_respected(self) -> None:
        r = SecurityBacklogResult(item_id="D-07", title="t", passed=False, status="CUSTOM")
        assert r.status == "CUSTOM"


class TestBacklogItems:
    def test_has_correct_count(self) -> None:
        assert len(BACKLOG_ITEMS) == 24

    def test_all_have_title(self) -> None:
        for item_id, info in BACKLOG_ITEMS.items():
            assert "title" in info, f"{item_id} missing title"
            assert info["title"], f"{item_id} empty title"

    def test_all_have_category(self) -> None:
        for item_id, info in BACKLOG_ITEMS.items():
            assert "category" in info, f"{item_id} missing category"

    def test_known_categories(self) -> None:
        valid = {"input", "dos", "ssrf", "secret", "audit", "cleanup", "resource", "sandbox"}
        for item_id, info in BACKLOG_ITEMS.items():
            assert info["category"] in valid, f"{item_id} has unknown category {info['category']!r}"


class TestRunBacklogChecks:
    def test_checker_exception_is_reported_as_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            sb,
            "BACKLOG_ITEMS",
            {"D-X": {"title": "broken probe", "category": "audit"}},
        )
        monkeypatch.setattr(
            sb,
            "_BACKLOG_CHECKERS",
            {"D-X": lambda: (_ for _ in ()).throw(RuntimeError("probe exploded"))},
        )

        result = sb.run_backlog_checks()

        assert len(result) == 1
        assert result[0].passed is False
        assert result[0].detail == "probe exploded"
        assert result[0].status == STATUS_OPEN

    def test_unreadable_object_source_fails_closed(self) -> None:
        assert sb._read_module_source(object()) == ""

    def test_returns_all_items(self) -> None:
        results = run_backlog_checks()
        assert len(results) == len(BACKLOG_ITEMS)
        result_ids = {r.item_id for r in results}
        assert result_ids == set(BACKLOG_ITEMS)

    def test_results_sorted_by_item_id(self) -> None:
        results = run_backlog_checks()
        ids = [r.item_id for r in results]
        assert ids == sorted(ids)

    def test_no_item_falsely_claims_landed_unless_probed(self) -> None:
        """The old bug: every item silently passed. Now only real probes may
        report LANDED-VERIFIED — everything else must be honest OPEN."""
        results = run_backlog_checks()
        for r in results:
            if r.item_id not in _PROBE_IDS:
                assert r.status == STATUS_OPEN, f"{r.item_id} is not a probed item but reports {r.status}"
                assert r.passed is False

    def test_explicit_open_items_report_open(self) -> None:
        results = {r.item_id: r for r in run_backlog_checks()}
        for item_id in _EXPLICIT_OPEN_IDS:
            r = results[item_id]
            assert r.passed is False, f"{item_id} should be OPEN (not landed)"
            assert r.status == STATUS_OPEN
            assert "OPEN" in r.detail
            assert r.deferred is False, f"{item_id} has a custom checker, should not be 'deferred'"

    def test_items_without_custom_checker_are_deferred_and_open(self) -> None:
        results = run_backlog_checks()
        custom = _EXPLICIT_OPEN_IDS | _PROBE_IDS
        for r in results:
            if r.item_id not in custom:
                assert r.deferred is True, f"{r.item_id} should be deferred"
                assert r.status == STATUS_OPEN
                assert r.passed is False

    def test_probe_items_pass_against_real_landed_code(self) -> None:
        """D-14/D-18/D-27 verify guards that are ACTUALLY landed on master —
        against the real, unmocked source tree they must report LANDED-VERIFIED."""
        results = {r.item_id: r for r in run_backlog_checks()}
        for item_id in _PROBE_IDS:
            r = results[item_id]
            assert r.passed is True, f"{item_id} probe failed against real code: {r.detail}"
            assert r.status == STATUS_LANDED
            assert r.deferred is False

    def test_d13_reports_phase_one_landed(self) -> None:
        result = {r.item_id: r for r in run_backlog_checks()}["D-13"]

        assert result.status == STATUS_LANDED
        assert result.passed is True
        assert result.deferred is False
        assert "LANDED-VERIFIED" in result.detail
        assert "query_wal_metrics" in result.detail
        assert "check_disk_pressure" in result.detail
        assert "single maintenance leader" in result.detail


class TestD08ProbeRegressionDetection:
    """Prove D-08 fails if strict extra-vars validation is removed."""

    def test_fails_if_validator_missing(self, monkeypatch) -> None:
        import general_ludd.ansible.unsafe as unsafe_mod

        monkeypatch.delattr(unsafe_mod, "validate_extravars")
        passed, detail = sb._check_d08_ansible_extravars()
        assert passed is False
        assert "validate_extravars" in detail


@pytest.mark.parametrize(
    "item_id",
    [
        "D-07",
        "D-08",
        "D-09",
        "D-10",
        "D-14",
        "D-15",
        "D-17",
        "D-18",
        "D-20",
        "D-22",
        "D-23",
        "D-24",
        "D-25",
        "D-26",
        "D-27",
        "D-28",
        "D-29",
        "D-30",
    ],
)
def test_source_backed_probe_fails_closed_when_source_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    item_id: str,
) -> None:
    monkeypatch.setattr(sb, "_read_module_source", lambda _target: "")

    passed, detail = sb._BACKLOG_CHECKERS[item_id]()

    assert passed is False
    assert detail.startswith("OPEN")


@pytest.mark.parametrize(
    ("item_id", "module_name", "symbol"),
    [
        ("D-13", "general_ludd.security.db_telemetry", "query_wal_metrics"),
        ("D-13", "general_ludd.security.db_telemetry", "check_disk_pressure"),
        ("D-13", "general_ludd.security.db_telemetry", "WalMetrics"),
        ("D-13", "general_ludd.security.db_telemetry", "DiskPressureStatus"),
        ("D-15", "general_ludd.secrets.openbao_scope", "validate_openbao_mount"),
        ("D-16", "general_ludd.security.session_ttl", "SessionManager"),
        ("D-16", "general_ludd.security.session_ttl", "SessionValidation"),
        ("D-16", "general_ludd.security.session_ttl", "SessionRecord"),
        ("D-17", "general_ludd.security.psk_rotation", "PSKIdentity"),
        ("D-17", "general_ludd.security.psk_rotation", "PSKStore"),
        ("D-17", "general_ludd.security.psk_rotation", "PSKRotator"),
        ("D-17", "general_ludd.security.psk_rotation", "PSKRotationState"),
        ("D-17", "general_ludd.security.psk_rotation", "create_psk_rotator"),
        ("D-20", "general_ludd.security.config_compiler", "CompiledConfig"),
        ("D-20", "general_ludd.security.config_compiler", "ConfigCompiler"),
        ("D-20", "general_ludd.security.config_compiler", "ConfigGeneration"),
        ("D-20", "general_ludd.security.config_compiler", "ConfigGenerationState"),
        ("D-20", "general_ludd.security.config_compiler", "compile_config"),
        ("D-21", "general_ludd.git_automation.worktree_lease", "write_worktree_lease"),
        ("D-21", "general_ludd.git_automation.worktree_lease", "check_worktree_lease"),
        ("D-21", "general_ludd.git_automation.worktree_lease", "release_worktree_lease"),
        ("D-21", "general_ludd.git_automation.worktree_lease", "cleanup_expired_leases"),
        ("D-22", "general_ludd.security.temp_cleanup", "TempRoot"),
        ("D-22", "general_ludd.security.temp_cleanup", "TempRootError"),
        ("D-22", "general_ludd.security.temp_cleanup", "cleanup_all_temp_roots"),
        ("D-22", "general_ludd.security.temp_cleanup", "compute_age_seconds"),
        ("D-22", "general_ludd.security.temp_cleanup", "is_temp_root_expired"),
        ("D-23", "general_ludd.security.orphan_pid", "PidRecord"),
        ("D-23", "general_ludd.security.orphan_pid", "PidRecordError"),
        ("D-23", "general_ludd.security.orphan_pid", "compute_boot_id"),
        ("D-23", "general_ludd.security.orphan_pid", "verify_pid_identity"),
        ("D-23", "general_ludd.security.orphan_pid", "reap_orphan_tree"),
        ("D-23", "general_ludd.security.orphan_pid", "is_reaper_safe"),
        ("D-26", "general_ludd.security.vacuum_schedule", "VacuumScheduler"),
        ("D-26", "general_ludd.security.vacuum_schedule", "VacuumResult"),
        ("D-26", "general_ludd.security.vacuum_schedule", "DEFAULT_MIN_INTERVAL_SEC"),
        ("D-30", "general_ludd.models.gateway", "_RequestPayloadBudget"),
        ("D-30", "general_ludd.models.gateway", "DEFAULT_MAX_RESPONSE_BYTES"),
        ("D-30", "general_ludd.models.gateway", "PayloadLimitError"),
    ],
)
def test_symbol_backed_probe_fails_closed_when_contract_disappears(
    monkeypatch: pytest.MonkeyPatch,
    item_id: str,
    module_name: str,
    symbol: str,
) -> None:
    module = importlib.import_module(module_name)
    monkeypatch.delattr(module, symbol)

    passed, detail = sb._BACKLOG_CHECKERS[item_id]()

    assert passed is False
    assert symbol in detail


class TestD14ProbeRegressionDetection:
    """Prove the D-14 probe is a REAL check by making it fail on symbol removal."""

    def test_fails_if_reject_clone_url_missing(self, monkeypatch) -> None:
        import general_ludd.git_automation.repo as repo_mod

        monkeypatch.delattr(repo_mod, "_reject_clone_url")
        passed, detail = sb._check_d14_url_parsing()
        assert passed is False
        assert "_reject_clone_url" in detail

    def test_fails_if_reject_unsafe_repo_url_missing(self, monkeypatch) -> None:
        import general_ludd.git_automation.repo as repo_mod

        monkeypatch.delattr(repo_mod, "reject_unsafe_repo_url")
        passed, detail = sb._check_d14_url_parsing()
        assert passed is False
        assert "reject_unsafe_repo_url" in detail

    def test_fails_if_clone_stops_calling_guard(self, monkeypatch) -> None:
        import general_ludd.git_automation.repo as repo_mod

        def _fake_clone(self, *args, **kwargs):  # pragma: no cover - never called
            raise NotImplementedError

        monkeypatch.setattr(repo_mod.GitAutomation, "clone", _fake_clone)
        passed, detail = sb._check_d14_url_parsing()
        assert passed is False
        assert "no longer calls _reject_clone_url" in detail

    def test_fails_if_manager_stops_referencing_guard(self, monkeypatch) -> None:
        def _fake_source(mod: object) -> str:
            if getattr(mod, "__name__", "") == "general_ludd.projects.manager":
                return ""
            return sb.inspect.getsource(mod)

        monkeypatch.setattr(sb, "_read_module_source", _fake_source)
        passed, detail = sb._check_d14_url_parsing()
        assert passed is False
        assert "projects.manager" in detail


class TestD24ProbeRegressionDetection:
    """Prove the D-24 probe fails if bounded stderr draining is removed."""

    def test_fails_if_stderr_drain_missing(self, monkeypatch) -> None:
        import general_ludd.mcp.transport as transport_mod

        monkeypatch.delattr(transport_mod.MCPStdioClient, "_drain_stderr")
        passed, detail = sb._check_d24_mcp_stderr_limit()
        assert passed is False
        assert "_drain_stderr" in detail


class TestD27ProbeRegressionDetection:
    """Prove the D-27 probe is a REAL check by making it fail on symbol removal."""

    def test_fails_if_apply_limits_missing(self, monkeypatch) -> None:
        import general_ludd.system.rlimit as rlimit_mod

        monkeypatch.delattr(rlimit_mod, "apply_limits")
        passed, detail = sb._check_d27_sandbox_limits()
        assert passed is False
        assert "apply_limits" in detail

    def test_fails_if_runner_stops_importing(self, monkeypatch) -> None:
        import general_ludd.project_runner.runner as runner_mod

        monkeypatch.setattr(
            sb,
            "_read_module_source",
            lambda mod: "" if mod is runner_mod else sb.inspect.getsource(mod),
        )
        passed, detail = sb._check_d27_sandbox_limits()
        assert passed is False
        assert "project_runner.runner" in detail

    def test_fails_if_child_stops_importing(self, monkeypatch) -> None:
        import general_ludd.abtest._child as child_mod

        monkeypatch.setattr(
            sb,
            "_read_module_source",
            lambda mod: "" if mod is child_mod else sb.inspect.getsource(mod),
        )
        passed, detail = sb._check_d27_sandbox_limits()
        assert passed is False
        assert "abtest._child" in detail


class TestD18ProbeRegressionDetection:
    """Prove the D-18 probe is a REAL check by making it fail on symbol removal."""

    def test_fails_if_record_typed_missing(self, monkeypatch) -> None:
        import general_ludd.db.repository as repo_mod

        monkeypatch.delattr(repo_mod.AuditEventRepository, "record_typed")
        passed, detail = sb._check_d18_audit_log()
        assert passed is False
        assert "record_typed" in detail

    def test_fails_if_loop_stops_calling_it(self, monkeypatch) -> None:
        import general_ludd.event_loop.loop as loop_mod

        monkeypatch.setattr(
            sb,
            "_read_module_source",
            lambda mod: "" if mod is loop_mod else sb.inspect.getsource(mod),
        )
        passed, detail = sb._check_d18_audit_log()
        assert passed is False
        assert "event_loop.loop" in detail


class TestD10ProbeRegressionDetection:
    """Prove the D-10 probe (MAX_BODY_BYTES) is a REAL check."""

    def test_fails_if_max_body_bytes_missing(self, monkeypatch) -> None:
        import general_ludd.receiver.router as router_mod

        monkeypatch.delattr(router_mod, "MAX_BODY_BYTES")
        passed, detail = sb._check_d10_body_size_limit()
        assert passed is False
        assert "MAX_BODY_BYTES" in detail

    def test_fails_if_source_removes_reference(self, monkeypatch) -> None:
        import general_ludd.receiver.router as router_mod

        monkeypatch.setattr(
            sb,
            "_read_module_source",
            lambda mod: "" if mod is router_mod else sb.inspect.getsource(mod),
        )
        passed, detail = sb._check_d10_body_size_limit()
        assert passed is False
        assert "MAX_BODY_BYTES" in detail


class TestD25ProbeRegressionDetection:
    """Prove the D-25 probe (recursion_limit + _max_depth) is a REAL check."""

    def test_fails_if_recursion_limit_missing(self, monkeypatch) -> None:
        import general_ludd.execution.langgraph_agent as lg_mod

        monkeypatch.setattr(
            sb,
            "_read_module_source",
            lambda mod: "" if mod is lg_mod else sb.inspect.getsource(mod),
        )
        passed, detail = sb._check_d25_stack_depth_cap()
        assert passed is False
        assert "recursion_limit" in detail

    def test_fails_if_max_depth_missing(self, monkeypatch) -> None:

        def _fake_source(mod: object) -> str:
            if getattr(mod, "__name__", "") == "general_ludd.ag2_lifecycle.hooks":
                return ""
            return sb.inspect.getsource(mod)

        monkeypatch.setattr(sb, "_read_module_source", _fake_source)
        passed, detail = sb._check_d25_stack_depth_cap()
        assert passed is False
        assert "_max_depth" in detail


class TestD28ProbeRegressionDetection:
    """Prove the D-28 probe (NetworkPolicy) is a REAL check."""

    def test_fails_if_network_policy_missing(self, monkeypatch) -> None:
        import general_ludd.ansible.network_policy as np_mod

        monkeypatch.delattr(np_mod, "NetworkPolicy")
        passed, detail = sb._check_d28_network_policy()
        assert passed is False
        assert "NetworkPolicy" in detail

    def test_fails_if_scan_playbook_tasks_missing(self, monkeypatch) -> None:
        import general_ludd.ansible.network_policy as np_mod

        monkeypatch.delattr(np_mod, "scan_playbook_tasks")
        passed, detail = sb._check_d28_network_policy()
        assert passed is False
        assert "scan_playbook_tasks" in detail

    def test_fails_if_core_runner_stops_referencing(self, monkeypatch) -> None:
        import general_ludd.ansible.core_runner as cr_mod

        monkeypatch.setattr(
            sb,
            "_read_module_source",
            lambda mod: "" if mod is cr_mod else sb.inspect.getsource(mod),
        )
        passed, detail = sb._check_d28_network_policy()
        assert passed is False
        assert "network_policy" in detail


class TestD29ProbeRegressionDetection:
    """Prove the D-29 probe (clone timeout) is a REAL check."""

    def test_fails_if_clone_removes_timeout(self, monkeypatch) -> None:
        import general_ludd.git_automation.repo as repo_mod

        def _fake_clone(self, *args, **kwargs):  # pragma: no cover - never called
            raise NotImplementedError

        monkeypatch.setattr(repo_mod.GitAutomation, "clone", _fake_clone)
        passed, detail = sb._check_d29_clone_timeout()
        assert passed is False
        assert "timeout" in detail

    def test_fails_if_clone_missing_entirely(self, monkeypatch) -> None:
        import general_ludd.git_automation.repo as repo_mod

        monkeypatch.delattr(repo_mod.GitAutomation, "clone")
        passed, detail = sb._check_d29_clone_timeout()
        assert passed is False
        assert "no longer exists" in detail


class TestMainExitCodeSemantics:
    def test_main_returns_zero_when_all_probes_pass(self) -> None:
        # Against the real tree, all probes are landed — the gate is green
        # even though many items are honestly OPEN.
        assert sb._main() == 0

    def test_main_returns_nonzero_on_probe_regression(self, monkeypatch, capsys) -> None:
        import general_ludd.system.rlimit as rlimit_mod

        monkeypatch.delattr(rlimit_mod, "apply_limits")
        rc = sb._main()
        assert rc == 1
        out = capsys.readouterr().out
        assert "GATE: FAIL" in out
        assert "D-27" in out

    def test_main_stays_zero_when_only_open_items_present(self, monkeypatch, capsys) -> None:
        rc = sb._main()
        out = capsys.readouterr().out
        assert "OPEN=" in out
        assert rc == 0
