"""Integration tests for the entity research system."""

from __future__ import annotations

import tempfile
from pathlib import Path

from general_ludd.entity.graph import Association, EntityGraph, EntityNode
from general_ludd.entity.research_patterns import (
    research_entity,
)


class TestEntityGraphRoundTrip:
    def test_build_from_json_save_and_reload(self) -> None:
        g = EntityGraph()
        for i in range(20):
            g.add_node(EntityNode(
                id=f"n{i}",
                name=f"Entity {i}",
                entity_type="organization" if i % 3 == 0 else "person",
                jurisdiction=f"J{i % 4}",
                industry=f"Ind{i % 7}",
                metadata={"source": "search", "rank": i} if i % 2 == 0 else {},
            ))
        for i in range(30):
            src = f"n{i % 20}"
            tgt = f"n{(i + 3) % 20}"
            if src != tgt:
                g.add_edge(Association(
                    source_id=src,
                    target_id=tgt,
                    assoc_type=["contractual", "financial", "personal"][i % 3],
                    weight=0.1 * (i % 10 + 1),
                    description=f"Relationship {i}",
                    metadata={"confidence": 0.8},
                ))

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(g.to_json())
            temp_path = f.name

        try:
            loaded = EntityGraph.from_json(Path(temp_path).read_text())

            assert loaded.node_count == g.node_count
            assert loaded.edge_count == g.edge_count

            for node_id in g.nodes:
                orig = g.get_node(node_id)
                loaded_node = loaded.get_node(node_id)
                assert orig is not None
                assert loaded_node is not None
                assert orig.id == loaded_node.id
                assert orig.name == loaded_node.name
                assert orig.entity_type == loaded_node.entity_type
                assert orig.jurisdiction == loaded_node.jurisdiction
                assert orig.industry == loaded_node.industry

            for (src, tgt), orig_edge in g.edges.items():
                loaded_edge = loaded.edges.get((src, tgt))
                assert loaded_edge is not None
                assert orig_edge.assoc_type == loaded_edge.assoc_type
                assert orig_edge.weight == loaded_edge.weight
        finally:
            Path(temp_path).unlink()

    def test_graph_clusters_preserved_through_roundtrip(self) -> None:
        g = EntityGraph()
        for nid in ("a1", "a2", "b1", "b2", "b3"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a1", target_id="a2", assoc_type="other"))
        g.add_edge(Association(source_id="b1", target_id="b2", assoc_type="other"))
        g.add_edge(Association(source_id="b2", target_id="b3", assoc_type="other"))

        clusters_before = g.detect_clusters()
        assert len(clusters_before) == 2

        reloaded = EntityGraph.from_json(g.to_json())
        clusters_after = reloaded.detect_clusters()
        assert len(clusters_after) == 2

    def test_traversal_preserved_through_roundtrip(self) -> None:
        g = EntityGraph()
        for nid in ("a", "b", "c", "d"):
            g.add_node(EntityNode(id=nid, name=nid.upper()))
        g.add_edge(Association(source_id="a", target_id="b", assoc_type="other"))
        g.add_edge(Association(source_id="b", target_id="c", assoc_type="other"))
        g.add_edge(Association(source_id="c", target_id="d", assoc_type="other"))

        path_before = g.find_path("a", "d")

        reloaded = EntityGraph.from_json(g.to_json())
        path_after = reloaded.find_path("a", "d")

        assert path_before == path_after == ["a", "b", "c", "d"]


class TestResearchEntityEndToEnd:
    def test_research_pipeline_on_report_text(self) -> None:
        report_text = (
            "=== Entity Research Report ===\n"
            "Entity: OpenAI\n"
            "Website: openai.com\n"
            "SEC CIK: 0001734954\n"
            "Form 10-K filed\n"
            "Headquarters IP: 13.107.42.14\n"
            "Funding: Series B $1B round led by Microsoft\n"
            "In 2023, OpenAI acquired by Microsoft (rumor)\n"
            "UK entity: Company No. 12345678\n"
        )
        result = research_entity(report_text)

        assert any(d.domain == "openai.com" for d in result.domains)
        assert any(f.cik == "0001734954" for f in result.sec_filings)
        assert any(f.form_type == "10-K" for f in result.sec_filings)
        assert any(ip.address == "13.107.42.14" for ip in result.ip_addresses)
        assert any(r.round_type == "Series B" for r in result.funding_rounds)
        assert any(a.acquirer == "Microsoft" for a in result.acquisitions)
        assert any(c.registration_number == "12345678" for c in result.companies_house_records)

    def test_build_graph_from_research_results(self) -> None:

        g = EntityGraph()
        g.add_node(EntityNode(id="acme", name="Acme Corp", entity_type="organization",
                              industry="Technology", metadata={"domain": "acme.com"}))
        g.add_node(EntityNode(id="beta", name="Beta Inc", entity_type="organization",
                              metadata={"domain": "beta.com"}))
        g.add_node(EntityNode(id="gamma", name="Gamma LLC", entity_type="organization",
                              metadata={"domain": "gamma.io"}))

        g.add_edge(Association(source_id="acme", target_id="beta", assoc_type="contractual",
                               description="partnership"))
        g.add_edge(Association(source_id="beta", target_id="acme", assoc_type="financial",
                               description="Series A investor"))
        g.add_edge(Association(source_id="gamma", target_id="acme", assoc_type="competitive",
                               description="competitor"))

        related = g.get_related("acme", max_depth=1)
        related_names = set()
        for node_id in related.get("depth_1", []):
            node = g.get_node(node_id)
            if node:
                related_names.add(node.name)

        assert "Beta Inc" in related_names
        assert "Gamma LLC" in related_names

        finance_entities = g.find_by_type("organization")
        assert len(finance_entities) == 3

    def test_research_then_query_workflow(self) -> None:
        g = EntityGraph()
        g.add_node(EntityNode(id="apple", name="Apple Inc", entity_type="organization",
                              industry="Technology", jurisdiction="US-CA"))
        g.add_node(EntityNode(id="samsung", name="Samsung Electronics", entity_type="organization",
                              industry="Technology", jurisdiction="KR"))
        g.add_node(EntityNode(id="tsmc", name="TSMC", entity_type="organization",
                              industry="Semiconductor", jurisdiction="TW"))
        g.add_node(EntityNode(id="foxconn", name="Foxconn", entity_type="organization",
                              industry="Manufacturing", jurisdiction="TW"))

        g.add_edge(Association(source_id="apple", target_id="samsung", assoc_type="competitive",
                               description="competitor"))
        g.add_edge(Association(source_id="apple", target_id="tsmc", assoc_type="contractual",
                               description="chip supplier"))
        g.add_edge(Association(source_id="apple", target_id="foxconn", assoc_type="contractual",
                               description="manufacturing partner"))

        tech_companies = g.find_by_industry("Technology")
        assert len(tech_companies) == 2

        tw_companies = g.find_by_jurisdiction("TW")
        assert len(tw_companies) == 2

        apple_related = g.get_related("apple", max_depth=2)
        assert len(apple_related["depth_1"]) == 3

        path = g.find_path("samsung", "foxconn")
        assert path is not None
        assert len(path) == 3

    def test_multi_entity_merge(self) -> None:
        g = EntityGraph()
        for i in range(10):
            g.add_node(EntityNode(
                id=f"e{i}",
                name=f"Entity{i}",
                entity_type="organization" if i % 2 == 0 else "person",
                industry=f"Ind{i % 3}",
            ))
        for i in range(15):
            g.add_edge(Association(
                source_id=f"e{i % 10}",
                target_id=f"e{(i + 1) % 10}",
                assoc_type="other",
            ))

        clusters = g.detect_clusters()
        assert len(clusters) == 1

        for i in range(3):
            ind_entities = g.find_by_industry(f"Ind{i}")
            assert len(ind_entities) > 0

        serialized = g.to_dict()
        assert "nodes" in serialized
        assert "edges" in serialized


class TestMockSearxIntegration:
    def test_research_with_mocked_search_results(self) -> None:
        mock_search_result = (
            "Result 1: Tesla Inc (tesla.com, CIK 0001318605) is an electric vehicle company.\n"
            "Tesla filed Form 10-K with Accession No. 001-34756.\n"
            "In 2020, Tesla raised $2B in Series A funding.\n"
            "Tesla acquired SolarCity for $2.6B.\n"
            "Tesla competes with Rivian (rivian.com).\n"
        )

        result = research_entity(mock_search_result)

        assert any(d.domain == "tesla.com" for d in result.domains)
        assert any(d.domain == "rivian.com" for d in result.domains)
        assert any(f.cik == "0001318605" for f in result.sec_filings)
        assert any(f.file_number == "001-34756" for f in result.sec_filings)
        assert any(f.form_type == "10-K" for f in result.sec_filings)
        assert any(r.round_type == "Series A" for r in result.funding_rounds)
        assert any(a.acquirer == "SolarCity" for a in result.acquisitions)

    def test_partial_search_results(self) -> None:
        partial_result = (
            "UnknownCo at unknownco.org seems to be a small company.\n"
            "No SEC filings found.\n"
            "No funding rounds detected.\n"
        )

        result = research_entity(partial_result)
        assert any(d.domain == "unknownco.org" for d in result.domains)
        assert result.sec_filings == []
        assert result.funding_rounds == []
        assert result.acquisitions == []

    def test_inject_results_into_graph(self) -> None:
        search_results = [
            "Company A (a.com) acquired by Company B (b.com) for $100M.",
            "Company B (b.com) raised Series C $200M. CIK 0000000001.",
            "Company C (c.com) is a competitor of Company A and Company B.",
        ]

        g = EntityGraph()
        g.add_node(EntityNode(id="a", name="Company A", entity_type="organization",
                              metadata={"domain": "a.com"}))
        g.add_node(EntityNode(id="b", name="Company B", entity_type="organization",
                              metadata={"domain": "b.com"}))
        g.add_node(EntityNode(id="c", name="Company C", entity_type="organization",
                              metadata={"domain": "c.com"}))

        for text in search_results:
            research = research_entity(text)
            for acq in research.acquisitions:
                g.add_edge(Association(
                    source_id="b" if "b.com" in text else "UNKNOWN",
                    target_id="a" if "a.com" in text else "UNKNOWN",
                    assoc_type=Association.classify_type(acq.raw_text),
                    description=acq.raw_text,
                ))

        assert g.node_count == 3
        assert g.has_node("a")
        assert g.has_node("b")
        assert g.has_node("c")
