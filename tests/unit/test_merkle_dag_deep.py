"""Deep tests for Merkle DAG — content-addressable nodes, IPLD-style
links, traversal, verification, and CID-based operations.
"""

from __future__ import annotations

import hashlib

import pytest

from general_ludd.storage.merkle_dag import (
    CID,
    MerkleDAG,
    MerkleLink,
    MerkleNode,
    NodeValidationError,
    PathResolutionError,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _cid_of(data: str) -> CID:
    m = hashlib.sha256()
    m.update(data.encode())
    return CID(m.digest())


def _node(data: str, links: list[MerkleLink] | None = None) -> MerkleNode:
    return MerkleNode(data, links or [])


def _link(name: str, cid: str, size: int = 0) -> MerkleLink:
    return MerkleLink(name, CID(bytes.fromhex(cid)), size)


# ---------------------------------------------------------------------------
# CID
# ---------------------------------------------------------------------------


class TestCID:
    def test_cid_creation_from_bytes(self) -> None:
        c = CID(b"\x00" * 32)
        assert len(c.digest) == 32
        assert c.digest == b"\x00" * 32

    def test_cid_equality(self) -> None:
        a = CID(b"\x01" * 32)
        b = CID(b"\x01" * 32)
        c = CID(b"\x02" * 32)
        assert a == b
        assert a != c

    def test_cid_hex_roundtrip(self) -> None:
        raw = hashlib.sha256(b"hello").digest()
        c = CID(raw)
        assert c.hex == raw.hex()

    def test_cid_str(self) -> None:
        raw = hashlib.sha256(b"node").digest()
        c = CID(raw)
        s = str(c)
        assert s == raw.hex()
        assert isinstance(s, str)

    def test_cid_hashability(self) -> None:
        c = CID(b"\xab" * 32)
        d = {c: "value"}
        assert d[c] == "value"
        assert c in d

    def test_cid_ordering(self) -> None:
        a = CID(b"\x00" * 32)
        b = CID(b"\xff" * 32)
        assert a < b
        assert a <= b
        assert b > a
        assert b >= a

    def test_cid_from_hex(self) -> None:
        raw = hashlib.sha256(b"test").hexdigest()
        c = CID.from_hex(raw)
        assert c.hex == raw
        assert isinstance(c.digest, bytes)
        assert len(c.digest) == 32


# ---------------------------------------------------------------------------
# MerkleNode
# ---------------------------------------------------------------------------


class TestMerkleNode:
    def test_node_creation_plain(self) -> None:
        n = MerkleNode("hello")
        assert n.data == "hello"
        assert n.links == []

    def test_node_creation_with_links(self) -> None:
        cid_a = CID(b"\x01" * 32)
        cid_b = CID(b"\x02" * 32)
        links = [MerkleLink("child_a", cid_a, 12), MerkleLink("child_b", cid_b, 34)]
        n = MerkleNode("parent", links)
        assert n.data == "parent"
        assert len(n.links) == 2
        assert n.links[0].name == "child_a"
        assert n.links[1].name == "child_b"

    def test_node_cid_is_deterministic(self) -> None:
        n1 = MerkleNode("same data")
        n2 = MerkleNode("same data")
        assert n1.cid == n2.cid

    def test_node_cid_differs_on_data_change(self) -> None:
        n1 = MerkleNode("data A")
        n2 = MerkleNode("data B")
        assert n1.cid != n2.cid

    def test_node_cid_differs_on_link_change(self) -> None:
        cid_a = CID(b"\x01" * 32)
        n1 = MerkleNode("x", [MerkleLink("a", cid_a, 0)])
        n2 = MerkleNode("x", [])
        assert n1.cid != n2.cid

    def test_node_cid_includes_link_names(self) -> None:
        cid = CID(b"\x01" * 32)
        n1 = MerkleNode("x", [MerkleLink("alpha", cid, 0)])
        n2 = MerkleNode("x", [MerkleLink("beta", cid, 0)])
        assert n1.cid != n2.cid

    def test_node_cid_includes_link_cid(self) -> None:
        n1 = MerkleNode("x", [MerkleLink("a", CID(b"\x01" * 32), 0)])
        n2 = MerkleNode("x", [MerkleLink("a", CID(b"\x02" * 32), 0)])
        assert n1.cid != n2.cid

    def test_node_verify_leaf(self) -> None:
        n = MerkleNode("leaf")
        n.verify()

    def test_node_repr(self) -> None:
        n = MerkleNode("hello")
        r = repr(n)
        assert "MerkleNode" in r
        assert n.cid.hex[:12] in r

    def test_node_serialize_leaf(self) -> None:
        n = MerkleNode("payload")
        d = n.serialize()
        assert d["data"] == "payload"
        assert d["links"] == []

    def test_node_serialize_with_links(self) -> None:
        cid_a = CID(b"\x01" * 32)
        cid_b = CID(b"\x02" * 32)
        n = MerkleNode(
            "root",
            [MerkleLink("a", cid_a, 10), MerkleLink("b", cid_b, 20)],
        )
        d = n.serialize()
        assert d["data"] == "root"
        assert len(d["links"]) == 2
        assert d["links"][0] == {"name": "a", "cid": cid_a.hex, "size": 10}
        assert d["links"][1] == {"name": "b", "cid": cid_b.hex, "size": 20}

    def test_node_deserialize_roundtrip(self) -> None:
        n1 = MerkleNode("hello", [MerkleLink("child", CID(b"\xaa" * 32), 5)])
        d = n1.serialize()
        n2 = MerkleNode.deserialize(d)
        assert n2.data == n1.data
        assert len(n2.links) == 1
        assert n2.links[0].name == "child"
        assert n2.links[0].cid == CID(b"\xaa" * 32)
        assert n2.cid == n1.cid


# ---------------------------------------------------------------------------
# MerkleDAG
# ---------------------------------------------------------------------------


@pytest.fixture
def dag() -> MerkleDAG[str]:
    return MerkleDAG()


class TestMerkleDAGPutGet:
    def test_put_and_get_node(self, dag: MerkleDAG) -> None:
        n = MerkleNode("leaf")
        dag.put(n)
        assert dag.get(n.cid) is n

    def test_get_missing_raises(self, dag: MerkleDAG) -> None:
        cid = CID(hashlib.sha256(b"nope").digest())
        with pytest.raises(KeyError):
            dag.get(cid)

    def test_contains(self, dag: MerkleDAG) -> None:
        n = MerkleNode("x")
        assert not dag.contains(n.cid)
        dag.put(n)
        assert dag.contains(n.cid)

    def test_put_multiple(self, dag: MerkleDAG) -> None:
        n1 = MerkleNode("a")
        n2 = MerkleNode("b")
        dag.put(n1)
        dag.put(n2)
        assert dag.get(n1.cid) is n1
        assert dag.get(n2.cid) is n2

    def test_put_idempotent(self, dag: MerkleDAG) -> None:
        n1 = MerkleNode("x")
        dag.put(n1)
        n2 = MerkleNode("x")
        dag.put(n2)
        assert dag.get(n1.cid) is n1


class TestMerkleDAGLinks:
    def test_link_two_nodes(self, dag: MerkleDAG) -> None:
        child = MerkleNode("child")
        dag.put(child)
        parent = MerkleNode("parent", [MerkleLink("c", child.cid, 0)])
        dag.put(parent)
        assert dag.get(parent.cid) is parent
        resolved = dag.get(parent.links[0].cid)
        assert resolved is child
        assert resolved.data == "child"

    def test_chain_of_three(self, dag: MerkleDAG) -> None:
        a = MerkleNode("a")
        dag.put(a)
        b = MerkleNode("b", [MerkleLink("prev", a.cid, 0)])
        dag.put(b)
        c = MerkleNode("c", [MerkleLink("prev", b.cid, 0)])
        dag.put(c)
        assert dag.get(c.cid).data == "c"
        assert dag.get(b.cid).data == "b"
        assert dag.get(a.cid).data == "a"

    def test_tree_structure(self, dag: MerkleDAG) -> None:
        left = MerkleNode("left")
        right = MerkleNode("right")
        dag.put(left)
        dag.put(right)
        root = MerkleNode(
            "root",
            [
                MerkleLink("l", left.cid, 0),
                MerkleLink("r", right.cid, 0),
            ],
        )
        dag.put(root)
        assert dag.get(root.cid).data == "root"
        assert dag.get(left.cid).data == "left"
        assert dag.get(right.cid).data == "right"

    def test_dag_multiple_parents(self, dag: MerkleDAG) -> None:
        shared = MerkleNode("shared-child")
        dag.put(shared)
        parent1 = MerkleNode("p1", [MerkleLink("child", shared.cid, 0)])
        parent2 = MerkleNode("p2", [MerkleLink("child", shared.cid, 0)])
        dag.put(parent1)
        dag.put(parent2)
        assert dag.get(parent1.cid).links[0].cid == shared.cid
        assert dag.get(parent2.cid).links[0].cid == shared.cid


class TestMerkleDAGTraversal:
    def test_walk_depth_first(self, dag: MerkleDAG) -> None:
        leaf = MerkleNode("leaf")
        dag.put(leaf)
        mid = MerkleNode("mid", [MerkleLink("leaf", leaf.cid, 0)])
        dag.put(mid)
        root = MerkleNode("root", [MerkleLink("mid", mid.cid, 0)])
        dag.put(root)
        order: list[str] = []

        def visitor(n: MerkleNode) -> None:
            order.append(str(n.data))

        dag.walk(root.cid, visitor)
        assert order[0] == "root"
        assert "leaf" in order
        assert "mid" in order
        assert len(order) == 3

    def test_walk_empty_links(self, dag: MerkleDAG) -> None:
        n = MerkleNode("only")
        dag.put(n)
        order: list[str] = []
        dag.walk(n.cid, lambda n: order.append(str(n.data)))
        assert order == ["only"]

    def test_walk_missing_link(self, dag: MerkleDAG) -> None:
        missing_cid = CID(hashlib.sha256(b"missing").digest())
        n = MerkleNode("parent", [MerkleLink("gone", missing_cid, 0)])
        dag.put(n)
        order: list[str] = []
        dag.walk(n.cid, lambda n: order.append(str(n.data)))
        assert order == ["parent"]


class TestMerkleDAGVerification:
    def test_verify_leaf(self, dag: MerkleDAG) -> None:
        n = MerkleNode("leaf")
        dag.put(n)
        dag.verify(n.cid)

    def test_verify_chain(self, dag: MerkleDAG) -> None:
        a = MerkleNode("a")
        dag.put(a)
        b = MerkleNode("b", [MerkleLink("a", a.cid, 0)])
        dag.put(b)
        dag.verify(b.cid)

    def test_verify_dangling_link_raises(self, dag: MerkleDAG) -> None:
        missing = CID(hashlib.sha256(b"missing").digest())
        n = MerkleNode("parent", [MerkleLink("gone", missing, 0)])
        dag.put(n)
        with pytest.raises(NodeValidationError, match="dangling"):
            dag.verify(n.cid)

    def test_verify_tampered_data_raises(self, dag: MerkleDAG) -> None:
        n = MerkleNode("original")
        dag.put(n)
        n.data = "tampered"
        with pytest.raises(NodeValidationError, match="content"):
            dag.verify(n.cid)


class TestMerkleDAGPathResolution:
    def test_resolve_simple_path(self, dag: MerkleDAG) -> None:
        child = MerkleNode("child-data")
        dag.put(child)
        root = MerkleNode("root", [MerkleLink("c", child.cid, 0)])
        dag.put(root)
        resolved = dag.resolve(root.cid, "c")
        assert resolved is child
        assert resolved.data == "child-data"

    def test_resolve_nested_path(self, dag: MerkleDAG) -> None:
        leaf = MerkleNode("deep")
        dag.put(leaf)
        inner = MerkleNode("inner", [MerkleLink("deep", leaf.cid, 0)])
        dag.put(inner)
        outer = MerkleNode("outer", [MerkleLink("inner", inner.cid, 0)])
        dag.put(outer)
        resolved = dag.resolve(outer.cid, "inner/deep")
        assert resolved is leaf
        assert resolved.data == "deep"

    def test_resolve_missing_segment_raises(self, dag: MerkleDAG) -> None:
        n = MerkleNode("leaf")
        dag.put(n)
        with pytest.raises(PathResolutionError, match=r"link.*not found"):
            dag.resolve(n.cid, "missing")

    def test_resolve_bad_root_raises(self, dag: MerkleDAG) -> None:
        cid = CID(hashlib.sha256(b"nope").digest())
        with pytest.raises(KeyError):
            dag.resolve(cid, "anything")

    def test_resolve_empty_path(self, dag: MerkleDAG) -> None:
        n = MerkleNode("root")
        dag.put(n)
        assert dag.resolve(n.cid, "") is n

    def test_resolve_path_mid_chain_missing(self, dag: MerkleDAG) -> None:
        leaf = MerkleNode("leaf")
        dag.put(leaf)
        root = MerkleNode("root", [MerkleLink("c", leaf.cid, 0)])
        dag.put(root)
        with pytest.raises(PathResolutionError, match=r"link.*not found"):
            dag.resolve(root.cid, "c/gone")


class TestMerkleDAGBulk:
    def test_bulk_import_export(self, dag: MerkleDAG) -> None:
        a = MerkleNode("a")
        dag.put(a)
        b = MerkleNode("b", [MerkleLink("a", a.cid, 0)])
        dag.put(b)
        raw = dag.export_dicts()
        dag2: MerkleDAG[str] = MerkleDAG()
        dag2.import_dicts(raw)
        assert dag2.get(a.cid).data == "a"
        assert dag2.get(b.cid).data == "b"
        assert dag2.get(b.cid).links[0].cid == a.cid

    def test_iter_nodes(self, dag: MerkleDAG) -> None:
        for x in ["a", "b", "c"]:
            dag.put(MerkleNode(x))
        data = sorted(str(n.data) for n in dag.iter_nodes())
        assert data == ["a", "b", "c"]

    def test_root_count(self, dag: MerkleDAG) -> None:
        child = MerkleNode("shared")
        dag.put(child)
        for i in range(3):
            dag.put(MerkleNode(f"root{i}", [MerkleLink("c", child.cid, 0)]))
        assert dag.root_count() == 3

    def test_leaf_count(self, dag: MerkleDAG) -> None:
        dag.put(MerkleNode("a"))
        dag.put(MerkleNode("b"))
        mid = MerkleNode("mid")
        dag.put(mid)
        dag.put(MerkleNode("parent", [MerkleLink("m", mid.cid, 0)]))
        assert dag.leaf_count() == 3
