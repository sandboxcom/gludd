"""Unit tests for AG.9: checkpoint branching (A/B execution paths).

Tests BranchManager create/restore/delete/list/compare operations and the
module-level convenience wrappers.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.ag9_checkpoint.branching import (
    BranchManager,
    BranchResult,
    CheckpointBranch,
    create_branch,
    delete_branch,
    list_branches,
    restore_branch,
)


@pytest.fixture
def store_dir():
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def manager(store_dir):
    return BranchManager(store_dir=store_dir)


class TestCheckpointBranch:
    def test_construction_minimal(self):
        b = CheckpointBranch(
            branch_id="b1", name="test", checkpoint_id="ck-1",
        )
        assert b.branch_id == "b1"
        assert b.name == "test"
        assert b.checkpoint_id == "ck-1"
        assert b.state == {}
        assert b.parent_branch is None
        assert b.description == ""

    def test_construction_full(self):
        b = CheckpointBranch(
            branch_id="b2",
            name="alt-strategy",
            checkpoint_id="ck-2",
            state={"key": "val"},
            parent_branch="b1",
            description="try another prompt",
            created_at="2026-07-13T00:00:00Z",
        )
        assert b.state == {"key": "val"}
        assert b.parent_branch == "b1"
        assert b.created_at == "2026-07-13T00:00:00Z"

    def test_created_at_auto_populated(self):
        b = CheckpointBranch(branch_id="b3", name="auto", checkpoint_id="ck-3")
        assert b.created_at != ""

    def test_created_at_not_overwritten_when_provided(self):
        b = CheckpointBranch(
            branch_id="b4", name="x", checkpoint_id="ck-4",
            created_at="2025-01-01T12:00:00Z",
        )
        assert b.created_at == "2025-01-01T12:00:00Z"


class TestBranchResult:
    def test_result_success(self):
        r = BranchResult(branch_id="b1", status="success", output={"x": 1})
        assert r.is_success is True

    def test_result_failure(self):
        r = BranchResult(branch_id="b2", status="failed", error="timeout")
        assert r.is_success is False

    def test_result_defaults(self):
        r = BranchResult(branch_id="b3", status="pending")
        assert r.output == {}
        assert r.error is None
        assert r.duration_ms == 0


class TestBranchManagerCreateDelete:
    def test_create_and_restore_roundtrip(self, manager):
        state = {"prompt": "variant A", "model": "sonnet"}
        branch = manager.create_branch("test-branch", "ck-123", state)
        assert branch.name == "test-branch"
        assert branch.checkpoint_id == "ck-123"

        restored = manager.restore_branch(branch.branch_id)
        assert restored is not None
        assert restored.name == "test-branch"
        assert restored.state == state
        assert restored.branch_id == branch.branch_id

    def test_create_with_description_and_parent(self, manager):
        branch = manager.create_branch(
            "child", "ck-1", {}, parent_branch="parent-id",
            description="forked after review",
        )
        assert branch.parent_branch == "parent-id"
        assert branch.description == "forked after review"

    def test_delete_existing_branch(self, manager):
        branch = manager.create_branch("del-me", "ck-1", {})
        assert manager.delete_branch(branch.branch_id) is True
        assert manager.restore_branch(branch.branch_id) is None

    def test_delete_nonexistent_branch(self, manager):
        assert manager.delete_branch("nonexistent") is False

    def test_restore_nonexistent(self, manager):
        assert manager.restore_branch("nonexistent") is None


class TestBranchManagerList:
    def test_list_empty(self, manager):
        assert manager.list_branches() == []

    def test_list_multiple(self, manager):
        m = manager
        m.create_branch("a", "ck-1", {})
        m.create_branch("b", "ck-2", {})
        m.create_branch("c", "ck-3", {})
        branches = m.list_branches()
        assert len(branches) == 3
        names = {b.name for b in branches}
        assert names == {"a", "b", "c"}

    def test_list_after_delete(self, manager):
        m = manager
        a = m.create_branch("a", "ck-1", {})
        m.create_branch("b", "ck-2", {})
        m.delete_branch(a.branch_id)
        branches = m.list_branches()
        assert len(branches) == 1
        assert branches[0].name == "b"


class TestBranchManagerCompare:
    def test_compare_all_found(self, manager):
        m = manager
        a = m.create_branch("a", "ck-1", {"score": 10})
        b = m.create_branch("b", "ck-2", {"score": 20})
        results = m.compare_branches([a.branch_id, b.branch_id])
        assert len(results) == 2
        assert {r.status for r in results} == {"pending", "diverged"}
        assert {r.branch_id for r in results} == {a.branch_id, b.branch_id}

    def test_compare_handles_missing(self, manager):
        results = manager.compare_branches(["missing-id"])
        assert len(results) == 1
        assert results[0].status == "missing"
        assert results[0].error is not None

    def test_compare_mixed_found_and_missing(self, manager):
        a = manager.create_branch("a", "ck-1", {})
        results = manager.compare_branches([a.branch_id, "missing"])
        assert len(results) == 2
        statuses = {r.status for r in results}
        assert statuses == {"pending", "missing"}


class TestBranchManagerPersistence:
    def test_persists_across_manager_instances(self, store_dir):
        m1 = BranchManager(store_dir=store_dir)
        branch = m1.create_branch("persistent", "ck-99", {"data": 42})

        m2 = BranchManager(store_dir=store_dir)
        restored = m2.restore_branch(branch.branch_id)
        assert restored is not None
        assert restored.name == "persistent"
        assert restored.state == {"data": 42}

    def test_corrupt_json_skipped_in_list(self, store_dir):
        m = BranchManager(store_dir=store_dir)
        m.create_branch("good", "ck-1", {})
        corrupt = Path(store_dir) / "corrupt.json"
        corrupt.write_text("not valid json")
        branches = m.list_branches()
        assert len(branches) == 1
        names = {b.name for b in branches}
        assert "good" in names


class TestModuleConvenienceFunctions:
    def test_create_and_restore_defaults(self, store_dir):
        import general_ludd.ag9_checkpoint.branching as mod
        old = mod._default._dir
        try:
            mod._default._dir = Path(store_dir)
            branch = create_branch("mod-test", "ck-1", {"a": 1})
            restored = restore_branch(branch.branch_id)
            assert restored is not None
            assert restored.name == "mod-test"
        finally:
            mod._default._dir = old

    def test_delete_default(self, store_dir):
        import general_ludd.ag9_checkpoint.branching as mod
        old = mod._default._dir
        try:
            mod._default._dir = Path(store_dir)
            branch = create_branch("mod-delete", "ck-1", {})
            assert delete_branch(branch.branch_id) is True
            assert restore_branch(branch.branch_id) is None
        finally:
            mod._default._dir = old

    def test_list_empty_default(self, store_dir):
        import general_ludd.ag9_checkpoint.branching as mod
        old = mod._default._dir
        try:
            mod._default._dir = Path(store_dir)
            branches = list_branches()
            assert branches == []
        finally:
            mod._default._dir = old


class TestBranchLineage:
    def test_parent_child_chain(self, manager):
        root = manager.create_branch("root", "ck-0", {"generation": 0})
        child_a = manager.create_branch(
            "child-a", "ck-1", {"generation": 1},
            parent_branch=root.branch_id,
        )
        child_b = manager.create_branch(
            "child-b", "ck-1", {"generation": 1},
            parent_branch=root.branch_id,
        )
        assert child_a.parent_branch == root.branch_id
        assert child_b.parent_branch == root.branch_id
        assert {b.name for b in manager.list_branches()} == {"root", "child-a", "child-b"}
