"""Integration tests: ChemistryRequest -> identity -> safety -> protocol flow.

Exercises the cross-module composition described in
``docs/specs/FEATURE_CHEMISTRY_EXPERT.md`` §15 (CHEM-AT-008 primarily, plus
CHEM-AT-009): identity resolution flows into hazard classification, which
gates protocol drafting. The full pipeline is:

    ChemistryRequest
        -> ChemistryRouter.route (risk classification first, §9)
        -> identity_resolve (resolve structure without erasing distinctions)
        -> screen_hazards / classify_risk (safety tier + required controls)
        -> protocol_draft (versioned digest + approval-gated)
        -> ChemistryResult with safety tier, approvals, and version digest

These tests use real package imports (not file-path loading) because the
integration contract is that the composed modules work together at runtime.
"""

from __future__ import annotations

from general_ludd.chemistry.api import ChemistryExpertAPI
from general_ludd.chemistry.core import resolve_identity, screen_hazards
from general_ludd.chemistry.policy import ChemistryPolicy
from general_ludd.chemistry.protocols import (
    create_protocol_draft,
    issue_approval_token,
    recompute_digest,
    validate_protocol,
)
from general_ludd.chemistry.router import ChemistryRouter
from general_ludd.chemistry.schemas import (
    ChemistryConstraints,
    ChemistryRequest,
    DataClassification,
    ResultStatus,
    RiskTier,
    TaskKind,
)


def _request(
    *,
    task: str = "identity",
    entities: tuple[str, ...] = ("water",),
    request_id: str = "req-int-001",
    approval_token: str | None = None,
    deadline_s: int = 300,
    data_classification: str = "internal",
) -> ChemistryRequest:
    return ChemistryRequest(
        request_id=request_id,
        tenant_id="tenant-int",
        task=TaskKind(task),
        entities=list(entities),
        approval_token=approval_token,
        constraints=ChemistryConstraints(
            deadline_s=deadline_s,
            data_classification=DataClassification(data_classification),
        ),
    )


def _minimal_protocol_payload(entity: str) -> dict:
    """Return a protocol payload satisfying all REQUIRED_SECTIONS."""
    return {
        "objective": f"demonstrate safe handling of {entity}",
        "evidence_refs": [{"source_id": "src-1", "locator": "sds-manual"}],
        "entities": [{"entity_id": entity, "lot": "lot-A"}],
        "quantities": [{"name": "mass", "value": 1.0, "unit": "g"}],
        "equipment": [{"name": "glass-beaker"}],
        "operations": [{"step": 1, "action": "combine"}],
        "stop_conditions": [{"condition": "reaction-complete"}],
        "waste_streams": [{"stream": "aqueous"}],
        "emergency_actions": [{"action": "flush-water"}],
        "approver_roles": ["lab_supervisor"],
    }


# ---------------------------------------------------------------------------
# Identity -> hazard screening composition (CHEM-002 + CHEM-008)
# ---------------------------------------------------------------------------


class TestIdentityResolvesToSafetyTier:
    """Identity resolution composes with hazard screening to set the risk tier."""

    def test_water_resolves_low_risk_with_approvals(self):
        identity = resolve_identity("water")
        assert identity["structure"]["formula"] == "H2O"
        screen = screen_hazards("water")
        assert screen["risk_tier"] == "low"
        assert len(screen["safety"]["approvals"]) >= 1
        assert screen["status"] == "succeeded"

    def test_hydrochloric_acid_resolves_moderate_risk(self):
        identity = resolve_identity("hydrochloric acid")
        assert identity["structure"]["formula"] == "HCl"
        screen = screen_hazards("hydrochloric acid")
        assert screen["risk_tier"] == "moderate"
        assert "corrosive_strong_acid" in screen["hazard_classes"]

    def test_nitroglycerin_resolves_prohibited_and_blocks_actionable_output(self):
        screen = screen_hazards("nitroglycerin")
        assert screen["risk_tier"] == "prohibited"
        assert "explosive" in screen["hazard_classes"]
        # CHEM-AT-008: prohibited tier blocks actionable output.
        assert screen["status"] == "refused"
        assert screen["safety"]["approvals"] == []


# ---------------------------------------------------------------------------
# Router classifies risk before detailed workflow generation (CHEM-001, §9)
# ---------------------------------------------------------------------------


class TestRouterClassifiesBeforeProtocol:
    """The router classifies risk before protocol drafting (spec §9)."""

    def test_protocol_for_water_routes_low_risk(self):
        router = ChemistryRouter(ChemistryPolicy())
        route = router.route(_request(task="protocol", entities=("water",), approval_token="tok-1"))
        assert route.workflow == "protocol_draft"
        assert route.risk_tier == "low"
        assert route.requires_hazard_review is False

    def test_protocol_for_explosive_requires_hazard_review(self):
        router = ChemistryRouter(ChemistryPolicy())
        route = router.route(_request(task="protocol", entities=("tnt",), approval_token="tok-1"))
        assert route.risk_tier == "prohibited"
        assert route.requires_hazard_review is True


# ---------------------------------------------------------------------------
# API §9 safety stops block actionable protocol output (CHEM-AT-008)
# ---------------------------------------------------------------------------


class TestAPISafetyStopsBlockProtocol:
    """The top-level API enforces §9 safety stops before protocol dispatch."""

    def test_ambiguous_identity_blocks_protocol(self):
        api = ChemistryExpertAPI()
        result = api.handle_request(_request(task="identity", entities=("ambiguous:citral",)))
        assert result.status is ResultStatus.refused
        assert any("disambig" in lim.lower() for lim in result.limitations)

    def test_missing_hazard_evidence_refuses_protocol(self):
        api = ChemistryExpertAPI(audit_available=True)
        result = api.handle_request(
            _request(
                task="protocol",
                entities=("totally-unknown-xyz",),
                approval_token="tok-1",
            )
        )
        assert result.status is ResultStatus.refused
        assert any("hazard" in lim.lower() or "evidence" in lim.lower() for lim in result.limitations)

    def test_water_protocol_succeeds_with_safety_record(self):
        api = ChemistryExpertAPI(audit_available=True)
        result = api.handle_request(_request(task="protocol", entities=("water",), approval_token="tok-1"))
        assert result.status is ResultStatus.succeeded
        assert result.safety.risk_tier is RiskTier.low
        assert isinstance(result.run_id, str) and len(result.run_id) >= 8


# ---------------------------------------------------------------------------
# Protocol draft: version digest + approval gate (CHEM-AT-009)
# ---------------------------------------------------------------------------


class TestProtocolDraftDigestAndApproval:
    """A protocol draft carries a version digest and approval gate (§8.1)."""

    def test_protocol_draft_has_sha256_version_digest(self):
        proto = create_protocol_draft(_minimal_protocol_payload("water"))
        assert "version_digest" in proto
        assert len(proto["version_digest"]) == 64

    def test_approval_token_binds_to_digest_and_validates(self):
        proto = create_protocol_draft(_minimal_protocol_payload("ethanol"))
        token = issue_approval_token(proto, approver="dr-house", role="lab_supervisor")
        verdict = validate_protocol(proto, token)
        assert verdict["approved_for_execution"] is True
        assert verdict["status"] == "succeeded"

    def test_protocol_change_invalidates_approval_token(self):
        proto = create_protocol_draft(_minimal_protocol_payload("water"))
        token = issue_approval_token(proto, approver="dr-house", role="lab_supervisor")
        proto["objective"] = "tampered objective"
        recompute_digest(proto)
        verdict = validate_protocol(proto, token)
        assert verdict["approved_for_execution"] is False
        assert any("digest" in e.get("code", "") for e in verdict["errors"])
