"""Contract tests for the extracted persistent-vector trie primitives."""

from general_ludd.algorithms import persistent_vector
from general_ludd.algorithms.persistent_vector import PersistentVector
from general_ludd.algorithms.persistent_vector_nodes import _node_new


def test_node_extraction_preserves_identity_and_vector_behavior() -> None:
    """The cohesive extraction must retain helper identity and public behavior."""
    assert persistent_vector._node_new is _node_new
    vector = PersistentVector.from_iterable((1, 2, 3))
    assert vector.assoc(1, 4).pop() == PersistentVector.from_iterable((1, 4))
