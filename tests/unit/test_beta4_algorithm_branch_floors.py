"""Canonical branch-floor regressions for beta4 algorithm modules."""

from __future__ import annotations

from typing import Any, cast

import pytest
from ansible_collections.general_ludd.physics.plugins.module_utils import convex_hull
from cryptography.hazmat.primitives.asymmetric import ec

from general_ludd.algorithms import finger_tree, line_intersect, oprf, persistent_vector


def test_convex_hull_handles_vertical_and_identical_collinear_points() -> None:
    """Qhull fallback retains vertical endpoints and one identical point."""
    assert convex_hull.graham_scan([(2.0, 3.0), (2.0, 1.0), (2.0, 5.0)]) == [
        (2.0, 1.0),
        (2.0, 5.0),
    ]
    assert convex_hull.graham_scan([(4.0, 7.0)] * 3) == [(4.0, 7.0)]


def test_oprf_modular_square_root_and_point_identity_edges() -> None:
    """Low-level curve helpers preserve roots and point-at-infinity cases."""
    assert oprf._mod_sqrt(0, 7) == 0
    assert oprf._mod_sqrt(3, 7) is None
    assert oprf._ec_add(1, 2, 1, 5, 7, 0) is None
    assert oprf._ec_add(1, 0, 1, 0, 7, 0) is None


def test_oprf_raw_scalar_rejects_negative_and_handles_identity() -> None:
    """Raw scalar multiplication rejects negative and zero scalars."""
    with pytest.raises(oprf.OPRError, match="non-negative"):
        oprf._scalar_mult_raw(-1, oprf.P256_GX, oprf.P256_GY, oprf.P256_P, oprf.P256_A)
    assert oprf._scalar_mult_raw(0, oprf.P256_GX, oprf.P256_GY, oprf.P256_P, oprf.P256_A) is None


def test_oprf_hash_to_curve_fails_after_bounded_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hash-to-curve remains bounded when no candidate has a square root."""
    monkeypatch.setattr(oprf, "_mod_sqrt", lambda _alpha, _prime: None)
    with pytest.raises(oprf.OPRError, match="256 attempts"):
        oprf.hash_to_curve(b"no-point", ec.SECP256R1())


def test_oprf_scalar_mult_propagates_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Public scalar multiplication preserves a low-level identity result."""
    point = oprf.hash_to_curve(b"identity", ec.SECP256R1())
    monkeypatch.setattr(oprf, "_scalar_mult_raw", lambda *_args: None)
    assert oprf.scalar_mult(1, point, ec.SECP256R1()) is None


def test_oprf_deserialize_rejects_invalid_coordinates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compressed points reject oversized coordinates and non-residues."""
    oversized = bytes([oprf.COMPRESSED_EVEN]) + oprf.P256_P.to_bytes(32, "big")
    assert oprf.deserialize_point(oversized, ec.SECP256R1()) is None

    monkeypatch.setattr(oprf, "_mod_sqrt", lambda _alpha, _prime: None)
    candidate = bytes([oprf.COMPRESSED_EVEN]) + bytes(32)
    assert oprf.deserialize_point(candidate, ec.SECP256R1()) is None


def test_oprf_blind_rejects_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blinding fails closed if scalar multiplication returns identity."""
    point = oprf.hash_to_curve(b"blind-identity", ec.SECP256R1())
    monkeypatch.setattr(oprf, "scalar_mult", lambda *_args: None)
    with pytest.raises(oprf.OPRError, match="blinding produced identity"):
        oprf.blind(point, ec.SECP256R1())


def test_oprf_evaluate_rejects_invalid_and_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Evaluation rejects malformed input and identity output."""
    private_key, _ = oprf.generate_keypair()
    with pytest.raises(oprf.OPRError, match="invalid blinded point"):
        oprf.evaluate(private_key, b"invalid", ec.SECP256R1())

    point = oprf.hash_to_curve(b"evaluate-identity", ec.SECP256R1())
    encoded = oprf.serialize_point(point)
    monkeypatch.setattr(oprf, "scalar_mult", lambda *_args: None)
    with pytest.raises(oprf.OPRError, match="evaluation produced identity"):
        oprf.evaluate(private_key, encoded, ec.SECP256R1())


def test_oprf_unblind_rejects_invalid_and_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unblinding rejects malformed input and identity output."""
    with pytest.raises(oprf.OPRError, match="invalid evaluated point"):
        oprf.unblind(b"invalid", 1, ec.SECP256R1())

    point = oprf.hash_to_curve(b"unblind-identity", ec.SECP256R1())
    encoded = oprf.serialize_point(point)
    monkeypatch.setattr(oprf, "scalar_mult", lambda *_args: None)
    with pytest.raises(oprf.OPRError, match="unblinding produced identity"):
        oprf.unblind(encoded, 1, ec.SECP256R1())


def test_persistent_vector_private_tree_edges() -> None:
    """Trie helpers cover empty, absent-child, and recursive path cases."""
    leaf = [1]
    assert persistent_vector._new_path(0, leaf) is leaf
    assert persistent_vector._new_path(10, leaf)[0][0] is leaf

    root = persistent_vector._node_new()
    tail = [*range(32)]
    pushed = persistent_vector._push_tail(1056, 10, root, tail)
    assert pushed[1][0] == tail
    assert persistent_vector._array_for(0, 5, root, tail) is tail
    assert persistent_vector._array_for(32, 5, root, tail) is tail
    assert persistent_vector._pop_tail(32, 10, root) is None


def test_persistent_vector_deep_growth_assoc_and_root_reduction() -> None:
    """Immutable vectors cross the 32-way root boundary and shrink safely."""
    vector = persistent_vector.PersistentVector.from_iterable(range(1100))
    assert len(vector) == 1100
    assert vector[1056] == 1056
    assert vector.assoc(1024, -1)[1024] == -1
    assert vector != persistent_vector.PersistentVector.from_iterable(range(3))
    assert persistent_vector.PersistentVector.__eq__(vector, []) is NotImplemented

    for _ in range(80):
        vector = vector.pop()
    assert len(vector) == 1020
    assert vector[-1] == 1019


def test_transient_vector_deep_growth_indexing_and_assoc() -> None:
    """Transient vectors cover tail/trie lookup and mutable deep association."""
    transient = persistent_vector.PersistentVector.from_iterable(range(1100)).transient()
    assert transient[-1] == 1099
    assert transient[33] == 33
    assert transient.assoc(-1, -1)[1099] == -1
    assert transient.assoc(32, -2)[32] == -2
    with pytest.raises(IndexError, match="out of range"):
        _ = transient[1100]
    with pytest.raises(IndexError, match="out of range"):
        transient.assoc(1100, 0)

    detached = persistent_vector._node_new()
    copied = transient._ensure_editable(detached)
    assert copied is not detached
    assert copied == detached

    for _ in range(12):
        transient.pop()
    assert len(transient) == 1088
    assert transient[-1] == 1087


def test_transient_vector_empty_single_and_sealed_fail_closed() -> None:
    """Transient mutation rejects empty, invalid, and post-seal operations."""
    empty = persistent_vector.TransientVector[int].empty()
    with pytest.raises(IndexError, match="empty transient"):
        empty.pop()

    single = empty.conj(1)
    assert single.pop().persistent() == persistent_vector.PersistentVector.empty()

    sealed = persistent_vector.PersistentVector.from_iterable([1, 2, 3]).transient()
    sealed.persistent()
    with pytest.raises(RuntimeError, match="already sealed"):
        sealed.conj(4)
    with pytest.raises(RuntimeError, match="already sealed"):
        sealed.pop()
    with pytest.raises(RuntimeError, match="already sealed"):
        sealed.assoc(0, 4)


def test_finger_tree_nested_nodes_and_bridge_partitioning() -> None:
    """Nested nodes flatten in order and bridge digits partition exactly."""
    nested = finger_tree.Single(
        finger_tree.Node3(finger_tree.Node2(1, 2), 3, finger_tree.Node2(4, 5))
    )
    assert finger_tree._tree_to_list(nested) == [1, 2, 3, 4, 5]
    assert [node.to_list() for node in finger_tree._nodes_of([1, 2], [3, 4])] == [
        [1, 2],
        [3, 4],
    ]
    assert [node.to_list() for node in finger_tree._nodes_of([1, 2, 3], [4, 5])] == [
        [1, 2, 3],
        [4, 5],
    ]
    with pytest.raises(ValueError, match="singleton"):
        finger_tree._nodes_of([1], [])


def test_finger_tree_shape_guards_and_absorption_fail_closed() -> None:
    """Unexpected tree and middle shapes raise instead of losing elements."""
    invalid = cast(Any, object())
    with pytest.raises(TypeError, match="Unexpected tree shape"):
        finger_tree.push_left(invalid, 1)
    with pytest.raises(TypeError, match="Unexpected tree shape"):
        finger_tree.push_right(invalid, 1)
    with pytest.raises(TypeError, match="Unexpected tree shape"):
        finger_tree._pop_left_atomic(invalid)
    with pytest.raises(TypeError, match="Unexpected tree shape"):
        finger_tree._pop_right_atomic(invalid)
    with pytest.raises(TypeError, match="Unexpected middle shape"):
        finger_tree._push_left_deep(invalid, finger_tree.Node2(1, 2))
    with pytest.raises(TypeError, match="Unexpected middle shape"):
        finger_tree._push_right_deep(invalid, finger_tree.Node2(1, 2))
    with pytest.raises(TypeError, match="Unexpected middle element"):
        finger_tree._absorb_left(finger_tree.Single(1), [2])
    with pytest.raises(TypeError, match="Unexpected middle element"):
        finger_tree._absorb_right([1], finger_tree.Single(2))


def test_finger_tree_peek_node_shapes_and_empty_guards() -> None:
    """Both ends unwrap two- and three-node digits and reject empty trees."""
    trees: tuple[finger_tree.FingerTree, ...] = (
        finger_tree.Empty(),
        finger_tree.Deep([], finger_tree.Empty(), []),
    )
    for tree in trees:
        if isinstance(tree, finger_tree.Empty):
            with pytest.raises(IndexError, match="peek"):
                finger_tree.peek_left(tree)
            with pytest.raises(IndexError, match="peek"):
                finger_tree.peek_right(tree)

    assert finger_tree.peek_left(finger_tree.Single(finger_tree.Node2(1, 2))) == 1
    assert finger_tree.peek_left(finger_tree.Single(finger_tree.Node3(1, 2, 3))) == 1
    assert finger_tree.peek_right(finger_tree.Single(finger_tree.Node2(1, 2))) == 2
    assert finger_tree.peek_right(finger_tree.Single(finger_tree.Node3(1, 2, 3))) == 3
    assert finger_tree.peek_left(
        finger_tree.Deep([finger_tree.Node3(1, 2, 3)], finger_tree.Empty(), [4])
    ) == 1
    assert finger_tree.peek_right(
        finger_tree.Deep([1], finger_tree.Empty(), [finger_tree.Node2(2, 3)])
    ) == 3


def test_finger_tree_concat_and_merge_cover_all_shapes() -> None:
    """Concatenation and middle merging preserve every supported shape pair."""
    empty = finger_tree.Empty()
    single1 = finger_tree.Single(1)
    single2 = finger_tree.Single(2)
    deep1 = finger_tree.Deep([1], empty, [2])
    deep2 = finger_tree.Deep([3], empty, [4])

    assert finger_tree._tree_to_list(finger_tree.concat(single1, deep2)) == [1, 3, 4]
    assert finger_tree._tree_to_list(finger_tree.concat(deep1, single2)) == [1, 2, 2]
    assert finger_tree._tree_to_list(finger_tree._merge_trees(single1, deep2)) == [1, 3, 4]
    assert finger_tree._tree_to_list(finger_tree._merge_trees(deep1, single2)) == [1, 2, 2]
    assert finger_tree._tree_to_list(finger_tree._merge_trees(deep1, deep2)) == [1, 2, 3, 4]
    assert finger_tree._merge_trees(empty, single1) is single1
    assert finger_tree._merge_trees(single1, empty) is single1
    with pytest.raises(TypeError, match="Unexpected concat shapes"):
        finger_tree.concat(cast(Any, object()), single1)


def test_finger_tree_index_and_split_private_boundaries() -> None:
    """Nested lookup and split helpers cover leaf and node boundaries."""
    nested = finger_tree.Node3(finger_tree.Node2(1, 2), 3, finger_tree.Node2(4, 5))
    assert [finger_tree._get_from_value(nested, i) for i in range(5)] == [1, 2, 3, 4, 5]
    with pytest.raises(IndexError, match="out of range"):
        finger_tree._get_from_value(1, 1)
    with pytest.raises(IndexError, match="out of range"):
        finger_tree._get_index(finger_tree.Empty(), 0)

    assert finger_tree._split_value(finger_tree.Node2(1, 2), 0) == ([], [1, 2])
    assert finger_tree._split_value(finger_tree.Node2(1, 2), 1) == ([1], [2])
    assert finger_tree._split_value(nested, 2) == ([nested.a], [3, nested.c])
    assert finger_tree._split_value(1, 0) == ([], [1])
    assert finger_tree._split_value(1, 1) == ([1], [])

    assert isinstance(finger_tree._build_deep([], finger_tree.Empty(), []), finger_tree.Empty)
    assert isinstance(finger_tree._build_deep([], finger_tree.Empty(), [1]), finger_tree.Single)
    assert isinstance(finger_tree._build_deep([1], finger_tree.Empty(), []), finger_tree.Single)
    middle = finger_tree.Single(finger_tree.Node2(1, 2))
    assert finger_tree._build_deep([], middle, []) is middle


def test_finger_tree_collection_wrappers_cover_empty_and_repr_paths() -> None:
    """Deque, sequence, and priority wrappers own their empty and helper paths."""
    deque = finger_tree.Deque[int]()
    assert not deque
    deque.extend([2, 3])
    deque.extend_left([0, 1])
    assert deque.to_list() == [0, 1, 2, 3]
    assert repr(deque) == "Deque([0, 1, 2, 3])"

    sequence = finger_tree.Sequence[int]()
    assert not sequence
    with pytest.raises(IndexError, match="empty sequence"):
        sequence.pop()
    with pytest.raises(IndexError, match="empty sequence"):
        sequence.pop_left()
    sequence.extend([2, 3])
    sequence.push_left(1)
    assert sequence.peek_left() == 1
    assert sequence.peek() == 3
    assert repr(sequence) == "Sequence([1, 2, 3])"

    priority = finger_tree.PriorityDeque[int]()
    with pytest.raises(IndexError, match="empty priority"):
        priority.pop_min()
    with pytest.raises(IndexError, match="empty priority"):
        priority.pop_max()
    priority.push_min(1)
    priority.push_max(3)
    assert priority.peek_min() == 1
    assert priority.peek_max() == 3
    assert repr(priority) == "PriorityDeque([1, 3])"


def test_line_intersection_special_collinear_branches() -> None:
    """Every collinear endpoint ordering is treated as an intersection."""
    point = line_intersect.Point
    segment = line_intersect.Segment
    horizontal = segment(point(0, 0), point(4, 0))
    cases = [
        segment(point(2, 0), point(6, 0)),
        segment(point(-2, 0), point(2, 0)),
        segment(point(-1, 0), point(5, 0)),
        segment(point(4, 0), point(6, 0)),
    ]
    assert all(line_intersect.segments_intersect(horizontal, other) for other in cases)
    assert not line_intersect.segments_intersect(
        horizontal,
        segment(point(5, 0), point(6, 0)),
    )


def test_line_intersection_sweep_rechecks_neighbors_after_removal() -> None:
    """Bentley-Ottmann detects outer neighbors exposed by an ending segment."""
    point = line_intersect.Point
    segment = line_intersect.Segment
    segments = [
        segment(point(0, 0), point(10, 10)),
        segment(point(0, 5), point(2, 5)),
        segment(point(0, 10), point(10, 0)),
    ]
    intersections = line_intersect.bentley_ottmann(segments)
    assert any(first == 0 and second == 2 for first, second, _ in intersections)


def test_line_intersection_reversed_sweep_and_outside_crossing() -> None:
    """Reversed endpoints sort correctly and infinite-line crossings stay bounded."""
    point = line_intersect.Point
    segment = line_intersect.Segment
    reversed_segments = [
        segment(point(4, 0), point(0, 4)),
        segment(point(0, 0), point(4, 4)),
    ]
    assert line_intersect.shamos_hoey(reversed_segments)
    assert line_intersect._sweep_y(segment(point(1, 4), point(1, 2)), 1) == 2
    assert line_intersect._sweep_y(segment(point(0, 0), point(4, 4)), 2) == 2
    assert (
        line_intersect.compute_intersection(
            segment(point(0, 0), point(1, 0)),
            segment(point(2, -1), point(2, 1)),
        )
        is None
    )


def test_line_intersection_collinear_merge_skips_consumed_segments() -> None:
    """Collinear merging consumes each overlapping group exactly once."""
    point = line_intersect.Point
    segment = line_intersect.Segment
    merged = line_intersect.collinear_segments(
        [
            segment(point(0, 0), point(2, 0)),
            segment(point(1, 0), point(3, 0)),
            segment(point(2, 0), point(4, 0)),
            segment(point(0, 1), point(1, 1)),
        ]
    )
    assert merged == [
        segment(point(0, 0), point(4, 0)),
        segment(point(0, 1), point(1, 1)),
    ]
