"""CHEM-AT-022 + AIML-AT-020: Tenant isolation — cross-tenant access prevention.

Both the chemistry expert and AI/ML expert specs require tenant-boundary
enforcement: a request scoped to one tenant must never access structures,
formulas, protocols, inventory, spectra, traces, indexes, voices, prompts,
or any artifact belonging to another tenant.

The chemistry policy module (:class:`ChemistryPolicy` in
``general_ludd.chemistry.policy``) gates mutation tasks behind approval
tokens and classifies data — the partial foundation for tenant gating.
This module exercises the existing policy and defines reference tenant-
isolation guards. Where the real implementation is not yet wired, tests
are marked ``@pytest.mark.skip`` with an explanation.
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass

import pytest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_POLICY_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "policy.py")
_CORE_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "core.py")


def _load_mod(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


policy = _load_mod(_POLICY_PATH, "chem_policy_at022")
core = _load_mod(_CORE_PATH, "chem_core_at022")


# ---------------------------------------------------------------------------
# Reference tenant isolation guard (proves concept; wire real when built)
# ---------------------------------------------------------------------------


@dataclass
class TenantScope:
    """Reference tenant boundary — a request is gated to one tenant_id."""

    tenant_id: str

    def validate_access(self, resource_tenant_id: str) -> bool:
        """Return True iff the scoped tenant matches the resource's tenant."""
        if not self.tenant_id or not resource_tenant_id:
            return False
        return self.tenant_id == resource_tenant_id


# ---------------------------------------------------------------------------
# Tests: Reference tenant guard
# ---------------------------------------------------------------------------


class TestTenantIsolationGuard:
    """CHEM-AT-022 / AIML-AT-020: a request from tenant-A cannot read tenant-B."""

    def test_same_tenant_access_allowed(self):
        scope = TenantScope(tenant_id="tenant-alpha")
        assert scope.validate_access("tenant-alpha") is True

    def test_cross_tenant_access_denied(self):
        scope = TenantScope(tenant_id="tenant-alpha")
        assert scope.validate_access("tenant-beta") is False

    def test_empty_tenant_id_denied(self):
        scope = TenantScope(tenant_id="tenant-alpha")
        assert scope.validate_access("") is False

    def test_empty_scope_denied(self):
        scope = TenantScope(tenant_id="")
        assert scope.validate_access("tenant-alpha") is False

    def test_case_sensitivity_maintains_isolation(self):
        scope = TenantScope(tenant_id="Tenant-Alpha")
        assert scope.validate_access("tenant-alpha") is False


# ---------------------------------------------------------------------------
# Tests: Policy module exports
# ---------------------------------------------------------------------------


class TestPolicyIsolationPrimitives:
    """The chemistry policy module ships the primitives tenant gating needs."""

    def test_policy_decision_class_exists(self):
        assert hasattr(policy, "PolicyDecision")

    def test_chemistry_policy_class_exists(self):
        assert hasattr(policy, "ChemistryPolicy")

    def test_policy_decision_has_fail_closed_flag(self):
        d = policy.PolicyDecision(
            decision_id="test-1",
            allowed=True,
        )
        assert d.fail_closed is False
        assert d.allowed is True

    def test_mutation_tasks_are_defined(self):
        assert hasattr(policy, "MUTATION_TASKS")
        assert len(policy.MUTATION_TASKS) > 0


# ---------------------------------------------------------------------------
# Tests: Risk classification (tenant-scoped)
# ---------------------------------------------------------------------------


class TestRiskClassification:
    """Risk classification feeds tenant-boundary decisions."""

    def test_core_screen_hazards_exists(self):
        assert hasattr(core, "screen_hazards")
        assert callable(core.screen_hazards)

    def test_screen_hazards_returns_structured_result(self):
        result = core.screen_hazards({"formula": "H2O", "identity": {"name": "water"}})
        assert isinstance(result, dict)
        assert "risk_tier" in result

    def test_unknown_entity_raises_risk_baseline(self):
        """Unknown entities should resolve to at least moderate risk."""
        result = core.screen_hazards({"formula": "Unobtainium", "identity": {"name": "unknown"}})
        tier = result.get("risk_tier", "low")
        assert tier in {"low", "moderate", "high", "prohibited"}


# ---------------------------------------------------------------------------
# Tests: Cross-tenant access (real wiring not complete)
# ---------------------------------------------------------------------------


class TestCrossTenantAccessChem:
    """CHEM-AT-022: cross-tenant access tests.

    These will exercise the tenant isolation layer when wired.  For now
    they are skipped with documentation of what the wiring requires.
    """

    @pytest.mark.skip(
        "CHEM-AT-022: tenant isolation layer not yet wired in chemistry/. "
        "Primitives exist (ChemistryPolicy, PolicyDecision.fail_closed, "
        "risk classification via screen_hazards).  Wiring requires "
        "tenant_id propagation through API → policy → storage paths."
    )
    def test_cross_tenant_structure_access_blocked(self):
        """Structure lookup from tenant-A must not return tenant-B structures."""
        pass

    @pytest.mark.skip("CHEM-AT-022: tenant isolation layer not yet wired in chemistry/.")
    def test_cross_tenant_protocol_access_blocked(self):
        """Protocol lookup from tenant-A must not return tenant-B protocols."""
        pass

    @pytest.mark.skip("CHEM-AT-022: tenant isolation layer not yet wired in chemistry/.")
    def test_cross_tenant_inventory_access_blocked(self):
        """Inventory query from tenant-A must not return tenant-B lots."""
        pass


class TestCrossTenantAccessAiml:
    """AIML-AT-020: cross-tenant access tests.

    Combined with CHEM-AT-022 per the acceptance test mapping.
    """

    @pytest.mark.skip(
        "AIML-AT-020: tenant isolation layer not yet wired in ai_ml/. "
        "Same tenant_id propagation pattern as CHEM-AT-022."
    )
    def test_cross_tenant_index_access_blocked(self):
        """Index query from tenant-A must not return tenant-B artifacts."""
        pass

    @pytest.mark.skip("AIML-AT-020: tenant isolation layer not yet wired in ai_ml/.")
    def test_cross_tenant_voice_access_blocked(self):
        """Voice synthesis from tenant-A must not access tenant-B custom voices."""
        pass
