"""Tests for general_ludd.business.entity_graph — EntityNode, Association, EntityGraph."""

from __future__ import annotations

import json
import os

from general_ludd.business.entity_graph import (
    Association,
    EntityGraph,
    EntityNode,
    build_graph,
    find_related,
)


class TestEntityNode:
    def test_defaults(self) -> None:
        node = EntityNode(name="Acme Corp")
        assert node.name == "Acme Corp"
        assert node.entity_type == "organization"
        assert node.jurisdiction == ""
        assert node.industry == ""
        assert node.metadata == {}

    def test_full(self) -> None:
        node = EntityNode(
            name="TechStart Inc.",
            entity_type="corporation",
            jurisdiction="US-DE",
            industry="Technology",
            metadata={"employees": 500, "founded": 2015},
        )
        assert node.name == "TechStart Inc."
        assert node.entity_type == "corporation"
        assert node.jurisdiction == "US-DE"
        assert node.industry == "Technology"
        assert node.metadata["employees"] == 500

    def test_equality(self) -> None:
        n1 = EntityNode(name="Acme")
        n2 = EntityNode(name="Acme")
        n3 = EntityNode(name="Beta")
        assert n1 == n2
        assert n1 != n3
        assert n1 == EntityNode(name="Acme", entity_type="different")
        assert n1 != EntityNode(name="NotAcme")

    def test_hash(self) -> None:
        n1 = EntityNode(name="Acme")
        n2 = EntityNode(name="Acme")
        s = {n1, n2}
        assert len(s) == 1

    def test_not_equal_different_type(self) -> None:
        n1 = EntityNode(name="Acme")
        assert n1 != "Acme"
        assert n1 != 42


class TestAssociation:
    def test_defaults(self) -> None:
        a = Association(from_node="A", to_node="B", assoc_type="contractual")
        assert a.from_node == "A"
        assert a.to_node == "B"
        assert a.assoc_type == "contractual"
        assert a.strength == 0.5
        assert a.evidence == []

    def test_full(self) -> None:
        a = Association(
            from_node="A",
            to_node="B",
            assoc_type="financial",
            strength=0.9,
            evidence=["sec_filing", "crunchbase"],
        )
        assert a.strength == 0.9
        assert len(a.evidence) == 2

    def test_classifications(self) -> None:
        tests = {
            "parent_company": "financial",
            "subsidiary": "financial",
            "board_member": "personal",
            "executive": "personal",
            "shareholder": "financial",
            "investor": "financial",
            "supplier": "contractual",
            "partner": "contractual",
            "competitor": "competitive",
            "acquirer": "financial",
            "joint_venture": "contractual",
            "founder": "personal",
            "unknown_type": "unknown",
        }
        for atype, expected in tests.items():
            a = Association(from_node="A", to_node="B", assoc_type=atype)
            assert a.classification == expected, f"{atype} -> {expected}"

    def test_to_dict(self) -> None:
        a = Association(
            from_node="A",
            to_node="B",
            assoc_type="investor",
            strength=0.8,
            evidence=["source1"],
        )
        d = a.to_dict()
        assert d["from"] == "A"
        assert d["to"] == "B"
        assert d["type"] == "investor"
        assert d["classification"] == "financial"
        assert d["strength"] == 0.8
        assert d["evidence"] == ["source1"]


class TestEntityGraphBasic:
    def test_empty(self) -> None:
        g = EntityGraph()
        assert g.node_count == 0
        assert g.edge_count == 0
        assert g.nodes == {}
        assert g.associations == []

    def test_add_node(self) -> None:
        g = EntityGraph()
        n = EntityNode(name="Acme")
        g.add_node(n)
        assert g.node_count == 1
        assert g.get_node("Acme") is not None
        assert g.get_node("Acme").name == "Acme"

    def test_add_nodes_batch(self) -> None:
        g = EntityGraph()
        nodes = [EntityNode(name=f"E{i}") for i in range(5)]
        g.add_nodes(nodes)
        assert g.node_count == 5

    def test_add_association_creates_missing_nodes(self) -> None:
        g = EntityGraph()
        a = Association(from_node="A", to_node="B", assoc_type="contractual")
        g.add_association(a)
        assert g.node_count == 2
        assert g.edge_count == 1

    def test_add_association_existing_nodes(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(name="A"))
        g.add_node(EntityNode(name="B"))
        a = Association(from_node="A", to_node="B", assoc_type="contractual")
        g.add_association(a)
        assert g.edge_count == 1

    def test_add_multiple_associations(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(name="A"))
        g.add_node(EntityNode(name="B"))
        g.add_node(EntityNode(name="C"))
        assocs = [
            Association(from_node="A", to_node="B", assoc_type="contractual"),
            Association(from_node="B", to_node="C", assoc_type="financial"),
            Association(from_node="A", to_node="C", assoc_type="competitive"),
        ]
        g.add_associations(assocs)
        assert g.edge_count == 3


class TestEntityGraphQueries:
    def _build_graph(self) -> EntityGraph:
        g = EntityGraph()
        g.add_nodes([
            EntityNode(name="Acme", entity_type="corporation", industry="Tech"),
            EntityNode(name="Beta", entity_type="llc", industry="Finance"),
            EntityNode(name="Gamma", entity_type="nonprofit", industry="Healthcare"),
        ])
        g.add_associations([
            Association("Acme", "Beta", "subsidiary", strength=1.0),
            Association("Acme", "Gamma", "partner", strength=0.7),
        ])
        return g

    def test_get_node(self) -> None:
        g = self._build_graph()
        assert g.get_node("Acme") is not None
        assert g.get_node("Nonexistent") is None

    def test_get_associations(self) -> None:
        g = self._build_graph()
        acme_assocs = g.get_associations("Acme")
        assert len(acme_assocs) == 2
        names = {a.to_node for a in acme_assocs}
        assert names == {"Beta", "Gamma"}

    def test_get_associations_by_type(self) -> None:
        g = self._build_graph()
        subs = g.get_associations_by_type("subsidiary")
        assert len(subs) == 1
        assert subs[0].from_node == "Acme"
        assert subs[0].to_node == "Beta"

    def test_get_associations_by_classification(self) -> None:
        g = self._build_graph()
        financial = g.get_associations_by_classification("financial")
        assert len(financial) == 1
        contractual = g.get_associations_by_classification("contractual")
        assert len(contractual) == 1

    def test_get_degree(self) -> None:
        g = self._build_graph()
        assert g.get_degree("Acme") == 2
        assert g.get_degree("Beta") == 0

    def test_get_hub_entities(self) -> None:
        g = self._build_graph()
        hubs = g.get_hub_entities(top_n=2)
        assert len(hubs) == 2
        assert hubs[0][0] == "Acme"
        assert hubs[0][1] == 2


class TestEntityGraphTraversal:
    def _build_chain(self) -> EntityGraph:
        g = EntityGraph()
        g.add_nodes([
            EntityNode(name="A"),
            EntityNode(name="B"),
            EntityNode(name="C"),
            EntityNode(name="D"),
        ])
        g.add_associations([
            Association("A", "B", "other"),
            Association("B", "C", "other"),
            Association("C", "D", "other"),
        ])
        return g

    def _build_branching(self) -> EntityGraph:
        g = EntityGraph()
        g.add_nodes([
            EntityNode(name="Root"),
            EntityNode(name="Left"),
            EntityNode(name="Right"),
            EntityNode(name="L2"),
            EntityNode(name="R2"),
        ])
        g.add_associations([
            Association("Root", "Left", "other"),
            Association("Root", "Right", "other"),
            Association("Left", "L2", "other"),
            Association("Right", "R2", "other"),
        ])
        return g

    def test_find_related_depth_1(self) -> None:
        g = self._build_chain()
        results = g.find_related("A", max_depth=1)
        assert len(results) == 1
        assert results[0].to_node == "B"

    def test_find_related_depth_3(self) -> None:
        g = self._build_chain()
        results = g.find_related("A", max_depth=3)
        found = {a.to_node for a in results}
        assert found == {"B", "C", "D"}

    def test_find_related_branching(self) -> None:
        g = self._build_branching()
        results = g.find_related("Root", max_depth=2)
        found = {a.to_node for a in results}
        assert "Left" in found
        assert "Right" in found
        assert "L2" in found
        assert "R2" in found
        assert len(results) == 4

    def test_find_related_missing(self) -> None:
        g = self._build_chain()
        assert g.find_related("Missing") == []

    def test_find_related_max_depth_0(self) -> None:
        g = self._build_chain()
        assert g.find_related("A", max_depth=0) == []

    def test_find_paths_direct(self) -> None:
        g = EntityGraph()
        g.add_nodes([EntityNode(name="A"), EntityNode(name="B")])
        g.add_association(Association("A", "B", "other"))
        paths = g.find_paths("A", "B")
        assert len(paths) == 1
        assert len(paths[0]) == 1
        assert paths[0][0].from_node == "A"
        assert paths[0][0].to_node == "B"

    def test_find_paths_multi_hop(self) -> None:
        g = self._build_chain()
        paths = g.find_paths("A", "D", max_depth=5)
        assert len(paths) == 1
        assert len(paths[0]) == 3

    def test_find_paths_no_path(self) -> None:
        g = EntityGraph()
        g.add_nodes([EntityNode(name="A"), EntityNode(name="B")])
        assert g.find_paths("A", "B") == []

    def test_find_paths_missing_nodes(self) -> None:
        g = self._build_chain()
        assert g.find_paths("A", "Missing") == []


class TestEntityGraphClusters:
    def test_fully_connected(self) -> None:
        g = EntityGraph()
        g.add_nodes([
            EntityNode(name="A"), EntityNode(name="B"), EntityNode(name="C"),
        ])
        g.add_associations([
            Association("A", "B", "other"),
            Association("B", "C", "other"),
            Association("A", "C", "other"),
        ])
        clusters = g.detect_clusters(min_size=2)
        assert len(clusters) == 1
        assert clusters[0] == {"A", "B", "C"}

    def test_two_disconnected(self) -> None:
        g = EntityGraph()
        g.add_nodes([
            EntityNode(name="A"), EntityNode(name="B"),
            EntityNode(name="X"), EntityNode(name="Y"),
        ])
        g.add_associations([
            Association("A", "B", "other"),
            Association("X", "Y", "other"),
        ])
        clusters = g.detect_clusters(min_size=2)
        assert len(clusters) == 2

    def test_min_size_filter(self) -> None:
        g = EntityGraph()
        g.add_nodes([
            EntityNode(name="A"), EntityNode(name="B"), EntityNode(name="C"),
        ])
        g.add_associations([
            Association("A", "B", "other"),
        ])
        clusters_2 = g.detect_clusters(min_size=2)
        assert len(clusters_2) == 1
        clusters_3 = g.detect_clusters(min_size=3)
        assert len(clusters_3) == 0

    def test_one_isolated_one_cluster(self) -> None:
        g = EntityGraph()
        g.add_nodes([
            EntityNode(name="A"), EntityNode(name="B"),
            EntityNode(name="Isolated"),
        ])
        g.add_association(Association("A", "B", "other"))
        clusters = g.detect_clusters(min_size=1)
        assert len(clusters) == 2


class TestEntityGraphSerialization:
    def test_to_dict_empty(self) -> None:
        g = EntityGraph()
        d = g.to_dict()
        assert d == {"nodes": [], "associations": []}

    def test_to_dict_with_data(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(
            name="Acme", entity_type="corporation",
            jurisdiction="US-DE", industry="Tech",
        ))
        g.add_node(EntityNode(name="Beta"))
        g.add_association(Association("Acme", "Beta", "subsidiary", strength=1.0))
        d = g.to_dict()
        assert len(d["nodes"]) == 2
        assert len(d["associations"]) == 1
        assert d["nodes"][0]["name"] == "Acme"
        assert d["nodes"][1]["name"] == "Beta"
        assert d["associations"][0]["type"] == "subsidiary"

    def test_to_json_file(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(name="TestCo"))
        g.add_node(EntityNode(name="Partner"))
        g.add_association(Association("TestCo", "Partner", "contractual"))
        path = "/tmp/test_entity_graph.json"
        try:
            g.to_json(path)
            with open(path) as f:
                data = json.load(f)
            assert len(data["nodes"]) == 2
            assert len(data["associations"]) == 1
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_to_dot(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(name="A", entity_type="corporation"))
        g.add_node(EntityNode(name="B", entity_type="llc"))
        g.add_association(Association("A", "B", "subsidiary", strength=0.9))
        path = "/tmp/test_entity_graph.dot"
        try:
            g.to_dot(path)
            with open(path) as f:
                content = f.read()
            assert "digraph EntityGraph" in content
            assert '"A" -> "B"' in content
            assert 'label="subsidiary"' in content
        finally:
            if os.path.exists(path):
                os.remove(path)


class TestBuildGraph:
    def test_build_graph(self) -> None:
        entities = [
            EntityNode(name="A"), EntityNode(name="B"), EntityNode(name="C"),
        ]
        associations = [
            Association("A", "B", "contractual"),
            Association("B", "C", "financial"),
        ]
        g = build_graph(entities, associations)
        assert g.node_count == 3
        assert g.edge_count == 2

    def test_find_related_helper(self) -> None:
        g = EntityGraph()
        g.add_nodes([EntityNode(name="A"), EntityNode(name="B")])
        g.add_association(Association("A", "B", "other"))
        results = find_related(g, "A", max_depth=1)
        assert len(results) == 1
        assert results[0].to_node == "B"


class TestEdgeCases:
    def test_circular(self) -> None:
        g = EntityGraph()
        g.add_nodes([EntityNode(name="A"), EntityNode(name="B")])
        g.add_associations([
            Association("A", "B", "other"),
            Association("B", "A", "other"),
        ])
        results = g.find_related("A", max_depth=3)
        assert len(results) == 2

    def test_self_referential(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(name="A"))
        g.add_association(Association("A", "A", "personal"))
        results = g.find_related("A", max_depth=2)
        assert len(results) == 1

    def test_large_node_name(self) -> None:
        name = "X" * 1000
        g = EntityGraph()
        g.add_node(EntityNode(name=name))
        assert g.get_node(name) is not None

    def test_many_associations(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(name="Hub"))
        for i in range(50):
            g.add_association(Association("Hub", f"Node{i}", "other"))
        assert g.node_count == 51
        assert g.edge_count == 50
        assert g.get_degree("Hub") == 50
