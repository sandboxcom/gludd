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

import general_ludd.security.security_backlog as sb
from general_ludd.security.security_backlog import (
    BACKLOG_ITEMS,
    STATUS_LANDED,
    STATUS_OPEN,
    SecurityBacklogResult,
    run_backlog_checks,
)

_EXPLICIT_OPEN_IDS = frozenset(
    {"D-11", "D-12", "D-13", "D-17", "D-19", "D-26", "D-30"}
)
_PROBE_IDS = frozenset(
    {"D-07", "D-10", "D-14", "D-18", "D-25", "D-27", "D-28", "D-29"}
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
                assert r.status == STATUS_OPEN, (
                    f"{r.item_id} is not a probed item but reports {r.status}"
                )
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
        import general_ludd.ag2_lifecycle.hooks as hooks_mod

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
