from __future__ import annotations

import pytest

from general_ludd.infra.fix_approval import (
    FixApprovalError,
    FixApprovalManager,
    FixProposal,
)


class TestFixProposal:
    def test_default_construction(self) -> None:
        p = FixProposal(fix_id="f1", deployment={}, patch={})
        assert p.fix_id == "f1"
        assert p.deployment == {}
        assert p.patch == {}
        assert p.status == "pending"
        assert p.reason == ""

    def test_merged_config_overlays_patch(self) -> None:
        p = FixProposal(
            fix_id="f1",
            deployment={"host": "a", "port": 80},
            patch={"host": "b"},
        )
        merged = p.merged_config()
        assert merged == {"host": "b", "port": 80}

    def test_merged_config_empty_patch(self) -> None:
        p = FixProposal(fix_id="f1", deployment={"key": "val"}, patch={})
        assert p.merged_config() == {"key": "val"}

    def test_merged_config_does_not_mutate_original(self) -> None:
        dep = {"a": 1}
        p = FixProposal(fix_id="f1", deployment=dep, patch={"b": 2})
        merged = p.merged_config()
        merged["c"] = 3
        assert "c" not in dep
        assert p.merged_config() == {"a": 1, "b": 2}


class TestFixApprovalManager:
    def test_propose_returns_pending(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "x"}, {"port": 80})
        assert p.status == "pending"
        assert isinstance(p.fix_id, str)
        assert len(p.fix_id) == 32

    def test_propose_stores_deployment_copy(self) -> None:
        dep = {"host": "x"}
        mgr = FixApprovalManager()
        p = mgr.propose(dep, {})
        dep["host"] = "y"
        assert p.deployment == {"host": "x"}

    def test_approve_advances_to_approved(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "a"}, {})
        result = mgr.approve(p.fix_id)
        assert result.status == "approved"
        assert result.merged_config() == {"host": "a"}

    def test_reject_advances_to_rejected_with_reason(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "a"}, {})
        result = mgr.reject(p.fix_id, reason="too risky")
        assert result.status == "rejected"
        assert result.reason == "too risky"

    def test_reject_default_empty_reason(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "a"}, {})
        result = mgr.reject(p.fix_id)
        assert result.status == "rejected"
        assert result.reason == ""

    def test_cannot_approve_already_approved(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "a"}, {})
        mgr.approve(p.fix_id)
        with pytest.raises(FixApprovalError, match="not pending"):
            mgr.approve(p.fix_id)

    def test_cannot_reject_already_approved(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "a"}, {})
        mgr.approve(p.fix_id)
        with pytest.raises(FixApprovalError, match="not pending"):
            mgr.reject(p.fix_id)

    def test_cannot_approve_already_rejected(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "a"}, {})
        mgr.reject(p.fix_id)
        with pytest.raises(FixApprovalError, match="not pending"):
            mgr.approve(p.fix_id)

    def test_cannot_reject_already_rejected(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "a"}, {})
        mgr.reject(p.fix_id)
        with pytest.raises(FixApprovalError, match="not pending"):
            mgr.reject(p.fix_id)

    def test_get_raises_for_unknown_id(self) -> None:
        mgr = FixApprovalManager()
        with pytest.raises(FixApprovalError, match="No fix proposal"):
            mgr.get("nonexistent")

    def test_approve_returns_proposal_with_config(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"name": "app", "replicas": 1}, {"replicas": 3})
        approved = mgr.approve(p.fix_id)
        assert approved.merged_config() == {"name": "app", "replicas": 3}

    def test_multiple_proposals_independent(self) -> None:
        mgr = FixApprovalManager()
        a = mgr.propose({"id": "a"}, {})
        b = mgr.propose({"id": "b"}, {})
        mgr.approve(a.fix_id)
        mgr.reject(b.fix_id, reason="no")
        assert mgr.get(a.fix_id).status == "approved"
        assert mgr.get(b.fix_id).status == "rejected"

    def test_get_returns_proposal(self) -> None:
        mgr = FixApprovalManager()
        p = mgr.propose({"host": "x"}, {})
        fetched = mgr.get(p.fix_id)
        assert fetched.fix_id == p.fix_id
        assert fetched.status == "pending"


class TestFixApprovalError:
    def test_is_exception(self) -> None:
        err = FixApprovalError("message")
        assert isinstance(err, Exception)

    def test_message_preserved(self) -> None:
        msg = "Cannot approve f1: not pending (status=approved)"
        err = FixApprovalError(msg)
        assert str(err) == msg
