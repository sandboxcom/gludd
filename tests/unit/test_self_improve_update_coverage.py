"""Coverage tests for self_improve/gate.py, self_improve/harness.py,
self_update/applier.py, self_update/router.py, and self_improve/dedup.py.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from typing import Any
from unittest.mock import MagicMock

import pytest

from general_ludd.self_improve.dedup import SelfImproveDeduplicator, proposal_signature
from general_ludd.self_improve.gate import SelfImproveGate
from general_ludd.self_improve.harness import SelfImprovementHarness
from general_ludd.self_update.applier import UpdateApplier
from general_ludd.self_update.router import (
    DEFAULT_SUBSYSTEM_MAP,
    UpdateRequestRouter,
)


# ---------------------------------------------------------------------------
# Minimal structural plan for applier tests
# ---------------------------------------------------------------------------

class _Plan:
    def __init__(self, kind: str, capability_required: str, target_paths: list[str]) -> None:
        self.kind = kind
        self.capability_required = capability_required
        self.target_paths = target_paths


# ---------------------------------------------------------------------------
# gate.py — SelfImproveGate
# ---------------------------------------------------------------------------

class TestSelfImproveGateReject:
    def test_gate_reject_at_max(self) -> None:
        gate = SelfImproveGate(max_open=5)
        result = gate.evaluate({}, open_count=5)
        assert result.admitted is False
        assert result.initial_status == ""

    def test_gate_reject_over_max(self) -> None:
        gate = SelfImproveGate(max_open=5)
        result = gate.evaluate({}, open_count=10)
        assert result.admitted is False
        assert result.initial_status == ""


class TestSelfImproveGateAdmit:
    def test_gate_admit_auto_queue(self) -> None:
        gate = SelfImproveGate(max_open=10, auto_queue=True)
        result = gate.evaluate({}, open_count=3)
        assert result.admitted is True
        assert result.initial_status == "queued"

    def test_gate_admit_approval_required(self) -> None:
        gate = SelfImproveGate(max_open=10, auto_queue=False)
        result = gate.evaluate({}, open_count=3)
        assert result.admitted is True
        assert result.initial_status == "approval_required"

    def test_gate_admit_open_count_zero(self) -> None:
        gate = SelfImproveGate()
        result = gate.evaluate({}, open_count=0)
        assert result.admitted is True
        assert result.initial_status == "approval_required"


# ---------------------------------------------------------------------------
# harness.py — SelfImprovementHarness._check_coverage_gaps + generate_fix_todos
# ---------------------------------------------------------------------------

class TestCheckCoverageGaps:
    def _make_coverage_xml(self, tmpdir: str, line_rate: float, filename: str = "src/foo.py") -> str:
        xml = f"""<?xml version="1.0" ?>
<coverage>
    <packages>
        <package name="src">
            <classes>
                <class filename="{filename}" line-rate="{line_rate}">
                </class>
            </classes>
        </package>
    </packages>
</coverage>"""
        path = os.path.join(tmpdir, "coverage.xml")
        with open(path, "w") as f:
            f.write(xml)
        return path

    def test_check_coverage_gaps_low_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_coverage_xml(tmpdir, line_rate=0.50, filename="src/foo.py")
            harness = SelfImprovementHarness(repo_root=tmpdir)
            findings: list[dict[str, Any]] = []
            harness._check_coverage_gaps(findings)
            assert len(findings) == 1
            f = findings[0]
            assert f["type"] == "low_coverage"
            assert f["coverage_pct"] == 50.0
            assert f["file"] == "src/foo.py"

    def test_check_coverage_gaps_malformed_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "coverage.xml")
            with open(path, "w") as fp:
                fp.write("THIS IS NOT XML <<<>>>")
            harness = SelfImprovementHarness(repo_root=tmpdir)
            findings: list[dict[str, Any]] = []
            harness._check_coverage_gaps(findings)  # must not raise
            assert findings == []

    def test_check_coverage_gaps_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._make_coverage_xml(tmpdir, line_rate=0.90)
            harness = SelfImprovementHarness(repo_root=tmpdir)
            findings: list[dict[str, Any]] = []
            harness._check_coverage_gaps(findings)
            assert findings == []

    def test_check_coverage_gaps_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            harness = SelfImprovementHarness(repo_root=tmpdir)
            findings: list[dict[str, Any]] = []
            harness._check_coverage_gaps(findings)
            assert findings == []


class TestGenerateFixTodos:
    def test_generate_fix_todos_low_coverage(self) -> None:
        harness = SelfImprovementHarness()
        finding = {
            "type": "low_coverage",
            "file": "src/general_ludd/foo.py",
            "severity": "medium",
            "coverage_pct": 55.0,
            "message": "foo.py at 55.0% coverage (below 85%)",
        }
        todos = harness.generate_fix_todos([finding])
        assert len(todos) == 1
        t = todos[0]
        assert t["work_type"] == "test"
        assert t["priority"] == "medium"
        assert t["gap_type"] == "low_coverage"
        assert t["source"] == "self_improve_harness"
        assert "foo.py" in t["title"]


# ---------------------------------------------------------------------------
# applier.py — UpdateApplier
# ---------------------------------------------------------------------------

class _AllowChecker:
    def allows(self, _cap: str) -> bool:
        return True


class _DenyChecker:
    def allows(self, _cap: str) -> bool:
        return False


class _RaisingChecker:
    def allows(self, _cap: str) -> bool:
        raise RuntimeError("simulated failure")


class _RecordingWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def write(self, path: str, content: str) -> None:
        self.calls.append((path, content))


class _RaisingWriter:
    def write(self, path: str, content: str) -> None:
        raise OSError("disk full")


class TestApplierCapabilityGate:
    def test_applier_capability_check_raises_denied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_RaisingChecker())
        plan = _Plan("config", "config_write", ["config/ratchet.yml"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "capability check raised" in result.evidence
        assert writer.calls == []

    def test_applier_capability_denied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_DenyChecker())
        plan = _Plan("config", "config_write", ["config/ratchet.yml"])
        result = applier.apply(plan, "key: value")
        assert result.status == "denied"
        assert "capability not allowed" in result.evidence
        assert writer.calls == []

    def test_applier_writer_not_called_when_denied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_DenyChecker())
        plan = _Plan("config", "config_write", ["config/ratchet.yml"])
        applier.apply(plan, "key: value")
        assert writer.calls == []


class TestApplierProtectedPaths:
    def test_applier_protected_path_guardrails(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("config", "config_write", ["src/general_ludd/guardrails/policy.yml"])
        result = applier.apply(plan, "key: val")
        assert result.status == "denied"
        assert "protected path" in result.evidence

    def test_applier_protected_path_case_insensitive(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("config", "config_write", ["src/GUARDRAILS/policy.yml"])
        result = applier.apply(plan, "key: val")
        assert result.status == "denied"

    def test_applier_protected_path_secrets_in_subdir(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("config", "config_write", ["/some/secrets/config.py"])
        result = applier.apply(plan, "key: val")
        assert result.status == "denied"

    def test_applier_protected_path_realpath_dot_claude(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("config", "config_write", ["/Users/user/.claude/settings.json"])
        result = applier.apply(plan, "key: val")
        assert result.status == "denied"


class TestApplierKindBranches:
    def test_applier_code_kind_proposed(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("code", "code_self_modify", ["src/general_ludd/foo.py"])
        result = applier.apply(plan, "# new logic")
        assert result.status == "proposed"
        assert result.evidence == "# new logic"
        assert writer.calls == []

    def test_applier_config_kind_applied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("config", "config_write", ["config/ratchet.yml"])
        result = applier.apply(plan, "key: value\n")
        assert result.status == "applied"
        assert writer.calls == [("config/ratchet.yml", "key: value\n")]

    def test_applier_yaml_kind_applied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("yaml", "config_write", ["config/foo.yml"])
        result = applier.apply(plan, "key: value\n")
        assert result.status == "applied"

    def test_applier_role_kind_applied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("role", "collections_self_modify", ["collections/roles/myrole"])
        result = applier.apply(plan, "key: value\n")
        assert result.status == "applied"

    def test_applier_yaml_invalid_denied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("config", "config_write", ["config/ratchet.yml"])
        result = applier.apply(plan, "key: [unclosed bracket")
        assert result.status == "denied"
        assert "invalid yaml" in result.evidence
        assert writer.calls == []

    def test_applier_writer_raises_denied(self) -> None:
        applier = UpdateApplier(writer=_RaisingWriter(), capability_checker=_AllowChecker())
        plan = _Plan("config", "config_write", ["config/ratchet.yml"])
        result = applier.apply(plan, "key: value\n")
        assert result.status == "denied"
        assert "write failed" in result.evidence

    def test_applier_unknown_kind_denied(self) -> None:
        writer = _RecordingWriter()
        applier = UpdateApplier(writer=writer, capability_checker=_AllowChecker())
        plan = _Plan("unknown_xyz", "config_write", ["config/ratchet.yml"])
        result = applier.apply(plan, "key: val")
        assert result.status == "denied"
        assert "unsupported plan kind" in result.evidence


# ---------------------------------------------------------------------------
# router.py — UpdateRequestRouter
# ---------------------------------------------------------------------------

class TestRouterClassify:
    def test_router_classify_role_preempts_subsystem(self) -> None:
        # "role" keyword must pre-empt all subsystem matches including "budget"
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update gludd: increase budget for the deploy role")
        assert plan.target.subsystem == "role"

    def test_router_classify_no_match_failsafe(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("xyzzy frobble wibble nothing matches")
        assert plan.target.subsystem == "unknown"
        assert plan.risk == "high"
        assert plan.target.paths == []

    def test_router_classify_longest_keyword_wins(self) -> None:
        # "log sources" (11 chars) > "log source" > "connector" — pick connector
        # also: "log sources" is a keyword for connector
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update gludd: adjust log sources connector")
        assert plan.target.subsystem == "connector"


class TestRouterNormalize:
    def test_router_normalize_strips_prefix(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update gludd: budget cap increase")
        assert plan.target.subsystem == "budget"

    def test_router_normalize_strips_dash_prefix(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update gludd - budget cap increase")
        assert plan.target.subsystem == "budget"


class TestRouterResolvePaths:
    def test_router_resolve_paths_code_kind_empty_falls_back_to_config(self) -> None:
        # Subsystem with code_paths=[] but whose default kind would be code
        # (simulate by setting a code-behaviour marker and code_paths=[])
        custom_map = {
            "lint": {
                "kind": "config",
                "keywords": ["lint"],
                "paths": ["config/ratchet.yml"],
                "code_paths": [],  # empty
            }
        }
        # "rewrite how the lint works" -> behaviour marker but no code_paths -> falls back to config
        router = UpdateRequestRouter(subsystem_map=custom_map, path_exists=lambda p: True)
        plan = router.route("rewrite how the lint works")
        # With empty code_paths the kind stays config and config paths are used
        assert plan.target.kind in ("config", "code")
        # At minimum must route to lint subsystem
        assert plan.target.subsystem == "lint"
        # paths must be the config paths (since code_paths is empty)
        assert "config/ratchet.yml" in plan.target.paths


# ---------------------------------------------------------------------------
# dedup.py — SelfImproveDeduplicator + proposal_signature
# ---------------------------------------------------------------------------

class TestProposalSignature:
    def test_proposal_signature_fallback_to_title(self) -> None:
        sig = proposal_signature({"title": "Add tests for foo.py"})
        assert sig == "title::Add tests for foo.py"

    def test_proposal_signature_uses_gap_type_and_source_file(self) -> None:
        sig = proposal_signature({"gap_type": "low_coverage", "source_file": "src/foo.py"})
        assert sig == "low_coverage::src/foo.py"


class TestSelfImproveDeduplicator:
    def test_dedup_is_duplicate(self) -> None:
        dedup = SelfImproveDeduplicator(open_signatures={"low_coverage::src/foo.py"})
        proposal = {"gap_type": "low_coverage", "source_file": "src/foo.py"}
        assert dedup.is_duplicate(proposal) is True

    def test_dedup_not_duplicate(self) -> None:
        dedup = SelfImproveDeduplicator(open_signatures={"low_coverage::src/bar.py"})
        proposal = {"gap_type": "low_coverage", "source_file": "src/foo.py"}
        assert dedup.is_duplicate(proposal) is False

    def test_dedup_filter_new_deduplicates_batch(self) -> None:
        dedup = SelfImproveDeduplicator()
        p1 = {"gap_type": "missing_tests", "source_file": "src/foo.py", "title": "Add tests"}
        p2 = {"gap_type": "missing_tests", "source_file": "src/foo.py", "title": "Add tests"}
        result = dedup.filter_new([p1, p2])
        assert len(result) == 1
        assert result[0] is p1

    def test_dedup_filter_new_removes_already_open(self) -> None:
        open_sigs = {"missing_tests::src/foo.py"}
        dedup = SelfImproveDeduplicator(open_signatures=open_sigs)
        p = {"gap_type": "missing_tests", "source_file": "src/foo.py", "title": "t"}
        result = dedup.filter_new([p])
        assert result == []

    def test_dedup_filter_new_passes_novel(self) -> None:
        dedup = SelfImproveDeduplicator(open_signatures={"missing_tests::src/bar.py"})
        p = {"gap_type": "missing_tests", "source_file": "src/foo.py", "title": "t"}
        result = dedup.filter_new([p])
        assert len(result) == 1
