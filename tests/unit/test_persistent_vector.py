"""Public-boundary regression tests for the persistent-vector module."""

import general_ludd.algorithms.persistent_vector as persistent_vector
from general_ludd.algorithms.persistent_vector_nodes import _node_new


def test_persistent_vector_reexports_the_canonical_node_factory() -> None:
    """The extraction must preserve the legacy module's helper identity."""
    assert persistent_vector._node_new is _node_new
    assert list(persistent_vector.PersistentVector.from_iterable((1, 2))) == [1, 2]
