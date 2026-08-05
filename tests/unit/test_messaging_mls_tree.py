"""Tests for MLS RatchetTree — left-balanced binary tree (RFC 9420 §5)."""

from __future__ import annotations

import pytest

from general_ludd.messaging.mls_tree import (
    LeafNode,
    MLSTreeError,
    ParentNode,
    RatchetTree,
    _is_leaf,
    _left,
    _level,
    _num_nodes,
    _parent,
    _right,
)

# ── tree sizing ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, 0),
        (1, 1),
        (2, 3),
        (3, 5),
        (4, 7),
        (5, 9),
        (8, 15),
        (16, 31),
        (32, 63),
    ],
)
def test_num_nodes(n: int, expected: int) -> None:
    assert _num_nodes(n) == expected


def test_empty_tree_size_zero() -> None:
    t = RatchetTree()
    assert t.size == 0
    assert t.leaf_count == 0
    assert t.leaf_nodes == []


def test_single_leaf_tree_size() -> None:
    t = RatchetTree()
    t.add_leaf(LeafNode(0, b"\x01" * 32))
    assert t.size == 1
    assert t.leaf_count == 1
    assert t.leaf_nodes == [0]


def test_two_leaf_tree_size() -> None:
    t = RatchetTree()
    t.add_leaf(LeafNode(0, b"\x01" * 32))
    t.add_leaf(LeafNode(1, b"\x02" * 32))
    assert t.size == 3
    assert t.leaf_count == 2
    assert t.leaf_nodes == [1, 2]


def test_three_leaf_tree_size() -> None:
    t = RatchetTree()
    for i in range(3):
        t.add_leaf(LeafNode(i, bytes([i + 1]) * 32))
    assert t.size == 5
    assert t.leaf_count == 3
    assert t.leaf_nodes == [2, 3, 4]


def test_five_leaf_tree_size() -> None:
    t = RatchetTree()
    for i in range(5):
        t.add_leaf(LeafNode(i, bytes([i + 1]) * 32))
    assert t.size == 9
    assert t.leaf_count == 5
    assert t.leaf_nodes == [4, 5, 6, 7, 8]


def test_eight_leaf_tree_size() -> None:
    t = RatchetTree()
    for i in range(8):
        t.add_leaf(LeafNode(i, bytes([i + 1]) * 32))
    assert t.size == 15
    assert t.leaf_count == 8


# ── heap-index helpers ─────────────────────────────────────────────────────


def test_parent_of_root_raises() -> None:
    with pytest.raises(MLSTreeError, match="root has no parent"):
        _parent(0)


def test_parent_of_child() -> None:
    assert _parent(1) == 0
    assert _parent(2) == 0
    assert _parent(3) == 1
    assert _parent(4) == 1
    assert _parent(5) == 2
    assert _parent(6) == 2
    assert _parent(7) == 3


def test_left_child() -> None:
    assert _left(0) == 1
    assert _left(1) == 3
    assert _left(2) == 5
    assert _left(3) == 7


def test_right_child() -> None:
    assert _right(0) == 2
    assert _right(1) == 4
    assert _right(2) == 6
    assert _right(3) == 8


def test_sibling() -> None:
    assert RatchetTree.sibling(1) == 2
    assert RatchetTree.sibling(2) == 1
    assert RatchetTree.sibling(3) == 4
    assert RatchetTree.sibling(4) == 3
    assert RatchetTree.sibling(5) == 6
    assert RatchetTree.sibling(6) == 5


def test_level() -> None:
    assert _level(0) == 0
    assert _level(1) == 1
    assert _level(2) == 1
    assert _level(3) == 2
    assert _level(6) == 2
    assert _level(7) == 3


def test_is_leaf() -> None:
    assert not _is_leaf(0, 7)  # N=4: internal node
    assert _is_leaf(3, 7)  # N=4: first leaf
    assert _is_leaf(6, 7)  # N=4: last leaf
    assert not _is_leaf(0, 9)  # N=5: internal node
    assert _is_leaf(4, 9)  # N=5: first leaf
    assert _is_leaf(8, 9)  # N=5: last leaf
    assert _is_leaf(0, 1)  # N=1: root IS a leaf
    assert not _is_leaf(0, 0)  # empty tree


# ── direct_path / copath ───────────────────────────────────────────────────


def test_direct_path_single_leaf() -> None:
    assert RatchetTree.direct_path(0) == []


def test_direct_path_two_leaves() -> None:
    assert RatchetTree.direct_path(1) == [0]
    assert RatchetTree.direct_path(2) == [0]


def test_direct_path_three_leaves() -> None:
    assert RatchetTree.direct_path(2) == [0]
    assert RatchetTree.direct_path(3) == [1, 0]
    assert RatchetTree.direct_path(4) == [1, 0]


def test_direct_path_four_leaves() -> None:
    assert RatchetTree.direct_path(3) == [1, 0]
    assert RatchetTree.direct_path(4) == [1, 0]
    assert RatchetTree.direct_path(5) == [2, 0]
    assert RatchetTree.direct_path(6) == [2, 0]


def test_direct_path_five_leaves() -> None:
    # N=5 leaves → nodes 0..8, leaves at 4,5,6,7,8
    # parent(4)=1, parent(1)=0 → [1,0]
    # parent(5)=2, parent(2)=0 → [2,0]
    # parent(6)=2 → [2,0]
    # parent(7)=3, parent(3)=1, parent(1)=0 → [3,1,0]
    assert RatchetTree.direct_path(4) == [1, 0]
    assert RatchetTree.direct_path(5) == [2, 0]
    assert RatchetTree.direct_path(6) == [2, 0]
    assert RatchetTree.direct_path(7) == [3, 1, 0]
    assert RatchetTree.direct_path(8) == [3, 1, 0]


# copath excludes root sibling (root has no parent → no sibling)


def test_copath_two_leaves() -> None:
    # d(1)=[0], exclude root → copath=[]
    # d(2)=[0], exclude root → copath=[]
    assert RatchetTree.copath(1) == []
    assert RatchetTree.copath(2) == []


def test_copath_three_leaves() -> None:
    # d(3)=[1,0], exclude root → sibling(1)=2 → [2]
    # d(4)=[1,0], exclude root → sibling(1)=2 → [2]
    # d(2)=[0], exclude root → []
    assert RatchetTree.copath(3) == [2]
    assert RatchetTree.copath(4) == [2]
    assert RatchetTree.copath(2) == []


def test_copath_four_leaves() -> None:
    # d(3)=[1,0] → sibling(1)=2 → [2]
    # d(6)=[2,0] → sibling(2)=1 → [1]
    assert RatchetTree.copath(3) == [2]
    assert RatchetTree.copath(6) == [1]


def test_copath_five_leaves() -> None:
    # d(8)=[3,1,0], exclude root → sibling(3)=4, sibling(1)=2 → [4, 2]
    assert RatchetTree.copath(8) == [4, 2]


# ── resolution ─────────────────────────────────────────────────────────────


def _tree_with_leaves(n: int) -> RatchetTree:
    t = RatchetTree()
    for i in range(n):
        t.add_leaf(LeafNode(i, bytes([i + 1]) * 32))
    return t


def test_resolution_empty_tree() -> None:
    t = RatchetTree()
    assert t.resolution(0) == []


def test_resolution_single_leaf() -> None:
    t = _tree_with_leaves(1)
    assert t.resolution(0) == [0]


def test_resolution_two_leaves_root_blank() -> None:
    t = _tree_with_leaves(2)
    t.blank_node(0)
    assert t.resolution(0) == [1, 2]


def test_resolution_parent_with_one_blank_child() -> None:
    t = _tree_with_leaves(4)
    # nodes: [None,None,None, L0,L1,L2,L3] (leaves at 3,4,5,6)
    t.blank_node(0)
    t.blank_node(2)
    t.blank_node(5)
    t.blank_node(6)
    # resolution(0): blank → recurse: res(1)=[3,4], res(2)=[] → [3,4]
    assert t.resolution(0) == [3, 4]


def test_resolution_all_populated() -> None:
    t = _tree_with_leaves(4)
    assert t.resolution(0) == [3, 4, 5, 6]


def test_resolution_deep_blank() -> None:
    t = _tree_with_leaves(4)
    for i in range(t.size):
        t.blank_node(i)
    t.set_parent_hash(2, b"\xcc" * 32)
    t.blank_node(0)
    t.blank_node(1)
    assert t.resolution(0) == [2]


# ── filtered_direct_path ───────────────────────────────────────────────────


def test_filtered_direct_path_all_populated() -> None:
    t = _tree_with_leaves(4)
    t.set_parent_hash(1, b"\xaa" * 32)
    t.set_parent_hash(2, b"\xbb" * 32)
    assert t.filtered_direct_path(3) == [1, 0]


def test_filtered_direct_path_with_blanks() -> None:
    t = _tree_with_leaves(4)
    t.blank_node(1)
    t.set_parent_hash(2, b"\xcc" * 32)
    # d(3)=[1,0]; res(1)=[3,4] (children are leaf nodes) → include 1
    # res(0)=[3,4,2] (via children 1 and 2) → include 0
    assert t.filtered_direct_path(3) == [1, 0]


# ── add_leaf ────────────────────────────────────────────────────────────────


def test_add_leaf_returns_leaf_index() -> None:
    t = RatchetTree()
    idx = t.add_leaf(LeafNode(0, b"\x01" * 32))
    assert idx == 0
    idx2 = t.add_leaf(LeafNode(1, b"\x02" * 32))
    assert idx2 == 1


def test_add_leaf_sets_leaf_index_on_object() -> None:
    t = RatchetTree()
    leaf0 = LeafNode(0, b"\x0a" * 32)
    leaf1 = LeafNode(1, b"\x0b" * 32)
    t.add_leaf(leaf0)
    t.add_leaf(leaf1)
    assert leaf0.index == 0
    assert leaf1.index == 1


def test_add_leaf_grows_tree_correctly() -> None:
    t = RatchetTree()
    for i in range(8):
        t.add_leaf(LeafNode(i, bytes([i]) * 32))
    assert t.size == 15
    assert t.leaf_count == 8
    for i in range(8):
        leaf = t.leaf_at(i)
        assert leaf.index == i


def test_add_leaf_internal_nodes_start_blank() -> None:
    t = RatchetTree()
    t.add_leaf(LeafNode(0, b"\x01" * 32))
    t.add_leaf(LeafNode(1, b"\x02" * 32))
    assert t.size == 3
    assert t.nodes[0] is None


# ── leaf_at ─────────────────────────────────────────────────────────────────


def test_leaf_at_returns_correct_node() -> None:
    t = RatchetTree()
    pk0 = b"\x10" * 32
    pk1 = b"\x20" * 32
    t.add_leaf(LeafNode(0, pk0, b"alice"))
    t.add_leaf(LeafNode(1, pk1, b"bob"))
    leaf0 = t.leaf_at(0)
    assert leaf0.public_key == pk0
    assert leaf0.credential == b"alice"
    leaf1 = t.leaf_at(1)
    assert leaf1.public_key == pk1
    assert leaf1.credential == b"bob"


def test_leaf_at_out_of_range() -> None:
    t = RatchetTree()
    with pytest.raises(MLSTreeError, match="out of range"):
        t.leaf_at(0)


# ── remove_leaf ─────────────────────────────────────────────────────────────


def test_remove_leaf_blanks_leaf_and_direct_path() -> None:
    t = RatchetTree()
    for i in range(4):
        t.add_leaf(LeafNode(i, bytes([i + 1]) * 32))
    t.set_parent_hash(1, b"\x11" * 32)
    t.set_parent_hash(2, b"\x22" * 32)
    t.set_parent_hash(0, b"\xaa" * 32)
    t.remove_leaf(2)
    assert t.nodes[5] is None
    assert t.nodes[2] is None
    assert t.nodes[0] is None


def test_remove_leaf_out_of_range() -> None:
    t = RatchetTree()
    with pytest.raises(MLSTreeError, match="out of range"):
        t.remove_leaf(0)


def test_remove_leaf_does_not_affect_unrelated_branch() -> None:
    t = RatchetTree()
    for i in range(4):
        t.add_leaf(LeafNode(i, bytes([i + 1]) * 32))
    t.set_parent_hash(1, b"\x11" * 32)
    t.set_parent_hash(2, b"\x22" * 32)
    t.remove_leaf(3)  # removes leaf 3 (node index 6)
    assert t.nodes[6] is None  # leaf 3 blanked
    assert t.nodes[2] is None  # parent blanked
    assert t.nodes[0] is None  # root blanked
    assert isinstance(t.nodes[3], LeafNode)  # leaf 0 (node 3) unaffected
    assert isinstance(t.nodes[4], LeafNode)  # leaf 1 (node 4) unaffected
    assert isinstance(t.nodes[5], LeafNode)  # leaf 2 (node 5) unaffected


# ── update_leaf ─────────────────────────────────────────────────────────────


def test_update_leaf_key() -> None:
    t = RatchetTree()
    old_pk = b"\x01" * 32
    new_pk = b"\xfe" * 32
    t.add_leaf(LeafNode(0, old_pk))
    updated = t.update_leaf(0, new_pk)
    assert updated.public_key == new_pk
    assert t.leaf_at(0).public_key == new_pk


# ── blank_node / is_blank / set_parent_hash ─────────────────────────────────


def test_blank_node() -> None:
    t = _tree_with_leaves(1)
    assert not t.is_blank(0)
    t.blank_node(0)
    assert t.is_blank(0)


def test_blank_node_out_of_range() -> None:
    t = RatchetTree()
    with pytest.raises(MLSTreeError, match="out of range"):
        t.blank_node(0)


def test_set_parent_hash() -> None:
    t = _tree_with_leaves(4)
    t.set_parent_hash(1, b"\xaa" * 32)
    node = t.nodes[1]
    assert isinstance(node, ParentNode)
    assert node.public_key == b"\xaa" * 32


def test_set_parent_hash_on_leaf_raises() -> None:
    t = _tree_with_leaves(4)
    with pytest.raises(MLSTreeError, match="is a leaf"):
        t.set_parent_hash(3, b"\x00" * 32)  # node 3 is first leaf


# ── edge cases ──────────────────────────────────────────────────────────────


def test_blank_node_out_of_range_upper() -> None:
    t = _tree_with_leaves(2)
    with pytest.raises(MLSTreeError, match="out of range"):
        t.blank_node(100)


def test_remove_leaf_negative_index() -> None:
    t = RatchetTree()
    with pytest.raises(MLSTreeError, match="out of range"):
        t.remove_leaf(-1)


def test_update_leaf_preserves_credential() -> None:
    t = RatchetTree()
    t.add_leaf(LeafNode(0, b"\x01" * 32, credential=b"eve", signature_key=b"\x03" * 32))
    updated = t.update_leaf(0, b"\xff" * 32)
    assert updated.credential == b"eve"
    assert updated.signature_key == b"\x03" * 32
    assert updated.public_key == b"\xff" * 32


def test_empty_leaf_nodes() -> None:
    t = RatchetTree()
    assert t.leaf_nodes == []
    t.add_leaf(LeafNode(0, b"\x01" * 32))
    assert t.leaf_nodes == [0]


def test_resolution_entirely_blank_tree() -> None:
    t = _tree_with_leaves(4)
    for i in range(t.size):
        t.blank_node(i)
    assert t.resolution(0) == []


def test_large_tree_direct_path() -> None:
    """Direct path for leaf 15 in a 16-leaf tree (nodes 0..30)."""
    path = RatchetTree.direct_path(15)
    assert path == [7, 3, 1, 0]


def test_large_tree_copath() -> None:
    """Copath for leaf 15 in a 16-leaf tree — excludes root sibling."""
    # d(15)=[7,3,1,0], skip root → sibling(7)=8, sibling(3)=4, sibling(1)=2
    copath = RatchetTree.copath(15)
    assert copath == [8, 4, 2]


def test_tree_rebuild_preserves_leaf_data() -> None:
    """After multiple expansions, all leaf data should be intact."""
    t = RatchetTree()
    for i in range(10):
        t.add_leaf(LeafNode(i, bytes([i + 1]) * 32, credential=f"user{i}".encode()))
    assert t.leaf_count == 10
    assert t.size == 19
    for i in range(10):
        leaf = t.leaf_at(i)
        assert leaf.index == i
        assert leaf.public_key == bytes([i + 1]) * 32
        assert leaf.credential == f"user{i}".encode()


def test_blank_path_clears_direct_path() -> None:
    t = _tree_with_leaves(4)
    t.set_parent_hash(1, b"\xaa" * 32)
    t.set_parent_hash(0, b"\xbb" * 32)
    t._blank_path(3)  # leaf 0, node index 3
    assert t.is_blank(3)  # leaf
    assert t.is_blank(1)  # parent
    assert t.is_blank(0)  # root
    assert not t.is_blank(4)  # sibling leaf unaffected
