"""Tests for EntityGraph, EntityNode, and Association."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from general_ludd.entity.graph import Association, EntityGraph, EntityNode


class TestEntityNode:
    def test_creation_defaults(self) -> None:
        node = EntityNode(id="e1", name="Acme Corp")
        assert node.id == "e1"
        assert node.name == "Acme Corp"
        assert node.entity_type == "organization"
        assert node.jurisdiction is None
        assert node.industry is None
        assert node.metadata == {}

    def test_creation_full(self) -> None:
        node = EntityNode(
            id="e2",
            name="TechStart Inc.",
            entity_type="corporation",
            jurisdiction="US-DE",
            industry="Technology",
            metadata={"employees": 500, "founded": 2015},
        )
        assert node.id == "e2"
        assert node.name == "TechStart Inc."
        assert node.entity_type == "corporation"
        assert node.jurisdiction == "US-DE"
        assert node.industry == "Technology"
        assert node.metadata["employees"] == 500
        assert node.metadata["founded"] == 2015

    def test_person_type(self) -> None:
        node = EntityNode(id="p1", name="Jane Smith", entity_type="person")
        assert node.entity_type == "person"

    def test_equality(self) -> None:
        n1 = EntityNode(id="e1", name="Acme")
        n2 = EntityNode(id="e1", name="Acme")
        n3 = EntityNode(id="e2", name="Acme")
        assert n1 == n2
        assert n1 != n3

    def test_hash(self) -> None:
        n1 = EntityNode(id="e1", name="Acme")
        n2 = EntityNode(id="e1", name="Acme")
        nodes_set = {n1, n2}
        assert len(nodes_set) == 1

    def test_immutability(self) -> None:
        node = EntityNode(id="e1", name="Acme")
        with pytest.raises(AttributeError):
            node.id = "new_id"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            node.name = "NewName"  # type: ignore[misc]

    def test_to_dict_minimal(self) -> None:
        node = EntityNode(id="e1", name="Acme Corp")
        d = node.to_dict()
        assert d == {"id": "e1", "name": "Acme Corp", "entity_type": "organization"}

    def test_to_dict_full(self) -> None:
        node = EntityNode(
            id="e2",
            name="Beta LLC",
            entity_type="llc",
            jurisdiction="US-CA",
            industry="Finance",
            metadata={"key": "value"},
        )
        d = node.to_dict()
        assert d["id"] == "e2"
        assert d["jurisdiction"] == "US-CA"
        assert d["industry"] == "Finance"
        assert d["metadata"] == {"key": "value"}

    def test_to_dict_omits_none_values(self) -> None:
        node = EntityNode(id="e1", name="X")
        d = node.to_dict()
        assert "jurisdiction" not in d
        assert "industry" not in d
        assert "metadata" not in d

    def test_to_json(self) -> None:
        node = EntityNode(id="e1", name="Acme", entity_type="organization")
        j = node.to_json()
        data = json.loads(j)
        assert data["id"] == "e1"
        assert data["name"] == "Acme"

    def test_multiple_entity_types(self) -> None:
        for etype in ("organization", "person", "corporation", "llc", "nonprofit", "government"):
            node = EntityNode(id=etype, name=f"{etype} test", entity_type=etype)
            assert node.entity_type == etype


class TestAssociation:
    def test_creation_defaults(self) -> None:
        assoc = Association(source_id="e1", target_id="e2", assoc_type="contractual")
        assert assoc.source_id == "e1"
        assert assoc.target_id == "e2"
        assert assoc.assoc_type == "contractual"
        assert assoc.weight == 1.0
        assert assoc.description is None
        assert assoc.metadata == {}

    def test_creation_full(self) -> None:
        assoc = Association(
            source_id="e1",
            target_id="e2",
            assoc_type="financial",
            weight=0.8,
            description="Series A investor",
            metadata={"amount": 10000000, "date": "2024-01-15"},
        )
        assert assoc.weight == 0.8
        assert assoc.description == "Series A investor"
        assert assoc.metadata["amount"] == 10000000

    def test_equality(self) -> None:
        a1 = Association(source_id="e1", target_id="e2", assoc_type="financial")
        a2 = Association(source_id="e1", target_id="e2", assoc_type="financial")
        a3 = Association(source_id="e1", target_id="e3", assoc_type="financial")
        assert a1 == a2
        assert a1 != a3

    def test_immutability(self) -> None:
        assoc = Association(source_id="e1", target_id="e2", assoc_type="financial")
        with pytest.raises(AttributeError):
            assoc.weight = 0.5  # type: ignore[misc]
        with pytest.raises(AttributeError):
            assoc.assoc_type = "personal"  # type: ignore[misc]

    def test_classify_contractual(self) -> None:
        assert Association.classify_type("vendor contract agreement") == "contractual"
        assert Association.classify_type("partnership deal signed") == "contractual"
        assert Association.classify_type("client engagement letter") == "contractual"

    def test_classify_financial(self) -> None:
        assert Association.classify_type("equity investment round") == "financial"
        assert Association.classify_type("acquisition of shares") == "financial"
        assert Association.classify_type("loan agreement $500k") == "financial"
        assert Association.classify_type("debt financing") == "financial"
        assert Association.classify_type("merger discussion") == "financial"
        assert Association.classify_type("divestiture of assets") == "financial"

    def test_classify_personal(self) -> None:
        assert Association.classify_type("founder relationship") == "personal"
        assert Association.classify_type("employee connection") == "personal"
        assert Association.classify_type("board member advisory") == "personal"
        assert Association.classify_type("executive team") == "personal"
        assert Association.classify_type("family office") == "personal"

    def test_classify_competitive(self) -> None:
        assert Association.classify_type("competitor analysis") == "competitive"
        assert Association.classify_type("rival company") == "competitive"
        assert Association.classify_type("alternative provider") == "competitive"
        assert Association.classify_type("competing products") == "competitive"

    def test_classify_other(self) -> None:
        assert Association.classify_type("general connection") == "other"
        assert Association.classify_type("attended conference together") == "other"

    def test_to_dict(self) -> None:
        assoc = Association(
            source_id="e1",
            target_id="e2",
            assoc_type="financial",
            weight=0.7,
            description="investment",
            metadata={"year": 2023},
        )
        d = assoc.to_dict()
        assert d["source_id"] == "e1"
        assert d["target_id"] == "e2"
        assert d["assoc_type"] == "financial"
        assert d["weight"] == 0.7
        assert d["description"] == "investment"
        assert d["metadata"] == {"year": 2023}

    def test_to_dict_omits_none(self) -> None:
        assoc = Association(source_id="e1", target_id="e2", assoc_type="other")
        d = assoc.to_dict()
        assert "description" not in d
        assert "metadata" not in d

    def test_to_json(self) -> None:
        assoc = Association(source_id="e1", target_id="e2", assoc_type="financial")
        j = assoc.to_json()
        data = json.loads(j)
        assert data["source_id"] == "e1"
        assert data["target_id"] == "e2"
        assert data["assoc_type"] == "financial"


class TestEntityGraphBasic:
    def test_empty_graph(self) -> None:
        g = EntityGraph()
        assert g.node_count == 0
        assert g.edge_count == 0
        assert g.nodes == {}
        assert g.edges == {}

    def test_add_node(self) -> None:
        g = EntityGraph()
        node = EntityNode(id="e1", name="Acme")
        g.add_node(node)
        assert g.node_count == 1
        assert g.has_node("e1")
        assert g.has_node("e2") is False
        assert g.get_node("e1") == node
        assert g.get_node("e2") is None

    def test_add_multiple_nodes(self) -> None:
        g = EntityGraph()
        nodes = [
            EntityNode(id=f"e{i}", name=f"Entity {i}")
            for i in range(5)
        ]
        for n in nodes:
            g.add_node(n)
        assert g.node_count == 5
        for i in range(5):
            assert g.has_node(f"e{i}")

    def test_add_edge_basic(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="Acme"))
        g.add_node(EntityNode(id="e2", name="Beta"))
        assoc = Association(source_id="e1", target_id="e2", assoc_type="contractual")
        g.add_edge(assoc)
        assert g.edge_count == 1

    def test_add_edge_missing_source(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="Acme"))
        assoc = Association(source_id="e99", target_id="e1", assoc_type="other")
        with pytest.raises(ValueError, match="Source node"):
            g.add_edge(assoc)

    def test_add_edge_missing_target(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="Acme"))
        assoc = Association(source_id="e1", target_id="e99", assoc_type="other")
        with pytest.raises(ValueError, match="Target node"):
            g.add_edge(assoc)


class TestEntityGraphQueries:
    def test_find_by_type(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="Org A", entity_type="organization"))
        g.add_node(EntityNode(id="e2", name="Person B", entity_type="person"))
        g.add_node(EntityNode(id="e3", name="Org C", entity_type="organization"))
        orgs = g.find_by_type("organization")
        assert len(orgs) == 2
        assert {n.id for n in orgs} == {"e1", "e3"}

    def test_find_by_type_none(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="A", entity_type="organization"))
        result = g.find_by_type("person")
        assert result == []

    def test_find_by_jurisdiction(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="US Corp", jurisdiction="US-DE"))
        g.add_node(EntityNode(id="e2", name="UK Ltd", jurisdiction="UK"))
        g.add_node(EntityNode(id="e3", name="US LLC", jurisdiction="US-DE"))
        us_entities = g.find_by_jurisdiction("US-DE")
        assert len(us_entities) == 2
        uk_entities = g.find_by_jurisdiction("UK")
        assert len(uk_entities) == 1

    def test_find_by_industry(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="TechCo", industry="Technology"))
        g.add_node(EntityNode(id="e2", name="Bank", industry="Finance"))
        g.add_node(EntityNode(id="e3", name="Startup", industry="Technology"))
        tech = g.find_by_industry("Technology")
        assert len(tech) == 2
        assert g.find_by_industry("Healthcare") == []

    def test_find_by_jurisdiction_none_matches(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="A"))
        assert g.find_by_jurisdiction("DE") == []

    def test_find_by_industry_none_matches(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="A"))
        assert g.find_by_industry("Unknown") == []


class TestEntityGraphTraversal:
    def _build_triangle(self) -> EntityGraph:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_node(EntityNode(id="c", name="C"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="a", assoc_type="other"))
        return g

    def test_get_related_depth_1(self) -> None:
        g = self._build_triangle()
        related = g.get_related("a", max_depth=1)
        assert "depth_1" in related
        assert sorted(related["depth_1"]) == ["b", "c"]

    def test_get_related_depth_2(self) -> None:
        g = self._build_triangle()
        related = g.get_related("a", max_depth=2)
        assert "depth_1" in related
        assert sorted(related["depth_1"]) == ["b", "c"]
        assert "depth_2" not in related

    def test_get_related_depth_3_chain(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_node(EntityNode(id="c", name="C"))
        g.add_node(EntityNode(id="d", name="D"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="d", assoc_type="other"))
        related = g.get_related("a", max_depth=3)
        assert related["depth_1"] == ["b"]
        assert related["depth_2"] == ["c"]
        assert related["depth_3"] == ["d"]

    def test_get_related_branching(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_node(EntityNode(id="c", name="C"))
        g.add_node(EntityNode(id="d", name="D"))
        g.add_node(EntityNode(id="e", name="E"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="a", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="d", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="e", assoc_type="other"))
        related = g.get_related("a", max_depth=2)
        assert sorted(related["depth_1"]) == ["b", "c"]
        assert sorted(related["depth_2"]) == ["d", "e"]

    def test_get_related_missing_node(self) -> None:
        g = EntityGraph()
        assert g.get_related("nonexistent") == {}

    def test_get_related_max_depth_zero(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        related = g.get_related("a", max_depth=0)
        assert related == {}

    def test_find_path_direct(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        path = g.find_path("a", "b")
        assert path == ["a", "b"]

    def test_find_path_multi_hop(self) -> None:
        g = EntityGraph()
        for nid in ("a", "b", "c", "d"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="d", assoc_type="other"))
        path = g.find_path("a", "d")
        assert path == ["a", "b", "c", "d"]

    def test_find_path_no_path(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        path = g.find_path("a", "b")
        assert path is None

    def test_find_path_self(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        path = g.find_path("a", "a")
        assert path == ["a"]

    def test_find_path_missing_node(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        assert g.find_path("a", "missing") is None
        assert g.find_path("missing", "a") is None

    def test_find_path_prefers_shortest(self) -> None:
        g = EntityGraph()
        for nid in ("a", "b", "c", "d", "e"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="e", assoc_type="other"))
        g.add_edge(Association(source_id="a", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="d", assoc_type="other"))
        g.add_edge(Association(source_id="d", target_id="e", assoc_type="other"))
        path = g.find_path("a", "e")
        assert path == ["a", "b", "e"]


class TestEntityGraphClusters:
    def test_single_node_cluster(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        clusters = g.detect_clusters()
        assert clusters == [["a"]]

    def test_two_disconnected_clusters(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_node(EntityNode(id="c", name="C"))
        g.add_node(EntityNode(id="d", name="D"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="d", assoc_type="other"))
        clusters = g.detect_clusters()
        assert len(clusters) == 2
        cluster_ids = [sorted(c) for c in clusters]
        assert ["a", "b"] in cluster_ids
        assert ["c", "d"] in cluster_ids

    def test_three_clusters(self) -> None:
        g = EntityGraph()
        for nid in ("a", "b", "c", "d", "e", "f"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="d", assoc_type="other"))
        g.add_edge(Association(source_id="e", target_id="f", assoc_type="other"))
        clusters = g.detect_clusters()
        assert len(clusters) == 3

    def test_empty_graph_clusters(self) -> None:
        g = EntityGraph()
        assert g.detect_clusters() == []

    def test_fully_connected_cluster(self) -> None:
        g = EntityGraph()
        for nid in ("a", "b", "c"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="a", target_id="c", assoc_type="other"))
        clusters = g.detect_clusters()
        assert len(clusters) == 1
        assert sorted(clusters[0]) == ["a", "b", "c"]


class TestEntityGraphSerialization:
    def test_to_dict_empty(self) -> None:
        g = EntityGraph()
        d = g.to_dict()
        assert d == {"nodes": [], "edges": []}

    def test_to_dict_with_data(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="Acme", entity_type="organization"))
        g.add_node(EntityNode(id="e2", name="Beta"))
        g.add_edge(Association(source_id="e1", target_id="e2", assoc_type="contractual"))
        d = g.to_dict()
        assert len(d["nodes"]) == 2
        assert isinstance(d["nodes"], list)
        assert isinstance(d["nodes"][0], dict)
        assert len(d["edges"]) == 1

    def test_to_json(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="Acme"))
        g.add_node(EntityNode(id="e2", name="Beta"))
        g.add_edge(Association(source_id="e1", target_id="e2", assoc_type="other"))
        j = g.to_json()
        data = json.loads(j)
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1

    def test_from_dict_roundtrip(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A", entity_type="organization", jurisdiction="US"))
        g.add_node(EntityNode(id="b", name="B", entity_type="person"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="personal", weight=0.5))
        data = g.to_dict()
        g2 = EntityGraph.from_dict(data)
        assert g2.node_count == g.node_count
        assert g2.edge_count == g.edge_count
        for nid in ("a", "b"):
            assert g2.has_node(nid)
            n1 = g.get_node(nid)
            n2 = g2.get_node(nid)
            assert n1 is not None
            assert n2 is not None
            assert n1.id == n2.id
            assert n1.name == n2.name
            assert n1.entity_type == n2.entity_type

    def test_from_json_roundtrip(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="x", name="X Corp", industry="Tech"))
        g.add_node(EntityNode(id="y", name="Y LLC"))
        g.add_edge(Association(source_id="x", target_id="y", assoc_type="financial", description="investor"))
        json_str = g.to_json()
        g2 = EntityGraph.from_json(json_str)
        assert g2.node_count == 2
        assert g2.edge_count == 1
        assert g2.to_dict() == g.to_dict()

    def test_serialize_deserialize_preserves_metadata(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="MetaCo", metadata={"source": "searx", "confidence": 0.95}))
        g.add_node(EntityNode(id="e2", name="SubCo"))
        g.add_edge(Association(
            source_id="e1", target_id="e2", assoc_type="financial",
            metadata={"date": "2024-06-01"}, description="acquired"
        ))
        j = g.to_json()
        g2 = EntityGraph.from_json(j)
        n1 = g2.get_node("e1")
        assert n1 is not None
        assert n1.metadata["source"] == "searx"
        assert n1.metadata["confidence"] == 0.95

    def test_to_and_from_json_file(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="e1", name="TestCo"))
        g.add_node(EntityNode(id="e2", name="PartnerCo"))
        g.add_edge(Association(source_id="e1", target_id="e2", assoc_type="contractual"))
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(g.to_json())
            temp_path = f.name
        try:
            loaded = EntityGraph.from_json(Path(temp_path).read_text())
            assert loaded.node_count == 2
            assert loaded.edge_count == 1
        finally:
            Path(temp_path).unlink()


class TestEntityGraphEdgeCases:
    def test_circular_relationships(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        g.add_node(EntityNode(id="c", name="C"))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="a", assoc_type="other"))
        assert g.edge_count == 3
        related = g.get_related("a", max_depth=2)
        assert "depth_1" in related
        path = g.find_path("a", "a")
        assert path == ["a"]

    def test_duplicate_edges(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_node(EntityNode(id="b", name="B"))
        a1 = Association(source_id="a", target_id="b", assoc_type="contractual")
        a2 = Association(source_id="a", target_id="b", assoc_type="financial")
        g.add_edge(a1)
        g.add_edge(a2)
        assert g.edge_count == 1

    def test_self_referential_edge(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="A"))
        g.add_edge(Association(source_id="a", target_id="a", assoc_type="personal"))
        related = g.get_related("a", max_depth=1)
        assert "depth_1" not in related

    def test_empty_graph_operations(self) -> None:
        g = EntityGraph()
        assert g.node_count == 0
        assert g.edge_count == 0
        assert g.get_related("none") == {}
        assert g.find_path("a", "b") is None
        assert g.detect_clusters() == []
        assert g.to_dict() == {"nodes": [], "edges": []}
        assert g.find_by_type("any") == []

    def test_large_node_properties(self) -> None:
        g = EntityGraph()
        large_name = "X" * 1000
        node = EntityNode(
            id="big",
            name=large_name,
            metadata={"data": "y" * 10000},
        )
        g.add_node(node)
        retrieved = g.get_node("big")
        assert retrieved is not None
        assert retrieved.name == large_name

    def test_disconnected_node_queries(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="iso", name="Isolated", entity_type="person", jurisdiction="JP"))
        assert len(g.find_by_type("person")) == 1
        assert len(g.find_by_jurisdiction("JP")) == 1
        assert g.get_related("iso", max_depth=5) == {}
        assert g.find_path("iso", "iso") == ["iso"]


class TestEntityGraphStress:
    def test_large_graph_100_nodes_500_edges(self) -> None:
        g = EntityGraph()
        for i in range(100):
            g.add_node(EntityNode(
                id=f"n{i}",
                name=f"Entity {i}",
                entity_type="organization" if i % 2 == 0 else "person",
                jurisdiction=f"J{i % 5}",
                industry=f"Ind{i % 10}",
            ))
        edge_count = 0
        for i in range(100):
            for j in range(i + 1, min(i + 6, 100)):
                g.add_edge(Association(
                    source_id=f"n{i}",
                    target_id=f"n{j}",
                    assoc_type=["contractual", "financial", "personal", "competitive"][(i + j) % 4],
                    weight=0.5,
                ))
                edge_count += 1
                if edge_count >= 500:
                    break
            if edge_count >= 500:
                break
        assert g.node_count == 100
        assert g.edge_count <= 500

        related = g.get_related("n0", max_depth=3)
        assert len(related) > 0

        path = g.find_path("n0", "n99")
        assert path is not None
        assert len(path) <= 25

        clusters = g.detect_clusters()
        assert len(clusters) == 1

        json_data = g.to_json()
        assert len(json_data) > 100

        types = g.find_by_type("organization")
        assert len(types) == 50

    def test_large_graph_bipartite(self) -> None:
        g = EntityGraph()
        for i in range(50):
            g.add_node(EntityNode(id=f"org{i}", name=f"Org {i}", entity_type="organization"))
            g.add_node(EntityNode(id=f"person{i}", name=f"Person {i}", entity_type="person"))
        for i in range(50):
            g.add_edge(Association(source_id=f"org{i}", target_id=f"person{i}", assoc_type="personal"))
        assert g.node_count == 100
        assert g.edge_count == 50
        clusters = g.detect_clusters()
        assert len(clusters) == 50
