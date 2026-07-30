"""Unit tests for the chemistry API surface, router, and policy modules.

Covers the Phase A chemistry expert API (CHEM-001 routing + CHEM-008 policy /
safety gate) from ``docs/specs/FEATURE_CHEMISTRY_EXPERT.md``:

* §4.2 ChemistryRequest — typed input contract.
* §4.3 ChemistryResult — typed output contract.
* §9 Safety table — missing hazard → refused; ambiguous identity → stop and
  request disambiguation; audit unavailable → fail closed for mutation.

Unlike the file-path-loaded ``test_chemistry_core``/``test_chemistry_safety``
modules, these three modules (``api``, ``router``, ``policy``) consume the
typed pydantic schemas directly and are imported via the package path.
"""

from __future__ import annotations

from general_ludd.chemistry.api import ChemistryExpertAPI
from general_ludd.chemistry.policy import ChemistryPolicy
from general_ludd.chemistry.router import ChemistryRouter
from general_ludd.chemistry.schemas import (
    ChemistryConstraints,
    ChemistryRequest,
    DataClassification,
    ResultStatus,
    TaskKind,
)


def _request(
    *,
    task: str = "identity",
    entities: tuple[str, ...] = ("water",),
    request_id: str = "req-test-001",
    approval_token: str | None = None,
    deadline_s: int = 300,
    budget_usd: float = 0.0,
    data_classification: str = "internal",
) -> ChemistryRequest:
    return ChemistryRequest(
        request_id=request_id,
        tenant_id="tenant-test",
        task=TaskKind(task),
        entities=list(entities),
        approval_token=approval_token,
        constraints=ChemistryConstraints(
            deadline_s=deadline_s,
            budget_usd=budget_usd,
            data_classification=DataClassification(data_classification),
        ),
    )


# ---------------------------------------------------------------------------
# Router: routes by task type + risk classification before detailed work
# ---------------------------------------------------------------------------


class TestRouterRoutesByTaskType:
    def test_routes_identity_to_identity_resolve(self):
        router = ChemistryRouter(ChemistryPolicy())
        route = router.route(_request(task="identity"))
        assert route.workflow == "identity_resolve"

    def test_routes_research_to_chemistry_research(self):
        router = ChemistryRouter(ChemistryPolicy())
        route = router.route(_request(task="research"))
        assert route.workflow == "chemistry_research"

    def test_routes_property_to_property_lookup(self):
        router = ChemistryRouter(ChemistryPolicy())
        route = router.route(_request(task="property"))
        assert route.workflow == "property_lookup"

    def test_routes_reaction_to_reaction_analyze(self):
        router = ChemistryRouter(ChemistryPolicy())
        route = router.route(_request(task="reaction"))
        assert route.workflow == "reaction_analyze"

    def test_router_classifies_risk_before_detailed_work(self):
        # Spec §9: "Risk classification occurs before detailed workflow
        # generation." The router must surface the risk tier.
        router = ChemistryRouter(ChemistryPolicy())
        route = router.route(_request(task="reaction", entities=("nitroglycerin",)))
        assert route.risk_tier == "prohibited"
        assert route.requires_hazard_review is True


# ---------------------------------------------------------------------------
# Policy: constraint validation (budget / deadline / data_classification)
# ---------------------------------------------------------------------------


class TestPolicyChecksConstraints:
    def test_valid_constraints_allowed(self):
        policy = ChemistryPolicy()
        decision = policy.check_request(_request(task="identity"))
        assert decision.allowed is True
        assert decision.reason is None

    def test_zero_deadline_refuses_mutation(self):
        policy = ChemistryPolicy()
        decision = policy.check_request(_request(task="protocol", deadline_s=0, approval_token="tok-1"))
        assert decision.allowed is False
        assert "deadline" in (decision.reason or "").lower()

    def test_restricted_data_requires_approval_token(self):
        policy = ChemistryPolicy()
        decision = policy.check_request(_request(task="research", data_classification="restricted"))
        assert decision.allowed is False
        reason = (decision.reason or "").lower()
        assert "approval" in reason or "restricted" in reason or "token" in reason

    def test_restricted_data_allowed_with_token(self):
        policy = ChemistryPolicy()
        decision = policy.check_request(
            _request(
                task="research",
                data_classification="restricted",
                approval_token="tok-classified",
            )
        )
        assert decision.allowed is True

    def test_policy_decision_has_unique_id(self):
        policy = ChemistryPolicy()
        d1 = policy.check_request(_request(request_id="req-a"))
        d2 = policy.check_request(_request(request_id="req-b"))
        assert d1.decision_id != d2.decision_id
        assert len(d1.decision_id) >= 8


# ---------------------------------------------------------------------------
# Policy: audit-unavailable → fail closed for mutation
# ---------------------------------------------------------------------------


class TestPolicyMutationAuditFailClosed:
    def test_audit_unavailable_fails_closed_for_mutation(self):
        policy = ChemistryPolicy()
        decision = policy.check_mutation(
            _request(task="protocol", approval_token="tok-1"),
            audit_available=False,
        )
        assert decision.allowed is False
        assert decision.fail_closed is True
        assert "audit" in (decision.reason or "").lower()

    def test_audit_available_allows_mutation_with_token(self):
        policy = ChemistryPolicy()
        decision = policy.check_mutation(
            _request(task="protocol", approval_token="tok-1"),
            audit_available=True,
        )
        assert decision.allowed is True

    def test_mutation_without_token_refused_regardless_of_audit(self):
        policy = ChemistryPolicy()
        decision = policy.check_mutation(
            _request(task="protocol", approval_token=None),
            audit_available=True,
        )
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# Policy: classify_risk returns low / moderate / high / prohibited
# ---------------------------------------------------------------------------


class TestPolicyClassifyRisk:
    def test_water_is_low_risk(self):
        policy = ChemistryPolicy()
        assert policy.classify_risk(_request(entities=("water",))) == "low"

    def test_nitroglycerin_is_prohibited(self):
        policy = ChemistryPolicy()
        assert policy.classify_risk(_request(entities=("nitroglycerin",))) == "prohibited"

    def test_unknown_entity_is_at_least_moderate(self):
        policy = ChemistryPolicy()
        tier = policy.classify_risk(_request(entities=("totally-unknown-xyz",)))
        assert tier in {"moderate", "high", "prohibited"}


# ---------------------------------------------------------------------------
# API: §9 safety contract — missing hazard, ambiguous identity, fail-closed
# ---------------------------------------------------------------------------


class TestAPIHandleRequestSafetyContract:
    def test_missing_hazard_refuses_protocol(self):
        api = ChemistryExpertAPI(audit_available=True)
        result = api.handle_request(
            _request(task="protocol", entities=("totally-unknown-xyz",), approval_token="tok-1")
        )
        assert result.status is ResultStatus.refused
        assert any("hazard" in lim.lower() or "evidence" in lim.lower() for lim in result.limitations)

    def test_ambiguous_identity_stops_and_requests_disambiguation(self):
        api = ChemistryExpertAPI()
        result = api.handle_request(_request(task="identity", entities=("ambiguous:citral",)))
        assert result.status is ResultStatus.refused
        assert any("disambig" in lim.lower() for lim in result.limitations)

    def test_audit_unavailable_refuses_protocol(self):
        api = ChemistryExpertAPI(audit_available=False)
        result = api.handle_request(_request(task="protocol", entities=("water",), approval_token="tok-1"))
        assert result.status is ResultStatus.refused
        assert any("audit" in e.message.lower() for e in result.errors)

    def test_read_only_research_succeeds_without_audit(self):
        api = ChemistryExpertAPI(audit_available=False)
        result = api.handle_request(_request(task="research"))
        assert result.status in {ResultStatus.succeeded, ResultStatus.degraded}

    def test_handle_request_returns_chemistry_result_with_run_id(self):
        api = ChemistryExpertAPI()
        result = api.handle_request(_request(task="identity"))
        assert result.request_id == "req-test-001"
        assert len(result.run_id) >= 8
