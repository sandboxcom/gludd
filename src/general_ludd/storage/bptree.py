"""B+ tree index for ordered key-value storage with range-scan support."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class _Node:
    is_leaf: bool
    keys: list[Any] = field(default_factory=list)
    children: list[_Node] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)
    next_leaf: _Node | None = None


def _ceil_div(a: int, b: int) -> int:
    return -(a // -b)


class BPlusTree:
    def __init__(self, order: int = 4) -> None:
        if order < 3:
            raise ValueError("order must be >= 3")
        self._order = order
        self._max_keys = order - 1
        self._min_keys = _ceil_div(order - 1, 2)
        self._root = _Node(is_leaf=True)
        self._size = 0

    # -- public API -------------------------------------------------------

    def insert(self, key: Any, value: Any) -> None:
        root = self._root
        if len(root.keys) == self._max_keys:
            new_root = _Node(is_leaf=False, keys=[], children=[root])
            self._root = new_root
            self._split_child(new_root, 0)
        self._insert_nonfull(self._root, key, value)

    def search(self, key: Any) -> Any | None:
        node = self._root
        while not node.is_leaf:
            i = 0
            while i < len(node.keys) and key >= node.keys[i]:
                i += 1
            node = node.children[i]
        for i, k in enumerate(node.keys):
            if k == key:
                return node.values[i]
        return None

    def search_range(self, start_key: Any, end_key: Any) -> list[tuple[Any, Any]]:
        results: list[tuple[Any, Any]] = []
        leaf = self._find_leaf(self._root, start_key)
        while leaf is not None:
            for i, k in enumerate(leaf.keys):
                if k > end_key:
                    return results
                if k >= start_key:
                    results.append((k, leaf.values[i]))
            leaf = leaf.next_leaf
        return results

    def delete(self, key: Any) -> bool:
        deleted = self._delete_from(self._root, key)
        if deleted:
            self._size -= 1
            if not self._root.is_leaf and len(self._root.children) == 1:
                self._root = self._root.children[0]
        return deleted

    def __len__(self) -> int:
        return self._size

    def __contains__(self, key: Any) -> bool:
        return self.search(key) is not None

    def keys(self) -> list[Any]:
        result: list[Any] = []
        leaf = self._leftmost_leaf()
        while leaf is not None:
            result.extend(leaf.keys)
            leaf = leaf.next_leaf
        return result

    def items(self) -> list[tuple[Any, Any]]:
        result: list[tuple[Any, Any]] = []
        leaf = self._leftmost_leaf()
        while leaf is not None:
            for k, v in zip(leaf.keys, leaf.values, strict=False):
                result.append((k, v))
            leaf = leaf.next_leaf
        return result

    # -- internal helpers -------------------------------------------------

    def _find_leaf(self, node: _Node, key: Any) -> _Node:
        while not node.is_leaf:
            i = 0
            while i < len(node.keys) and key >= node.keys[i]:
                i += 1
            node = node.children[i]
        return node

    # -- insert -----------------------------------------------------------

    def _insert_nonfull(self, node: _Node, key: Any, value: Any) -> None:
        if node.is_leaf:
            i = 0
            while i < len(node.keys) and node.keys[i] < key:
                i += 1
            if i < len(node.keys) and node.keys[i] == key:
                node.values[i] = value
                return
            node.keys.insert(i, key)
            node.values.insert(i, value)
            self._size += 1
            return

        i = len(node.keys) - 1
        while i >= 0 and key < node.keys[i]:
            i -= 1
        i += 1
        child = node.children[i]
        if len(child.keys) == self._max_keys:
            self._split_child(node, i)
            if key >= node.keys[i]:
                i += 1
        self._insert_nonfull(node.children[i], key, value)

    def _split_child(self, parent: _Node, idx: int) -> None:
        child = parent.children[idx]
        mid = self._max_keys // 2
        new_node = _Node(is_leaf=child.is_leaf)

        if child.is_leaf:
            new_node.keys = child.keys[mid:]
            new_node.values = child.values[mid:]
            child.keys = child.keys[:mid]
            child.values = child.values[:mid]
            new_node.next_leaf = child.next_leaf
            child.next_leaf = new_node
            promote_key = new_node.keys[0]
        else:
            promote_key = child.keys[mid]
            new_node.keys = child.keys[mid + 1 :]
            new_node.children = child.children[mid + 1 :]
            child.keys = child.keys[:mid]
            child.children = child.children[: mid + 1]

        parent.keys.insert(idx, promote_key)
        parent.children.insert(idx + 1, new_node)

    # -- delete -----------------------------------------------------------

    def _delete_from(self, node: _Node, key: Any) -> bool:
        i = 0
        while i < len(node.keys) and node.keys[i] < key:
            i += 1

        if node.is_leaf:
            if i < len(node.keys) and node.keys[i] == key:
                node.keys.pop(i)
                node.values.pop(i)
                return True
            return False

        child_idx = i + 1 if i < len(node.keys) and node.keys[i] == key else i

        child = node.children[child_idx]
        if len(child.keys) < self._min_keys:
            self._fill_child(node, child_idx)
            if child_idx >= len(node.children) or (child_idx > 0 and node.children[child_idx] is not child):
                child_idx -= 1

        return self._delete_from(node.children[child_idx], key)

    def _fill_child(self, parent: _Node, idx: int) -> None:
        if idx > 0 and len(parent.children[idx - 1].keys) > self._min_keys:
            self._borrow_from_left(parent, idx)
        elif idx < len(parent.children) - 1 and len(parent.children[idx + 1].keys) > self._min_keys:
            self._borrow_from_right(parent, idx)
        elif idx > 0:
            self._merge_children(parent, idx - 1)
        else:
            self._merge_children(parent, idx)

    def _borrow_from_left(self, parent: _Node, idx: int) -> None:
        child = parent.children[idx]
        left_sibling = parent.children[idx - 1]

        if child.is_leaf:
            borrowed_key = left_sibling.keys.pop()
            borrowed_val = left_sibling.values.pop()
            child.keys.insert(0, borrowed_key)
            child.values.insert(0, borrowed_val)
            parent.keys[idx - 1] = child.keys[0]
        else:
            child.keys.insert(0, parent.keys[idx - 1])
            parent.keys[idx - 1] = left_sibling.keys.pop()
            child.children.insert(0, left_sibling.children.pop())

    def _borrow_from_right(self, parent: _Node, idx: int) -> None:
        child = parent.children[idx]
        right_sibling = parent.children[idx + 1]

        if child.is_leaf:
            borrowed_key = right_sibling.keys.pop(0)
            borrowed_val = right_sibling.values.pop(0)
            child.keys.append(borrowed_key)
            child.values.append(borrowed_val)
            parent.keys[idx] = right_sibling.keys[0]
        else:
            child.keys.append(parent.keys[idx])
            parent.keys[idx] = right_sibling.keys.pop(0)
            child.children.append(right_sibling.children.pop(0))

    def _merge_children(self, parent: _Node, idx: int) -> None:
        left = parent.children[idx]
        right = parent.children[idx + 1]

        if left.is_leaf:
            left.keys.extend(right.keys)
            left.values.extend(right.values)
            left.next_leaf = right.next_leaf
        else:
            left.keys.append(parent.keys[idx])
            left.keys.extend(right.keys)
            left.children.extend(right.children)

        parent.keys.pop(idx)
        parent.children.pop(idx + 1)

    # -- helpers ----------------------------------------------------------

    def _leftmost_leaf(self) -> _Node:
        node = self._root
        while not node.is_leaf:
            node = node.children[0]
        return node
