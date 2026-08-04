"""Deep tree data structure tests: Trie, Radix Tree, B-Tree, Merkle Tree."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import pytest

# ---------------------------------------------------------------------------
# Trie
# ---------------------------------------------------------------------------


class TrieNode:
    __slots__ = ("children", "is_end")

    def __init__(self) -> None:
        self.children: dict[str, TrieNode] = {}
        self.is_end = False


class Trie:
    def __init__(self) -> None:
        self.root = TrieNode()

    def insert(self, word: str) -> None:
        node = self.root
        for ch in word:
            node = node.children.setdefault(ch, TrieNode())
        node.is_end = True

    def search(self, word: str) -> bool:
        node = self.root
        for ch in word:
            if ch not in node.children:
                return False
            node = node.children[ch]
        return node.is_end

    def starts_with(self, prefix: str) -> list[str]:
        node = self.root
        for ch in prefix:
            if ch not in node.children:
                return []
            node = node.children[ch]
        return self._collect(node, prefix)

    def _collect(self, node: TrieNode, prefix: str) -> list[str]:
        words: list[str] = []
        stack: list[tuple[TrieNode, str]] = [(node, prefix)]
        while stack:
            cur, path = stack.pop()
            if cur.is_end:
                words.append(path)
            for ch, child in cur.children.items():
                stack.append((child, path + ch))
        return words

    def remove(self, word: str) -> bool:
        return self._remove(self.root, word, 0)

    def _remove(self, node: TrieNode, word: str, depth: int) -> bool:
        if depth == len(word):
            if not node.is_end:
                return False
            node.is_end = False
            return len(node.children) == 0
        ch = word[depth]
        if ch not in node.children:
            return False
        should_delete = self._remove(node.children[ch], word, depth + 1)
        if should_delete:
            del node.children[ch]
            return len(node.children) == 0 and not node.is_end
        return False

    def __len__(self) -> int:
        return len(self.starts_with(""))

    def __contains__(self, word: str) -> bool:
        return self.search(word)


# ---------------------------------------------------------------------------
# Radix Tree (compressed trie)
# ---------------------------------------------------------------------------


class RadixNode:
    __slots__ = ("children", "is_end", "key")

    def __init__(self, key: str = "") -> None:
        self.children: dict[str, RadixNode] = {}
        self.is_end = False
        self.key = key


class RadixTree:
    def __init__(self) -> None:
        self.root = RadixNode()

    @staticmethod
    def _common_prefix(a: str, b: str) -> int:
        i = 0
        while i < len(a) and i < len(b) and a[i] == b[i]:
            i += 1
        return i

    def insert(self, word: str) -> None:
        if not word:
            self.root.is_end = True
            return
        self._insert(self.root, word)

    def _insert(self, node: RadixNode, word: str) -> None:
        for _i, (edge, child) in enumerate(list(node.children.items())):
            cp = self._common_prefix(edge, word)
            if cp == 0:
                continue
            if cp == len(edge):
                self._insert(child, word[cp:])
                return
            common = edge[:cp]
            old_suffix = edge[cp:]
            new_suffix = word[cp:]
            mid = RadixNode(common)
            node.children[common] = mid
            mid.children[old_suffix] = child
            child.key = old_suffix
            del node.children[edge]
            if new_suffix:
                mid.children[new_suffix] = RadixNode(new_suffix)
                mid.children[new_suffix].is_end = True
            else:
                mid.is_end = True
            return
        node.children[word] = RadixNode(word)
        node.children[word].is_end = True

    def search(self, word: str) -> bool:
        node = self.root
        remaining = word
        while remaining:
            found = False
            for edge, child in node.children.items():
                if remaining.startswith(edge):
                    node = child
                    remaining = remaining[len(edge) :]
                    found = True
                    break
            if not found:
                return False
        return node.is_end

    def starts_with(self, prefix: str) -> list[str]:
        node = self.root
        remaining = prefix
        while remaining:
            found = False
            for edge, child in node.children.items():
                if remaining.startswith(edge) or edge.startswith(remaining):
                    if remaining.startswith(edge):
                        node = child
                        remaining = remaining[len(edge) :]
                    else:
                        node = child
                        remaining = ""
                    found = True
                    break
            if not found:
                return []
        return self._collect(node, prefix)

    def _collect(self, node: RadixNode, prefix: str) -> list[str]:
        words: list[str] = []
        stack: list[tuple[RadixNode, str]] = [(node, prefix)]
        while stack:
            cur, path = stack.pop()
            if cur.is_end:
                words.append(path)
            for edge, child in cur.children.items():
                stack.append((child, path + edge))
        return words

    def __contains__(self, word: str) -> bool:
        return self.search(word)


# ---------------------------------------------------------------------------
# B-Tree (order 3+)
# ---------------------------------------------------------------------------


class BTreeNode:
    __slots__ = ("children", "keys", "leaf")

    def __init__(self, leaf: bool = True) -> None:
        self.keys: list[int] = []
        self.children: list[BTreeNode] = []
        self.leaf = leaf


class BTree:
    def __init__(self, t: int = 2) -> None:
        self.root = BTreeNode(leaf=True)
        self.t = max(t, 2)

    def insert(self, key: int) -> None:
        root = self.root
        if len(root.keys) == (2 * self.t - 1):
            new_root = BTreeNode(leaf=False)
            new_root.children.append(root)
            self._split_child(new_root, 0)
            self.root = new_root
            self._insert_nonfull(new_root, key)
        else:
            self._insert_nonfull(root, key)

    def _split_child(self, parent: BTreeNode, idx: int) -> None:
        t = self.t
        child = parent.children[idx]
        new_child = BTreeNode(leaf=child.leaf)
        mid = t - 1
        parent.keys.insert(idx, child.keys[mid])
        parent.children.insert(idx + 1, new_child)
        new_child.keys = child.keys[mid + 1 :]
        child.keys = child.keys[:mid]
        if not child.leaf:
            new_child.children = child.children[t:]
            child.children = child.children[:t]

    def _insert_nonfull(self, node: BTreeNode, key: int) -> None:
        i = len(node.keys) - 1
        if node.leaf:
            node.keys.append(0)
            while i >= 0 and key < node.keys[i]:
                node.keys[i + 1] = node.keys[i]
                i -= 1
            node.keys[i + 1] = key
        else:
            while i >= 0 and key < node.keys[i]:
                i -= 1
            i += 1
            if len(node.children[i].keys) == (2 * self.t - 1):
                self._split_child(node, i)
                if key > node.keys[i]:
                    i += 1
            self._insert_nonfull(node.children[i], key)

    def search(self, key: int) -> bool:
        return self._search(self.root, key)

    def _search(self, node: BTreeNode, key: int) -> bool:
        i = 0
        while i < len(node.keys) and key > node.keys[i]:
            i += 1
        if i < len(node.keys) and key == node.keys[i]:
            return True
        if node.leaf:
            return False
        return self._search(node.children[i], key)

    def inorder(self) -> list[int]:
        result: list[int] = []
        self._inorder(self.root, result)
        return result

    def _inorder(self, node: BTreeNode, result: list[int]) -> None:
        if node.leaf:
            result.extend(node.keys)
        else:
            for i, key in enumerate(node.keys):
                self._inorder(node.children[i], result)
                result.append(key)
            self._inorder(node.children[len(node.keys)], result)

    def __contains__(self, key: int) -> bool:
        return self.search(key)


# ---------------------------------------------------------------------------
# Merkle Tree
# ---------------------------------------------------------------------------


def sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


class MerkleTree:
    def __init__(self, leaves: Iterable[str]) -> None:
        self.leaves = list(leaves)
        self.layers: list[list[str]] = []
        self._build()

    def _build(self) -> None:
        if not self.leaves:
            self.root_hash = sha256("")
            return
        hashed_leaves = [sha256(leaf) for leaf in self.leaves]
        self.layers = [hashed_leaves]
        current = hashed_leaves
        while len(current) > 1:
            next_layer: list[str] = []
            for i in range(0, len(current), 2):
                left = current[i]
                right = current[i + 1] if i + 1 < len(current) else left
                next_layer.append(sha256(left + right))
            self.layers.append(next_layer)
            current = next_layer
        self.root_hash = current[0]

    def get_proof(self, index: int) -> list[tuple[str, bool]]:
        if index < 0 or index >= len(self.leaves):
            raise IndexError("leaf index out of range")
        proof: list[tuple[str, bool]] = []
        idx = index
        for layer in self.layers[:-1]:
            if idx % 2 == 0:
                sibling_idx = idx + 1
                is_right = True
            else:
                sibling_idx = idx - 1
                is_right = False
            if sibling_idx < len(layer):
                proof.append((layer[sibling_idx], is_right))
            else:
                proof.append((layer[idx], False))
            idx //= 2
        return proof

    @staticmethod
    def verify_proof(leaf: str, proof: list[tuple[str, bool]], root_hash: str) -> bool:
        current = sha256(leaf)
        for sibling_hash, is_right in proof:
            current = sha256(current + sibling_hash) if is_right else sha256(sibling_hash + current)
        return current == root_hash


# ---------------------------------------------------------------------------
# Tests: Trie
# ---------------------------------------------------------------------------


class TestTrieInsertLookup:
    def test_insert_and_search_single(self) -> None:
        trie = Trie()
        trie.insert("hello")
        assert trie.search("hello") is True
        assert trie.search("hell") is False
        assert trie.search("hello!") is False

    def test_insert_and_search_multiple(self) -> None:
        trie = Trie()
        for w in ("cat", "car", "cart", "cargo", "dog"):
            trie.insert(w)
        assert all(trie.search(w) for w in ("cat", "car", "cart", "cargo", "dog"))
        assert not trie.search("card")
        assert not trie.search("do")

    def test_duplicate_insert(self) -> None:
        trie = Trie()
        trie.insert("abc")
        trie.insert("abc")
        assert trie.search("abc") is True
        assert len(trie) == 1

    def test_empty_string(self) -> None:
        trie = Trie()
        trie.insert("")
        assert trie.search("") is True
        assert len(trie) == 1

    def test_contains_dunder(self) -> None:
        trie = Trie()
        trie.insert("test")
        assert "test" in trie
        assert "nope" not in trie


class TestTriePrefixSearch:
    def test_prefix_multiple_matches(self) -> None:
        trie = Trie()
        for w in ("app", "apple", "applet", "application", "apt"):
            trie.insert(w)
        results = trie.starts_with("app")
        assert sorted(results) == sorted(["app", "apple", "applet", "application"])

    def test_prefix_exact_word(self) -> None:
        trie = Trie()
        trie.insert("prefix")
        assert trie.starts_with("prefix") == ["prefix"]

    def test_prefix_no_matches(self) -> None:
        trie = Trie()
        trie.insert("hello")
        assert trie.starts_with("xyz") == []

    def test_prefix_empty_returns_all(self) -> None:
        trie = Trie()
        for w in ("a", "b", "c"):
            trie.insert(w)
        assert sorted(trie.starts_with("")) == sorted(["a", "b", "c"])


class TestTrieDeletion:
    def test_remove_existing(self) -> None:
        trie = Trie()
        trie.insert("abc")
        assert trie.remove("abc") is True
        assert "abc" not in trie

    def test_remove_nonexistent(self) -> None:
        trie = Trie()
        trie.insert("abc")
        assert trie.remove("abcd") is False

    def test_remove_prefix_preserves_suffix(self) -> None:
        trie = Trie()
        trie.insert("ab")
        trie.insert("abc")
        trie.remove("abc")
        assert trie.search("ab") is True
        assert trie.search("abc") is False

    def test_remove_non_word_prefix(self) -> None:
        trie = Trie()
        trie.insert("abc")
        assert trie.remove("ab") is False
        assert trie.search("abc") is True


# ---------------------------------------------------------------------------
# Tests: Radix Tree
# ---------------------------------------------------------------------------


class TestRadixTreeSearch:
    def test_insert_and_search_single(self) -> None:
        rt = RadixTree()
        rt.insert("hello")
        assert rt.search("hello") is True
        assert rt.search("hell") is False

    def test_insert_and_search_multiple(self) -> None:
        rt = RadixTree()
        for w in ("test", "testing", "tester", "toast", "toaster"):
            rt.insert(w)
        assert all(rt.search(w) for w in ("test", "testing", "tester", "toast", "toaster"))
        assert not rt.search("tes")
        assert not rt.search("to")

    def test_empty_string_root(self) -> None:
        rt = RadixTree()
        rt.insert("")
        assert rt.search("") is True


class TestRadixTreeCompaction:
    def test_shared_prefix_compacted(self) -> None:
        rt = RadixTree()
        rt.insert("romane")
        rt.insert("romanus")
        rt.insert("romulus")
        rt.insert("rubens")
        rt.insert("ruber")
        rt.insert("rubicon")
        rt.insert("rubicundus")
        for w in ("romane", "romanus", "romulus", "rubens", "ruber", "rubicon", "rubicundus"):
            assert rt.search(w), f"missing: {w}"
        assert not rt.search("roman")
        assert not rt.search("rub")

    def test_insert_causes_split(self) -> None:
        rt = RadixTree()
        rt.insert("abcde")
        rt.insert("abxyz")
        assert rt.search("abcde") is True
        assert rt.search("abxyz") is True
        assert not rt.search("ab")

    def test_prefix_search_after_compaction(self) -> None:
        rt = RadixTree()
        for w in ("app", "apple", "application", "apply"):
            rt.insert(w)
        results = rt.starts_with("app")
        assert sorted(results) == sorted(["app", "apple", "application", "apply"])


# ---------------------------------------------------------------------------
# Tests: B-Tree
# ---------------------------------------------------------------------------


class TestBTreeInsertSearch:
    def test_insert_and_search_small(self) -> None:
        bt = BTree(t=2)
        for k in (10, 20, 5, 6, 12, 30, 7, 17):
            bt.insert(k)
        assert all(bt.search(k) for k in (10, 20, 5, 6, 12, 30, 7, 17))
        assert not bt.search(99)
        assert not bt.search(0)

    def test_insert_many_maintains_sorted_order(self) -> None:
        bt = BTree(t=3)
        keys = list(range(1, 101))
        for k in keys:
            bt.insert(k)
        assert bt.inorder() == keys

    def test_insert_causes_splits(self) -> None:
        bt = BTree(t=2)
        keys = list(range(1, 51))
        for k in keys:
            bt.insert(k)
        assert bt.inorder() == keys
        assert all(bt.search(k) for k in keys)

    def test_contains_dunder(self) -> None:
        bt = BTree(t=2)
        bt.insert(42)
        assert 42 in bt
        assert 99 not in bt


# ---------------------------------------------------------------------------
# Tests: Merkle Tree
# ---------------------------------------------------------------------------


class TestMerkleTreeHash:
    def test_single_leaf(self) -> None:
        mt = MerkleTree(["data"])
        assert len(mt.root_hash) == 64
        assert mt.root_hash == sha256("data")

    def test_two_leaves(self) -> None:
        mt = MerkleTree(["a", "b"])
        expected = sha256(sha256("a") + sha256("b"))
        assert mt.root_hash == expected

    def test_odd_leaves_duplicates_last(self) -> None:
        mt = MerkleTree(["a", "b", "c"])
        h = [sha256(x) for x in ("a", "b", "c")]
        layer1 = sha256(h[0] + h[1]), sha256(h[2] + h[2])
        assert mt.root_hash == sha256(layer1[0] + layer1[1])

    def test_empty_tree(self) -> None:
        mt = MerkleTree([])
        assert mt.root_hash == sha256("")

    def test_deterministic(self) -> None:
        mt1 = MerkleTree(["x", "y", "z"])
        mt2 = MerkleTree(["x", "y", "z"])
        assert mt1.root_hash == mt2.root_hash


class TestMerkleProof:
    def test_proof_and_verify(self) -> None:
        mt = MerkleTree(["a", "b", "c", "d"])
        proof = mt.get_proof(2)
        assert MerkleTree.verify_proof("c", proof, mt.root_hash) is True

    def test_verify_tampered_leaf_fails(self) -> None:
        mt = MerkleTree(["a", "b", "c", "d"])
        proof = mt.get_proof(1)
        assert MerkleTree.verify_proof("tampered", proof, mt.root_hash) is False

    def test_verify_tampered_root_fails(self) -> None:
        mt = MerkleTree(["a", "b", "c", "d"])
        proof = mt.get_proof(0)
        assert MerkleTree.verify_proof("a", proof, "badhash123") is False

    def test_proof_oob_raises(self) -> None:
        mt = MerkleTree(["a", "b"])
        with pytest.raises(IndexError):
            mt.get_proof(5)

    def test_large_tree_proof(self) -> None:
        leaves = [f"leaf_{i}" for i in range(100)]
        mt = MerkleTree(leaves)
        for idx in (0, 1, 50, 98, 99):
            proof = mt.get_proof(idx)
            assert MerkleTree.verify_proof(leaves[idx], proof, mt.root_hash), f"proof failed at {idx}"
