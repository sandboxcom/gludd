"""Unit tests for ``general_ludd.chemistry.router`` (CHEM-001).

Covers the ChemistryRouter: task-kind → workflow mapping, hazard-review
gating rules (spec §3), risk-tier thresholds, and WorkflowRoute dataclass.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from general_ludd.chemistry.policy import ChemistryPolicy
from general_ludd.chemistry.router import (
    TASK_WORKFLOW,
    ChemistryRouter,
    WorkflowRoute,
)
from general_ludd.chemistry.schemas import (
    ChemistryConstraints,
    ChemistryRequest,
    DataClassification,
    TaskKind,
)


def _make_request(task: TaskKind, request_id: str = "req-1") -> ChemistryRequest:
    return ChemistryRequest(
        request_id=request_id,
        tenant_id="tenant-1",
        task=task,
        entities=["ent-1"],
        constraints=ChemistryConstraints(
            deadline_s=60,
            data_classification=DataClassification.public,
        ),
    )


# ── TASK_WORKFLOW mapping ──────────────────────────────────────────────────


class TestTaskWorkflowMapping:
    def test_all_task_kinds_have_workflow(self):
        for kind in TaskKind:
            assert kind in TASK_WORKFLOW, f"{kind} missing from TASK_WORKFLOW"

    def test_identity_maps_to_identity_resolve(self):
        assert TASK_WORKFLOW[TaskKind.identity] == "identity_resolve"

    def test_research_maps_to_chemistry_research(self):
        assert TASK_WORKFLOW[TaskKind.research] == "chemistry_research"

    def test_property_maps_to_property_lookup(self):
        assert TASK_WORKFLOW[TaskKind.property] == "property_lookup"

    def test_reaction_maps_to_reaction_analyze(self):
        assert TASK_WORKFLOW[TaskKind.reaction] == "reaction_analyze"

    def test_protocol_maps_to_protocol_draft(self):
        assert TASK_WORKFLOW[TaskKind.protocol] == "protocol_draft"

    def test_stoichiometry_maps_to_stoichiometry(self):
        assert TASK_WORKFLOW[TaskKind.stoichiometry] == "stoichiometry"

    def test_hazard_maps_to_hazard_review(self):
        assert TASK_WORKFLOW[TaskKind.hazard] == "hazard_review"

    def test_inventory_maps_to_inventory_check(self):
        assert TASK_WORKFLOW[TaskKind.inventory] == "inventory_check"

    def test_compute_maps_to_quantum_workflow(self):
        assert TASK_WORKFLOW[TaskKind.compute] == "quantum_workflow"

    def test_spectra_maps_to_spectra_analyze(self):
        assert TASK_WORKFLOW[TaskKind.spectra] == "spectra_analyze"

    def test_analytical_maps_to_analytical_validate(self):
        assert TASK_WORKFLOW[TaskKind.analytical] == "analytical_validate"

    def test_electrochemistry_maps_to_electrochemistry(self):
        assert TASK_WORKFLOW[TaskKind.electrochemistry] == "electrochemistry"

    def test_process_maps_to_process_scaleup(self):
        assert TASK_WORKFLOW[TaskKind.process] == "process_scaleup"

    def test_no_duplicate_workflow_names(self):
        names = list(TASK_WORKFLOW.values())
        assert len(names) == len(set(names)), "duplicate workflow names"


# ── WorkflowRoute dataclass ────────────────────────────────────────────────


class TestWorkflowRoute:
    def test_construction_all_fields(self):
        route = WorkflowRoute(
            request_id="req-abc",
            workflow="protocol_draft",
            risk_tier="moderate",
            requires_hazard_review=True,
        )
        assert route.request_id == "req-abc"
        assert route.workflow == "protocol_draft"
        assert route.risk_tier == "moderate"
        assert route.requires_hazard_review is True

    def test_defaults(self):
        route = WorkflowRoute(
            request_id="req-1",
            workflow="",
            risk_tier="low",
            requires_hazard_review=False,
        )
        assert route.request_id == "req-1"
        assert route.workflow == ""
        assert route.risk_tier == "low"
        assert route.requires_hazard_review is False

    def test_equality_by_value(self):
        a = WorkflowRoute(request_id="x", workflow="a", risk_tier="low", requires_hazard_review=False)
        b = WorkflowRoute(request_id="x", workflow="a", risk_tier="low", requires_hazard_review=False)
        assert a == b

    def test_inequality(self):
        a = WorkflowRoute(request_id="x", workflow="a", risk_tier="low", requires_hazard_review=False)
        b = WorkflowRoute(request_id="x", workflow="b", risk_tier="low", requires_hazard_review=False)
        assert a != b


# ── ChemistryRouter ────────────────────────────────────────────────────────


@pytest.fixture
def policy() -> ChemistryPolicy:
    return ChemistryPolicy()


@pytest.fixture
def router(policy: ChemistryPolicy) -> ChemistryRouter:
    return ChemistryRouter(policy)


class TestChemistryRouter:
    def test_route_identity_resolve_workflow(self, router: ChemistryRouter) -> None:
        req = _make_request(TaskKind.identity)
        route = router.route(req)
        assert route.workflow == "identity_resolve"

    def test_route_research_workflow(self, router: ChemistryRouter) -> None:
        req = _make_request(TaskKind.research)
        route = router.route(req)
        assert route.workflow == "chemistry_research"

    def test_route_returns_request_id(self, router: ChemistryRouter) -> None:
        req = _make_request(TaskKind.research, request_id="my-req-42")
        route = router.route(req)
        assert route.request_id == "my-req-42"

    def test_route_risk_tier_from_policy(self, router: ChemistryRouter) -> None:
        req = _make_request(TaskKind.research)
        route = router.route(req)
        assert route.risk_tier in {"low", "moderate", "high", "prohibited"}

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_low_risk_no_hazard_review_on_hazard_gated_workflow(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "low"}
        req = _make_request(TaskKind.protocol)
        route = router.route(req)
        assert route.requires_hazard_review is False
        assert route.risk_tier == "low"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_moderate_risk_triggers_hazard_review_on_hazard_gated(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        req = _make_request(TaskKind.protocol)
        route = router.route(req)
        assert route.requires_hazard_review is True
        assert route.risk_tier == "moderate"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_moderate_risk_no_hazard_review_on_non_hazard_gated(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        req = _make_request(TaskKind.research)
        route = router.route(req)
        assert route.requires_hazard_review is False

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_high_risk_forces_hazard_review_any_workflow(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "high"}
        req = _make_request(TaskKind.research)
        route = router.route(req)
        assert route.requires_hazard_review is True

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_prohibited_risk_forces_hazard_review_any_workflow(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "prohibited"}
        req = _make_request(TaskKind.identity)
        route = router.route(req)
        assert route.requires_hazard_review is True

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_all_hazard_gated_workflows_at_moderate_require_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        hazard_gated = {TaskKind.protocol, TaskKind.compute, TaskKind.process}
        for task in hazard_gated:
            req = _make_request(task)
            route = router.route(req)
            assert route.requires_hazard_review is True, f"{task} missing hazard gate at moderate"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_compute_at_low_does_not_require_hazard_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "low"}
        req = _make_request(TaskKind.compute)
        route = router.route(req)
        assert route.requires_hazard_review is False

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_process_at_low_does_not_require_hazard_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "low"}
        req = _make_request(TaskKind.process)
        route = router.route(req)
        assert route.requires_hazard_review is False

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_stoichiometry_at_moderate_no_hazard_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        req = _make_request(TaskKind.stoichiometry)
        route = router.route(req)
        assert route.requires_hazard_review is False

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_stoichiometry_at_high_requires_hazard_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "high"}
        req = _make_request(TaskKind.stoichiometry)
        route = router.route(req)
        assert route.requires_hazard_review is True

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_spectra_at_moderate_no_hazard_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        req = _make_request(TaskKind.spectra)
        route = router.route(req)
        assert route.requires_hazard_review is False

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_electrochemistry_at_moderate_no_hazard_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        req = _make_request(TaskKind.electrochemistry)
        route = router.route(req)
        assert route.requires_hazard_review is False

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_inventory_hazard_at_moderate_no_hazard_review(self, mock_screen, router: ChemistryRouter) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        for task in (TaskKind.inventory, TaskKind.hazard):
            req = _make_request(task)
            route = router.route(req)
            assert route.requires_hazard_review is False, f"{task} should not require at moderate"


class TestRouterEdgeCases:
    def test_router_stores_policy_reference(self) -> None:
        p = ChemistryPolicy()
        r = ChemistryRouter(p)
        assert r._policy is p

    def test_missing_workflow_mapping_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delitem(TASK_WORKFLOW, TaskKind.identity)
        route = ChemistryRouter(ChemistryPolicy()).route(_make_request(TaskKind.identity))
        assert route == WorkflowRoute(
            request_id="req-1",
            workflow="",
            risk_tier="low",
            requires_hazard_review=False,
        )
