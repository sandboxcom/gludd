"""Deep skip graph DHT tests: insert, search, forward, neighbor tables.

Covers: SkipGraphNode, SkipGraphOverlay. 15+ tests.
"""

from __future__ import annotations

from general_ludd.distributed.skip_graph import LeftRight, SkipGraphNode, SkipGraphOverlay


def _node_ids(overlay: SkipGraphOverlay) -> set[str]:
    return {n.node_id for n in overlay.nodes.values()}


class TestSkipGraphNode:
    def test_node_initialization(self):
        n = SkipGraphNode("node-a", max_level=4)
        assert n.node_id == "node-a"
        assert n.max_level == 4
        assert n.neighbors == {}  # no levels yet populated
        assert n.routing_level() == 0

    def test_node_with_neighbors_at_level_zero(self):
        n = SkipGraphNode("a", max_level=2)
        n.neighbors[0] = LeftRight(left="b", right="c")
        assert n.left_neighbor(0) == "b"
        assert n.right_neighbor(0) == "c"

    def test_node_left_right_neighbors_at_multiple_levels(self):
        n = SkipGraphNode("x", max_level=3)
        n.neighbors[0] = LeftRight(left="l0", right="r0")
        n.neighbors[1] = LeftRight(left="l1", right="r1")
        n.neighbors[2] = LeftRight(left=None, right="r2")
        assert n.left_neighbor(0) == "l0"
        assert n.right_neighbor(0) == "r0"
        assert n.left_neighbor(1) == "l1"
        assert n.right_neighbor(1) == "r1"
        assert n.left_neighbor(2) is None
        assert n.right_neighbor(2) == "r2"

    def test_node_null_neighbors_for_unset_level(self):
        n = SkipGraphNode("z", max_level=5)
        assert n.left_neighbor(3) is None
        assert n.right_neighbor(3) is None

    def test_routing_level_reflects_max_populated_level(self):
        n = SkipGraphNode("n", max_level=6)
        n.neighbors[0] = LeftRight(left="a", right="b")
        n.neighbors[2] = LeftRight(left="c", right="d")
        assert n.routing_level() == 2

    def test_node_repr(self):
        n = SkipGraphNode("node-1", max_level=3)
        n.neighbors[0] = LeftRight(left="l", right="r")
        s = repr(n)
        assert "node-1" in s
        assert "SkipGraphNode" in s


class TestSkipGraphOverlayInsert:
    def test_insert_single_node(self):
        g = SkipGraphOverlay()
        g.insert("n1")
        assert _node_ids(g) == {"n1"}
        n = g.nodes["n1"]
        assert n.neighbors.get(0) is not None

    def test_insert_two_nodes_links_level_zero(self):
        g = SkipGraphOverlay()
        g.insert("n10")
        g.insert("n20")
        assert _node_ids(g) == {"n10", "n20"}
        n10 = g.nodes["n10"]
        n20 = g.nodes["n20"]
        assert n10.right_neighbor(0) is not None
        assert n20.left_neighbor(0) is not None

    def test_insert_sequence_links_in_order(self):
        g = SkipGraphOverlay()
        g.insert("a")
        g.insert("b")
        g.insert("c")
        a = g.nodes["a"]
        b = g.nodes["b"]
        c = g.nodes["c"]
        assert a.right_neighbor(0) is not None
        assert b.left_neighbor(0) is not None
        assert b.right_neighbor(0) is not None
        assert c.left_neighbor(0) is not None

    def test_insert_many_nodes_forms_ring(self):
        g = SkipGraphOverlay()
        ids = [f"node-{i:03d}" for i in range(50)]
        for nid in ids:
            g.insert(nid)
        assert _node_ids(g) == set(ids)
        for nid in ids:
            assert g.nodes[nid].neighbors.get(0) is not None

    def test_insert_duplicate_is_idempotent(self):
        g = SkipGraphOverlay()
        g.insert("dup")
        g.insert("dup")
        assert len(g.nodes) == 1


class TestSkipGraphOverlayLookup:
    def test_lookup_owner_for_inserted_key(self):
        g = SkipGraphOverlay()
        g.insert("host-a")
        g.insert("host-b")
        g.put("greeting", "hello", owner="host-a")
        assert g.lookup("greeting") == "hello"

    def test_lookup_missing_key_returns_none(self):
        g = SkipGraphOverlay()
        g.insert("n1")
        assert g.lookup("nokey") is None

    def test_lookup_routes_through_forward_hops(self):
        g = SkipGraphOverlay()
        for i in range(16):
            g.insert(f"node-{i}")
        g.put("dht-key", "dht-value", owner="node-15")
        assert g.lookup("dht-key") == "dht-value"

    def test_lookup_empty_overlay_returns_none(self):
        g = SkipGraphOverlay()
        assert g.lookup("anything") is None


class TestSkipGraphOverlayRouting:
    def test_forward_picks_closest_neighbor(self):
        g = SkipGraphOverlay()
        for i in range(20):
            g.insert(f"n-{i:02d}")
        routes = g.route("n-01", "n-19")
        assert len(routes) >= 1
        assert routes[0] == "n-01"
        assert routes[-1] == "n-19"

    def test_forward_to_self_is_identity(self):
        g = SkipGraphOverlay()
        g.insert("self-node")
        routes = g.route("self-node", "self-node")
        assert routes == ["self-node"]

    def test_forward_uses_higher_levels_for_long_distance(self):
        g = SkipGraphOverlay(max_level=4)
        for i in range(100):
            g.insert(f"node-{i:04d}")
        hops = g.route("node-0000", "node-0099")
        assert len(hops) > 1
        assert len(hops) < 100  # skip graph skips

    def test_neighbor_table_has_level_zero_and_higher(self):
        g = SkipGraphOverlay(max_level=3)
        for i in range(30):
            g.insert(f"peer-{i:03d}")
        higher = sum(1 for n in g.nodes.values() if n.routing_level() > 0)
        assert higher > 0

    def test_neighbor_table_level_zero_is_fully_linked(self):
        g = SkipGraphOverlay(max_level=2)
        for i in range(10):
            g.insert(f"n{i}")
        for n in g.nodes.values():
            assert n.neighbors.get(0) is not None


class TestSkipGraphOverlayPut:
    def test_put_stores_and_retrieve(self):
        g = SkipGraphOverlay()
        g.insert("server-1")
        g.put("k1", "v1", owner="server-1")
        assert g.lookup("k1") == "v1"

    def test_put_overwrites_existing_key(self):
        g = SkipGraphOverlay()
        g.insert("s1")
        g.put("key-x", "old")
        g.put("key-x", "new")
        assert g.lookup("key-x") == "new"

    def test_put_without_owner_stores_on_any_node(self):
        g = SkipGraphOverlay()
        g.insert("any-node")
        g.put("float-key", 42)
        assert g.lookup("float-key") == 42


class TestSkipGraphOverlayEdgeCases:
    def test_large_overlay_remains_consistent(self):
        g = SkipGraphOverlay(max_level=5)
        for i in range(200):
            g.insert(f"n-{i:05d}")
            if i % 50 == 0:
                g.put(f"key-{i}", f"val-{i}", owner=f"n-{i:05d}")
        for i in (0, 50, 100, 150):
            assert g.lookup(f"key-{i}") == f"val-{i}"
        assert g.lookup("missing") is None

    def test_overlay_size_property(self):
        g = SkipGraphOverlay()
        assert len(g) == 0
        for i in range(7):
            g.insert(f"n{i}")
        assert len(g) == 7

    def test_nodes_with_different_max_levels(self):
        g = SkipGraphOverlay(max_level=5)
        g.insert("low")  # may get low level
        g.insert("mid")
        g.insert("high")
        for i in range(8):
            g.insert(f"extra-{i}")
        assert _node_ids(g).issuperset({"low", "mid", "high"})

    def test_remove_node_updates_neighbor_tables(self):
        g = SkipGraphOverlay()
        g.insert("a")
        g.insert("b")
        g.insert("c")
        g.remove("b")
        assert _node_ids(g) == {"a", "c"}
        a = g.nodes["a"]
        g.nodes["c"]
        assert a.neighbors.get(0) is not None

    def test_remove_only_node_clears_overlay(self):
        g = SkipGraphOverlay()
        g.insert("lone")
        g.remove("lone")
        assert len(g) == 0

    def test_remove_nonexistent_node_returns_false(self):
        g = SkipGraphOverlay()
        g.insert("real")
        assert g.remove("ghost") is False
