"""Deep capability validation and feature claim tests.

Covers:
- Feature claim verification (FeatureVerifier evidence dispatch, status transitions)
- Capability gating (role-based dispatch, self-modification guards, protected paths)
- Runtime enforcement (RequireCapability FastAPI dependency, sandbox routing)
- Audit assurance (_BUILTIN completeness, check_capability integration, fail-closed invariants)

Spans six source modules:
- general_ludd.security.capability_lattice  (per-role dispatch + self-modification)
- general_ludd.security.capability_guard    (FastAPI RequireCapability dependency)
- general_ludd.security.permissions         (check_capability, PermissionSpec)
- general_ludd.quality.feature_verifier      (evidence-gated verification)
- general_ludd.routers.features              (feature database API)
- general_ludd.sandbox.capability_router     (sandbox backend routing)
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import Request

from general_ludd.quality.feature_verifier import (
    _NODE_META_CHARS,
    _SAFE_NODE_ID,
    FeatureVerifier,
    _validate_node_id,
)
from general_ludd.sandbox.capability_router import SandboxCapabilityRouter
from general_ludd.sandbox.contracts import (
    IsolationLevel,
    SandboxConfig,
    isolation_rank,
)
from general_ludd.security.capability_guard import RequireCapability
from general_ludd.security.capability_lattice import (
    _BUILTIN,
    _KIND_REQUIRES_SELF_MODIFY,
    CapabilityError,
    ProtectedPathError,
    capabilities_for,
    check_dispatch,
    check_self_modification,
    is_collections_path,
    is_protected_path,
    role_may_dispatch,
)
from general_ludd.security.permissions import PermissionSpec, check_capability


def _attempt_frozen_mutation(instance: object, attribute: str, value: object) -> None:
    """Attempt mutation through the public Python attribute protocol."""
    setattr(instance, attribute, value)


# ============================================================================
# 1. Feature Claim Verification — evidence dispatch + status transitions
# ============================================================================


class TestFeatureClaimVerification:
    """Evidence-gated verification: every claim must carry verifiable proof."""

    def test_empty_evidence_never_verified_fail_closed(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-00000001", "name": "no_evidence", "status": "verified", "evidence": []}
        result = v.verify_feature(feature)
        assert result["status"] == "requested"
        er = cast("dict[str, object]", result["evidence_results"])
        assert er["all_met"] is False
        assert result["verified_at"] is None

    def test_none_evidence_forced_to_requested(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-00000002", "name": "null_evidence", "status": "implemented", "evidence": None}
        result = v.verify_feature(feature)
        assert result["status"] == "requested"

    def test_unknown_evidence_prefix_marks_unmet(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-00000003", "name": "bad_prefix", "status": "requested", "evidence": ["bogus:something"]}
        result = v.verify_feature(feature)
        assert result["status"] == "requested"
        er = cast("dict[str, object]", result["evidence_results"])
        per_ref = cast("list[object]", er["per_ref"])
        ref0 = cast("dict[str, object]", per_ref[0])
        assert ref0["met"] is False
        assert "unknown prefix" in str(ref0["detail"])

    def test_node_id_rejection_leading_dash(self) -> None:
        with pytest.raises(ValueError, match="must not start with a dash"):
            _validate_node_id("-option_injection")

    def test_node_id_rejection_shell_metachar(self) -> None:
        for char in (";", "|", "&", "$", "`", ">", "<", "(", ")", " "):
            with pytest.raises(ValueError, match="shell metacharacter"):
                _validate_node_id(f"test_file.py::test_x{char}")

    def test_node_id_rejection_empty_string(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_node_id("")
        assert True  # must have raised

    def test_node_id_rejection_non_safe_pattern(self) -> None:
        with pytest.raises(ValueError, match="not a safe pytest node id"):
            _validate_node_id("@invalid@node@id")
        assert True  # must have raised

    def test_node_id_rejection_non_string(self) -> None:
        with pytest.raises(ValueError, match="non-empty string"):
            _validate_node_id(cast("str", 42))

    def test_check_ref_test_fails_with_unsafe_node_id(self, tmp_path: Path) -> None:
        from general_ludd.quality.feature_verifier import _default_runner

        rc = _default_runner("-inject_option")
        assert rc == 1

    def test_file_evidence_malformed_missing_separator(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        met, detail = v._check_file_symbol("path/to/file.py")  # no ::
        assert met is False
        assert "malformed" in detail

    def test_file_evidence_path_escape_blocked(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        met, detail = v._check_file_symbol("../../../etc/passwd::root")
        assert met is False
        assert "escapes repo root" in detail

    def test_feature_status_requested_unchanged_on_all_fail(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-status-01", "name": "req_fail", "status": "requested", "evidence": ["module:nope"]}
        result = v.verify_feature(feature)
        assert result["status"] == "requested"

    def test_feature_status_verified_degraded_to_regressed(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-status-02", "name": "was_verified", "status": "verified", "evidence": ["module:nope"]}
        result = v.verify_feature(feature)
        assert result["status"] == "regressed"

    def test_feature_status_implemented_degraded_to_regressed(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {
            "id": "FEAT-status-03",
            "name": "was_implemented",
            "status": "implemented",
            "evidence": ["module:nope"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "regressed"

    def test_feature_unknown_status_defaults_to_requested(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {
            "id": "FEAT-status-04",
            "name": "bogus_status",
            "status": "bogus_status",
            "evidence": ["module:nope"],
        }
        result = v.verify_feature(feature)
        assert result["status"] == "requested"

    def test_safe_node_id_accepted(self) -> None:
        valid_ids = [
            "tests/unit/test_foo.py",
            "tests/unit/test_foo.py::test_bar",
            "tests/unit/test_foo.py::TestClass::test_method",
            "tests/unit/test_foo.py::TestClass::test_param[True]",
            "a_B_c/d-e_f/g-h.i_j/k-l_m/n_o.py",
        ]
        for nid in valid_ids:
            assert _validate_node_id(nid) == nid


# ============================================================================
# 2. Capability Gating — role-based dispatch + self-modification guards
# ============================================================================


class TestCapabilityGatingDeep:
    """Every role gets only what its job requires — default-DENY everywhere."""

    def test_none_role_denies_all_dispatch(self) -> None:
        caps = capabilities_for(None)
        assert caps.collections_self_modify is False
        assert caps.dispatch_kinds == frozenset()

    def test_unknown_role_denies_all_dispatch(self) -> None:
        for kind in ("role", "collection", "mcp", "skill"):
            assert role_may_dispatch("fantasy_role", kind) is False

    def test_coder_denied_collection_dispatch(self) -> None:
        assert role_may_dispatch("coder", "collection") is False

    def test_event_loop_denied_collection_dispatch(self) -> None:
        assert role_may_dispatch("event_loop", "collection") is False

    def test_report_status_only_skill_dispatch(self) -> None:
        assert role_may_dispatch("report_status", "skill") is True
        assert role_may_dispatch("report_status", "role") is False
        assert role_may_dispatch("report_status", "mcp") is False
        assert role_may_dispatch("report_status", "collection") is False

    def test_security_auditor_capabilities(self) -> None:
        assert role_may_dispatch("security_auditor", "role") is True
        assert role_may_dispatch("security_auditor", "skill") is True
        assert role_may_dispatch("security_auditor", "mcp") is False
        assert role_may_dispatch("security_auditor", "collection") is False

    def test_self_improve_has_full_dispatch(self) -> None:
        for kind in ("role", "collection", "mcp", "skill"):
            assert role_may_dispatch("self_improve_agent", kind) is True

    def test_self_research_has_full_dispatch(self) -> None:
        for kind in ("role", "collection", "mcp", "skill"):
            assert role_may_dispatch("self_research_agent", kind) is True

    def test_operator_without_self_modify_may_not_dispatch_collection(self) -> None:
        caps = capabilities_for("operator")
        assert "collection" in caps.dispatch_kinds
        assert caps.collections_self_modify is False
        assert role_may_dispatch("operator", "collection") is False

    def test_check_dispatch_raises_capability_error(self) -> None:
        with pytest.raises(CapabilityError, match="lacks the capability"):
            check_dispatch(None, "role")

    def test_check_dispatch_passes_for_valid(self) -> None:
        check_dispatch("coder", "skill")  # must not raise

    def test_capability_error_is_exception(self) -> None:
        assert issubclass(CapabilityError, Exception)

    def test_protected_path_error_is_exception(self) -> None:
        assert issubclass(ProtectedPathError, Exception)


# ============================================================================
# 3. Protected Path + Collections Self-Modification
# ============================================================================


class TestProtectedPathGating:
    """Protected-path deny-list and collections self-modify guard."""

    def test_collections_path_detection_true(self) -> None:
        assert is_collections_path("/repo/collections/ansible_collections/x/y.py") is True
        assert is_collections_path("collections/agent/collection/roles/r/tasks/main.yml") is True

    def test_collections_path_detection_false(self) -> None:
        assert is_collections_path("/repo/src/collections.py") is False  # file, not dir
        assert is_collections_path("src/general_ludd/utils.py") is False

    def test_protected_path_identified(self) -> None:
        assert is_protected_path("src/general_ludd/security/capability_lattice.py") is True

    def test_ordinary_path_not_protected(self) -> None:
        assert is_protected_path("src/general_ludd/utils/helpers.py") is False

    def test_check_self_modification_raises_on_protected_first(self) -> None:
        with pytest.raises(ProtectedPathError):
            check_self_modification("src/general_ludd/security/permissions.py", "self_improve_agent")

    def test_check_self_modification_allows_non_collections(self) -> None:
        check_self_modification("/tmp/some_script.py", "coder")  # must not raise

    def test_collections_denied_for_coder(self) -> None:
        with pytest.raises(CapabilityError, match="may not self-modify"):
            check_self_modification("collections/agent/foo.py", "coder")

    def test_collections_allowed_for_self_improve(self) -> None:
        check_self_modification("collections/agent/foo.py", "self_improve_agent")


# ============================================================================
# 4. Runtime Enforcement — RequireCapability FastAPI dependency
# ============================================================================


class TestCapabilityGuardRuntimeEnforcement:
    """RequireCapability FastAPI dependency enforces resource:action at runtime."""

    def test_require_capability_stores_resource_and_action(self) -> None:
        guard = RequireCapability(resource="admin:account", action="delete")
        assert guard._resource == "admin:account"
        assert guard._action == "delete"

    @pytest.mark.anyio
    async def test_no_auth_spec_raises_403(self) -> None:
        from fastapi import HTTPException

        class _FakeRequest:
            state: object

            def __init__(self) -> None:
                self.state = type("State", (), {"auth_spec": None})()

            @property
            def method(self) -> str:
                return "GET"

            @property
            def url(self) -> object:
                return type("URL", (), {"path": "/api/test"})()

        guard = RequireCapability(resource="admin:account", action="delete")
        fake = _FakeRequest()
        with pytest.raises(HTTPException) as exc_info:
            await guard(cast("Request", fake))
        assert exc_info.value.status_code == 403
        detail = cast("dict[str, object]", exc_info.value.detail)
        assert detail["error"] == "forbidden: no_auth_spec"

    @pytest.mark.anyio
    async def test_insufficient_capability_raises_403(self) -> None:
        from fastapi import HTTPException

        class _FakeRequest:
            def __init__(self) -> None:
                spec = PermissionSpec(agent_type="viewer")
                self.state = type("State", (), {"auth_spec": spec})()

            @property
            def method(self) -> str:
                return "POST"

            @property
            def url(self) -> object:
                return type("URL", (), {"path": "/api/admin/delete"})()

        guard = RequireCapability(resource="admin:account", action="delete")
        fake = _FakeRequest()
        with pytest.raises(HTTPException) as exc_info:
            await guard(cast("Request", fake))
        assert exc_info.value.status_code == 403
        detail2 = cast("dict[str, object]", exc_info.value.detail)
        assert detail2["error"] == "forbidden: insufficient_capability"


# ============================================================================
# 5. Sandbox Capability Routing — backend resolution + fallback
# ============================================================================


class TestSandboxCapabilityRouting:
    """SandboxCapabilityRouter resolves backends correctly per isolation level."""

    def test_explicit_process_backend(self) -> None:
        config = SandboxConfig(backend="process", isolation=IsolationLevel.PROCESS)
        router = SandboxCapabilityRouter(config)
        assert router.backend_name == "process"

    def test_explicit_container_backend(self) -> None:
        config = SandboxConfig(backend="container", isolation=IsolationLevel.CONTAINER)
        router = SandboxCapabilityRouter(config)
        assert type(router.backend).__name__ == "ContainerBackend"
        assert router.backend_name in {"podman", "docker"}

    def test_unknown_backend_falls_back_to_process(self) -> None:
        config = SandboxConfig(backend="nonexistent_backend_xyz", isolation=IsolationLevel.PROCESS)
        router = SandboxCapabilityRouter(config)
        assert router.backend_name == "process"

    def test_isolation_rank_ordering(self) -> None:
        assert isolation_rank(IsolationLevel.PROCESS) < isolation_rank(IsolationLevel.CONTAINER)
        assert isolation_rank(IsolationLevel.CONTAINER) < isolation_rank(IsolationLevel.VM_HARDWARE)

    def test_backend_lazy_initialization(self) -> None:
        config = SandboxConfig(backend="process", isolation=IsolationLevel.PROCESS)
        router = SandboxCapabilityRouter(config)
        assert router._backend is None  # not resolved until accessed
        _ = router.backend  # triggers resolution
        assert router._backend is not None


# ============================================================================
# 6. Audit Assurance — _BUILTIN completeness + invariants
# ============================================================================


class TestBuiltinAuditAssurance:
    """Every role in _BUILTIN must have a valid-capable, correctly-scoped shape."""

    KNOWN_ROLES = frozenset(
        {
            "self_improve_agent",
            "self_research_agent",
            "coder",
            "operator",
            "report_status",
            "security_auditor",
            "event_loop",
        }
    )

    def test_all_known_roles_present(self) -> None:
        for role in self.KNOWN_ROLES:
            assert role in _BUILTIN, f"missing builtin: {role}"

    def test_every_role_has_frozenset_dispatch_kinds(self) -> None:
        for caps in _BUILTIN.values():
            assert isinstance(caps.dispatch_kinds, frozenset)

    def test_only_self_prefixed_have_collections_self_modify(self) -> None:
        for name, caps in _BUILTIN.items():
            if name.startswith("self_"):
                assert caps.collections_self_modify is True, name
            else:
                assert caps.collections_self_modify is False, name

    def test_no_role_dispatches_kind_they_dont_have(self) -> None:
        for role, caps in _BUILTIN.items():
            for kind in ("role", "collection", "mcp", "skill"):
                may = role_may_dispatch(role, kind)
                if may:
                    assert kind in caps.dispatch_kinds, f"{role} dispatches {kind} but lacks it in caps"

    def test_dispatch_kinds_are_valid_kind_strings(self) -> None:
        all_kinds: set[str] = set()
        for caps in _BUILTIN.values():
            all_kinds.update(caps.dispatch_kinds)
        for kind in all_kinds:
            assert kind in {"role", "collection", "mcp", "skill"}, f"unknown kind {kind}"

    def test_kind_requires_self_modify_collection_only(self) -> None:
        assert "collection" in _KIND_REQUIRES_SELF_MODIFY
        assert "role" not in _KIND_REQUIRES_SELF_MODIFY
        assert "mcp" not in _KIND_REQUIRES_SELF_MODIFY
        assert "skill" not in _KIND_REQUIRES_SELF_MODIFY

    def test_role_capabilities_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        caps = capabilities_for("coder")
        with pytest.raises(FrozenInstanceError):
            _attempt_frozen_mutation(caps, "collections_self_modify", True)

    def test_check_capability_integration_deny(self) -> None:
        from general_ludd.security.permissions import PermissionSpec

        spec = PermissionSpec(agent_type="viewer", capabilities=[])
        assert check_capability(spec, "admin:account", "delete") is False


# ============================================================================
# 7. Audit — verify_all consistency + FeatureVerifier invariants
# ============================================================================


class TestVerifyAllAudit:
    """verify_all must produce consistent, complete summaries."""

    def test_verify_all_zero_features(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        summary = v.verify_all([])
        assert summary["total"] == 0
        assert summary["verified_count"] == 0

    def test_verify_all_counts_match_results(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        features = [
            {"id": "FEAT-a1", "name": "pass", "status": "requested", "evidence": []},
            {"id": "FEAT-a2", "name": "empty2", "status": "requested", "evidence": []},
            {"id": "FEAT-a3", "name": "empty3", "status": "requested", "evidence": None},
        ]
        summary = v.verify_all(features)
        assert summary["total"] == 3
        assert summary["requested_count"] == 3
        results_list = cast("list[object]", summary["results"])
        assert len(results_list) == 3

    def test_verify_all_injects_id_and_name(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        features = [{"id": "FEAT-zz", "name": "my_feat", "status": "requested", "evidence": []}]
        summary = v.verify_all(features)
        rlist = cast("list[object]", summary["results"])
        r = cast("dict[str, object]", rlist[0])
        assert r["id"] == "FEAT-zz"
        assert r["name"] == "my_feat"

    def test_verify_all_verified_count_correct(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        (tmp_path / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
        (tmp_path / "plugins" / "modules" / "gludd_facts.py").write_text("# module")
        features = [
            {"id": "FEAT-b1", "name": "has_evidence", "status": "requested", "evidence": ["module:gludd_facts"]},
            {"id": "FEAT-b2", "name": "no_evidence", "status": "requested", "evidence": []},
        ]
        summary = v.verify_all(features)
        assert summary["verified_count"] == 1
        assert summary["requested_count"] == 1
        assert summary["implemented_count"] == 0
        assert summary["regressed_count"] == 0

    def test_node_id_metachar_set_is_complete(self) -> None:
        expected = set(";|&$`><(){}!*?~#'\" \t\n\r\\")
        assert expected == _NODE_META_CHARS

    def test_safe_node_id_regex_rejects_empty(self) -> None:
        assert _SAFE_NODE_ID.match("") is None


# ============================================================================
# 8. FeatureVerifier _build_result shape invariants
# ============================================================================


class TestBuildResultShape:
    """_build_result always returns the agreed-upon dict shape."""

    def test_result_dict_has_all_required_keys(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-shape", "name": "test", "status": "requested", "evidence": []}
        result = v.verify_feature(feature)
        assert "status" in result
        assert "verified_at" in result
        assert "evidence_results" in result
        er2 = cast("dict[str, object]", result["evidence_results"])
        assert "all_met" in er2
        assert "met_count" in er2
        assert "total_count" in er2
        assert "per_ref" in er2

    def test_verified_feature_has_iso_verified_at(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        (tmp_path / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
        (tmp_path / "plugins" / "modules" / "mod_x.py").write_text("# mod")
        feature = {"id": "FEAT-iso", "name": "test", "status": "requested", "evidence": ["module:mod_x"]}
        result = v.verify_feature(feature)
        assert result["status"] == "verified"
        assert result["verified_at"] is not None
        datetime.datetime.fromisoformat(cast("str", result["verified_at"]))  # must not raise

    def test_non_verified_feature_has_none_verified_at(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-no-iso", "name": "test", "status": "requested", "evidence": []}
        result = v.verify_feature(feature)
        assert result["verified_at"] is None

    def test_per_ref_detail_is_always_string(self, tmp_path: Path) -> None:
        v = _make_minimal_verifier(tmp_path)
        feature = {"id": "FEAT-str", "name": "test", "status": "requested", "evidence": ["module:nope"]}
        result = v.verify_feature(feature)
        er3 = cast("dict[str, object]", result["evidence_results"])
        for entry in cast("list[object]", er3["per_ref"]):
            e = cast("dict[str, object]", entry)
            assert isinstance(e["detail"], str)


# ============================================================================
# Helpers
# ============================================================================


def _fake_runner_pass(_node_id: str) -> int:
    return 0


def _make_minimal_verifier(tmp_path: Path) -> FeatureVerifier:
    (tmp_path / "plugins" / "modules").mkdir(parents=True, exist_ok=True)
    return FeatureVerifier(repo_root=str(tmp_path), runner=_fake_runner_pass)
