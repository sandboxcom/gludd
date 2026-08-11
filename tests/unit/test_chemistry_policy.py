"""Unit tests for ``general_ludd.chemistry.policy``.

Covers CHEM policy surface: request constraint validation, mutation gating,
audit-service fail-closed, and risk-tier classification.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from unittest.mock import patch

import pytest

from general_ludd.chemistry.schemas import (
    ChemistryConstraints,
    ChemistryRequest,
    DataClassification,
    TaskKind,
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
_POLICY_PATH = os.path.join(_PROJECT_ROOT, "src", "general_ludd", "chemistry", "policy.py")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, f"{name} spec failed"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


policy_mod = _load(_POLICY_PATH, "chem_policy_under_test")
ChemistryPolicy = policy_mod.ChemistryPolicy
PolicyDecision = policy_mod.PolicyDecision
MUTATION_TASKS = policy_mod.MUTATION_TASKS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    approval_token: str | None = None,
    task: TaskKind = TaskKind.research,
    deadline_s: int = 60,
    data_classification: DataClassification = DataClassification.public,
    entities: list[str] | None = None,
) -> ChemistryRequest:
    return ChemistryRequest(
        request_id="req-1",
        tenant_id="t-1",
        task=task,
        entities=entities or ["ent-1"],
        constraints=ChemistryConstraints(
            deadline_s=deadline_s,
            data_classification=data_classification,
        ),
        approval_token=approval_token,
    )


@pytest.fixture
def policy() -> ChemistryPolicy:
    return ChemistryPolicy()


# ---------------------------------------------------------------------------
# check_request
# ---------------------------------------------------------------------------


class TestCheckRequest:
    def test_allows_public_research_without_token(self, policy: ChemistryPolicy) -> None:
        req = _make_request()
        dec = policy.check_request(req)
        assert dec.allowed is True
        assert dec.decision_id.startswith("pol-")

    def test_denies_mutation_without_approval_token(self, policy: ChemistryPolicy) -> None:
        for task in MUTATION_TASKS:
            req = _make_request(task=task, approval_token="")
            dec = policy.check_request(req)
            assert dec.allowed is False, f"{task} should be denied without token"
            assert dec.reason is not None

    def test_allows_mutation_with_approval_token(self, policy: ChemistryPolicy) -> None:
        for task in MUTATION_TASKS:
            req = _make_request(task=task, approval_token="ok-token")
            dec = policy.check_request(req)
            assert dec.allowed is True, f"{task} should be allowed with token"

    def test_denies_zero_deadline_mutation(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.protocol, deadline_s=0, approval_token="tok")
        dec = policy.check_request(req)
        assert dec.allowed is False
        assert dec.reason is not None

    def test_allows_positive_deadline_mutation(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.compute, deadline_s=300, approval_token="tok")
        dec = policy.check_request(req)
        assert dec.allowed is True

    def test_denies_restricted_data_without_token_even_for_research(self, policy: ChemistryPolicy) -> None:
        req = _make_request(
            task=TaskKind.research,
            data_classification=DataClassification.restricted,
            approval_token=None,
        )
        dec = policy.check_request(req)
        assert dec.allowed is False
        assert dec.reason is not None

    def test_allows_restricted_data_with_token(self, policy: ChemistryPolicy) -> None:
        req = _make_request(
            task=TaskKind.research,
            data_classification=DataClassification.restricted,
            approval_token="approved",
        )
        dec = policy.check_request(req)
        assert dec.allowed is True

    def test_decision_id_is_unique(self, policy: ChemistryPolicy) -> None:
        ids: set[str] = set()
        for _ in range(50):
            dec = policy.check_request(_make_request())
            ids.add(dec.decision_id)
        assert len(ids) == 50

    def test_allowed_decision_has_none_reason(self, policy: ChemistryPolicy) -> None:
        req = _make_request()
        dec = policy.check_request(req)
        assert dec.allowed is True
        assert dec.reason is None

    def test_denied_decision_has_reason(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.protocol, approval_token="")
        dec = policy.check_request(req)
        assert dec.allowed is False
        assert dec.reason is not None and len(dec.reason) > 0

    def test_confidential_data_without_token_allowed(self, policy: ChemistryPolicy) -> None:
        """Not restricted → token not required for non-mutation."""
        req = _make_request(
            task=TaskKind.research,
            data_classification=DataClassification.confidential,
            approval_token=None,
        )
        dec = policy.check_request(req)
        assert dec.allowed is True

    def test_internal_data_allowed_without_token(self, policy: ChemistryPolicy) -> None:
        req = _make_request(
            task=TaskKind.research,
            data_classification=DataClassification.internal,
            approval_token=None,
        )
        dec = policy.check_request(req)
        assert dec.allowed is True


# ---------------------------------------------------------------------------
# check_mutation
# ---------------------------------------------------------------------------


class TestCheckMutation:
    def test_allows_non_mutation_without_audit(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.research)
        dec = policy.check_mutation(req, audit_available=False)
        assert dec.allowed is True

    def test_allows_non_mutation_with_audit(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.research)
        dec = policy.check_mutation(req, audit_available=True)
        assert dec.allowed is True

    def test_fail_closed_when_audit_unavailable_for_mutation(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.protocol, approval_token="tok")
        dec = policy.check_mutation(req, audit_available=False)
        assert dec.allowed is False
        assert dec.fail_closed is True
        assert dec.reason is not None

    def test_denies_mutation_without_token_when_audit_available(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.process, approval_token="")
        dec = policy.check_mutation(req, audit_available=True)
        assert dec.allowed is False

    def test_allows_mutation_with_token_and_audit_available(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.compute, approval_token="tok")
        dec = policy.check_mutation(req, audit_available=True)
        assert dec.allowed is True

    def test_non_mutation_fail_closed_is_false(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.research)
        dec = policy.check_mutation(req, audit_available=True)
        assert dec.fail_closed is False

    def test_every_mutation_task_fail_closed_without_audit(self, policy: ChemistryPolicy) -> None:
        for task in MUTATION_TASKS:
            req = _make_request(task=task, approval_token="tok")
            dec = policy.check_mutation(req, audit_available=False)
            assert dec.allowed is False, f"{task} not fail-closed"
            assert dec.fail_closed is True

    def test_reason_populated_when_mutation_blocked(self, policy: ChemistryPolicy) -> None:
        req = _make_request(task=TaskKind.protocol, approval_token="tok")
        dec = policy.check_mutation(req, audit_available=False)
        assert dec.reason is not None
        assert "fail-closed" in dec.reason.lower() or "unavailable" in dec.reason.lower()


# ---------------------------------------------------------------------------
# classify_risk
# ---------------------------------------------------------------------------


class TestClassifyRisk:
    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_no_entities_returns_low(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.return_value = {"risk_tier": "low"}
        req = _make_request()
        tier = policy.classify_risk(req)
        assert tier == "low"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_single_low_hazard_returns_low(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.return_value = {"risk_tier": "low"}
        req = _make_request()
        tier = policy.classify_risk(req)
        assert tier == "low"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_single_moderate_hazard_returns_moderate(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.return_value = {"risk_tier": "moderate"}
        req = _make_request()
        tier = policy.classify_risk(req)
        assert tier == "moderate"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_high_hazard_returns_high(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.return_value = {"risk_tier": "high"}
        req = _make_request()
        tier = policy.classify_risk(req)
        assert tier == "high"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_prohibited_hazard_returns_prohibited(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.return_value = {"risk_tier": "prohibited"}
        req = _make_request()
        tier = policy.classify_risk(req)
        assert tier == "prohibited"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_worst_case_across_multiple_entities(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.side_effect = [
            {"risk_tier": "low"},
            {"risk_tier": "moderate"},
            {"risk_tier": "high"},
        ]
        req = _make_request(entities=["ent-a", "ent-b", "ent-c"])
        tier = policy.classify_risk(req)
        assert tier == "high"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_unknown_tier_defaults_to_moderate(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.return_value = {"risk_tier": "unknown_bogus"}
        req = _make_request()
        tier = policy.classify_risk(req)
        assert tier == "moderate"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_low_and_prohibited_returns_prohibited(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.side_effect = [
            {"risk_tier": "low"},
            {"risk_tier": "prohibited"},
        ]
        req = _make_request(entities=["ent-a", "ent-b"])
        tier = policy.classify_risk(req)
        assert tier == "prohibited"

    @patch("general_ludd.chemistry.core.screen_hazards")
    def test_all_same_tier_returns_that_tier(self, mock_screen, policy: ChemistryPolicy) -> None:
        mock_screen.side_effect = [
            {"risk_tier": "low"},
            {"risk_tier": "low"},
            {"risk_tier": "low"},
        ]
        req = _make_request(entities=["a", "b", "c"])
        tier = policy.classify_risk(req)
        assert tier == "low"


# ---------------------------------------------------------------------------
# PolicyDecision dataclass
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def test_defaults(self) -> None:
        pd = PolicyDecision(decision_id="abc", allowed=True)
        assert pd.decision_id == "abc"
        assert pd.allowed is True
        assert pd.reason is None
        assert pd.risk_tier == "low"
        assert pd.fail_closed is False

    def test_with_reason_and_fail_closed(self) -> None:
        pd = PolicyDecision(
            decision_id="def",
            allowed=False,
            reason="blocked",
            risk_tier="high",
            fail_closed=True,
        )
        assert pd.allowed is False
        assert pd.reason == "blocked"
        assert pd.risk_tier == "high"
        assert pd.fail_closed is True

    def test_equality_by_value(self) -> None:
        a = PolicyDecision(decision_id="x", allowed=True)
        b = PolicyDecision(decision_id="x", allowed=True)
        assert a == b

    def test_inequality(self) -> None:
        a = PolicyDecision(decision_id="x", allowed=True)
        b = PolicyDecision(decision_id="x", allowed=False)
        assert a != b


# ---------------------------------------------------------------------------
# MUTATION_TASKS constant
# ---------------------------------------------------------------------------


class TestMutationTasks:
    def test_contains_expected_task_kinds(self) -> None:
        assert TaskKind.protocol in MUTATION_TASKS
        assert TaskKind.compute in MUTATION_TASKS
        assert TaskKind.process in MUTATION_TASKS

    def test_research_is_not_mutation(self) -> None:
        assert TaskKind.research not in MUTATION_TASKS

    def test_is_frozenset(self) -> None:
        assert isinstance(MUTATION_TASKS, frozenset)

    def test_identity_is_not_mutation(self) -> None:
        assert TaskKind.identity not in MUTATION_TASKS
