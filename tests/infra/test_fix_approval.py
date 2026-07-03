"""Tests for the accept/reject approval manager (SLICE C)."""

from __future__ import annotations

import pytest

from general_ludd.infra.fix_approval import (
    FixApprovalError,
    FixApprovalManager,
    FixProposal,
)

DEPLOYMENT = {"engine": "vllm", "gpu_memory_utilization": 0.99, "max_num_seqs": 8}
PATCH = {"gpu_memory_utilization": 0.94}


def test_propose_mints_pending_proposal() -> None:
    mgr = FixApprovalManager()
    prop = mgr.propose(DEPLOYMENT, PATCH)
    assert isinstance(prop, FixProposal)
    assert prop.status == "pending"
    assert prop.fix_id  # non-empty uuid hex
    assert prop.patch == PATCH
    assert mgr.get(prop.fix_id) is prop


def test_approve_returns_merged_config_and_marks_approved() -> None:
    mgr = FixApprovalManager()
    prop = mgr.propose(DEPLOYMENT, PATCH)
    approved = mgr.approve(prop.fix_id)
    assert approved.status == "approved"
    assert approved.merged_config() == {**DEPLOYMENT, **PATCH}
    # the patch wins on the overlapping key
    assert approved.merged_config()["gpu_memory_utilization"] == 0.94


def test_approve_twice_raises() -> None:
    mgr = FixApprovalManager()
    prop = mgr.propose(DEPLOYMENT, PATCH)
    mgr.approve(prop.fix_id)
    with pytest.raises(FixApprovalError):
        mgr.approve(prop.fix_id)


def test_reject_sets_rejected_and_reason() -> None:
    mgr = FixApprovalManager()
    prop = mgr.propose(DEPLOYMENT, PATCH)
    rejected = mgr.reject(prop.fix_id, reason="too risky")
    assert rejected.status == "rejected"
    assert rejected.reason == "too risky"


def test_approve_after_reject_raises() -> None:
    mgr = FixApprovalManager()
    prop = mgr.propose(DEPLOYMENT, PATCH)
    mgr.reject(prop.fix_id)
    with pytest.raises(FixApprovalError):
        mgr.approve(prop.fix_id)


def test_reject_after_approve_raises() -> None:
    mgr = FixApprovalManager()
    prop = mgr.propose(DEPLOYMENT, PATCH)
    mgr.approve(prop.fix_id)
    with pytest.raises(FixApprovalError):
        mgr.reject(prop.fix_id)


def test_get_unknown_id_raises() -> None:
    mgr = FixApprovalManager()
    with pytest.raises(FixApprovalError):
        mgr.get("does-not-exist")
