"""Cartesian tree: build from array, RMQ via LCA with Euler tour.

A Cartesian tree of an array A is a binary tree whose inorder traversal yields A
and that satisfies the min-heap property (each node's value <= children's).

RMQ(i, j) = the minimum element in A[i..j] is the LCA of nodes i and j.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar


class Comparable(Protocol):
    def __lt__(self, other: object, /) -> bool: ...
    def __gt__(self, other: object, /) -> bool: ...
    def __le__(self, other: object, /) -> bool: ...


T = TypeVar("T", bound=Comparable)


class CartesianNode:
    __slots__ = ("index", "left", "parent", "right", "value")

    def __init__(self, index: int, value: T) -> None:
        self.index = index
        self.value = value
        self.left: CartesianNode | None = None
        self.right: CartesianNode | None = None
        self.parent: CartesianNode | None = None

    def __repr__(self) -> str:
        return f"CartesianNode(idx={self.index}, val={self.value!r})"


def build_cartesian_tree(arr: Sequence[T]) -> CartesianNode | None:
    """Build a min-heap Cartesian tree in O(n) using a monotonic stack.

    Returns None for an empty array.
    """
    if not arr:
        return None
    stack: list[CartesianNode] = []
    for i, val in enumerate(arr):
        node = CartesianNode(i, val)
        last_popped: CartesianNode | None = None
        while stack and stack[-1].value > val:
            last_popped = stack.pop()
        if last_popped is not None:
            node.left = last_popped
            last_popped.parent = node
        if stack:
            stack[-1].right = node
            node.parent = stack[-1]
        stack.append(node)
    return stack[0]


def inorder(node: CartesianNode | None) -> list[object]:
    result: list[object] = []

    def _dfs(n: CartesianNode | None) -> None:
        if n is None:
            return
        _dfs(n.left)
        result.append(n.value)
        _dfs(n.right)

    _dfs(node)
    return result


def inorder_indices(node: CartesianNode | None) -> list[int]:
    result: list[int] = []

    def _dfs(n: CartesianNode | None) -> None:
        if n is None:
            return
        _dfs(n.left)
        result.append(n.index)
        _dfs(n.right)

    _dfs(node)
    return result


class EulerTour:
    """Euler tour of a Cartesian tree for RMQ via LCA.

    The tour records (depth, node_index) at each visit and supports
    RMQ(i, j) by finding the minimum-depth node between the first
    occurrences of i and j in the tour.
    """

    def __init__(self, root: CartesianNode | None) -> None:
        self._tour: list[int] = []  # node indices in tour order
        self._depth: list[int] = []  # depth of each tour entry
        self._first: dict[int, int] = {}  # first occurrence of node index
        self._min_table: list[list[int]] = []  # sparse table of argmin over tour
        self._root_index: int | None = root.index if root is not None else None

        if root is None:
            return

        self._build_tour(root, 0)
        self._build_sparse_table()

    def _build_tour(self, node: CartesianNode | None, depth: int) -> None:
        if node is None:
            return
        self._tour.append(node.index)
        self._depth.append(depth)
        if node.index not in self._first:
            self._first[node.index] = len(self._tour) - 1
        if node.left is not None:
            self._build_tour(node.left, depth + 1)
            self._tour.append(node.index)
            self._depth.append(depth)
        if node.right is not None:
            self._build_tour(node.right, depth + 1)
            self._tour.append(node.index)
            self._depth.append(depth)

    def _build_sparse_table(self) -> None:
        n = len(self._tour)
        if n == 0:
            return
        log = n.bit_length()
        self._min_table = [list(range(n)) for _ in range(log)]
        for j in range(1, log):
            step = 1 << (j - 1)
            for i in range(n - (1 << j) + 1):
                left = self._min_table[j - 1][i]
                right = self._min_table[j - 1][i + step]
                self._min_table[j][i] = left if self._depth[left] <= self._depth[right] else right

    def _rmq_tour_index(self, lo: int, r: int) -> int:
        if lo > r:
            lo, r = r, lo
        length = r - lo + 1
        k = length.bit_length() - 1
        left_cand = self._min_table[k][lo]
        right_cand = self._min_table[k][r - (1 << k) + 1]
        return left_cand if self._depth[left_cand] <= self._depth[right_cand] else right_cand

    def lca_index(self, i: int, j: int) -> int:
        """Return the index of the LCA of nodes i and j in the Cartesian tree."""
        fi = self._first[i]
        fj = self._first[j]
        return self._tour[self._rmq_tour_index(fi, fj)]

    def lca_depth(self, i: int, j: int) -> int:
        fi = self._first[i]
        fj = self._first[j]
        return self._depth[self._rmq_tour_index(fi, fj)]

    def tour_sequence(self) -> list[tuple[int, int]]:
        """Return the tour as list of (depth, index) pairs."""
        return list(zip(self._depth, self._tour, strict=False))

    def first_occurrence(self, index: int) -> int:
        return self._first.get(index, -1)

    @property
    def root_index(self) -> int | None:
        return self._root_index


def cartesian_rmq(arr: Sequence[T], i: int, j: int) -> int:
    """Return the index of the minimum element in arr[i..j] inclusive.

    Uses Cartesian tree + Euler tour LCA.  Returns the INDEX, not the value.
    """
    root = build_cartesian_tree(arr)
    if root is None:
        raise ValueError("empty array")
    tour = EulerTour(root)
    return tour.lca_index(i, j)


def cartesian_rmq_value(arr: Sequence[T], i: int, j: int) -> T:
    """Return the minimum value in arr[i..j] inclusive."""
    idx = cartesian_rmq(arr, i, j)
    return arr[idx]
