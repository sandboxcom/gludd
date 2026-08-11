"""Unit tests for AIML Phase A: research discovery (AIML-002).

Covers docs/specs/FEATURE_AI_ML_EXPERT.md §5.1 (Source discovery) and §5.2 (Authority scoring):

  - QueryPortfolio validates topics, connectors, and optional gap fields.
  - RetrievedItem enforces untrusted-by-default and content-addressed invariants.
  - AuthorityScore blends recency, reproducibility, directness, independence.
  - ResearchDiscovery.search_sources returns one fixture item per (topic, connector).
  - ResearchDiscovery.score_authority computes equal-weight mean composite.
  - RetrievedItem refuses trusted=True at construction time.
"""

from __future__ import annotations

import pytest

from general_ludd.ai_ml.research import (
    AuthorityScore,
    QueryPortfolio,
    ResearchDiscovery,
    RetrievedItem,
    SourceConnectorKind,
)

_SHA_SRC = "a" * 64


# ---------------------------------------------------------------------------
# QueryPortfolio
# ---------------------------------------------------------------------------


class TestQueryPortfolio:
    def test_minimal_construction(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        assert pf.topics == ("ml",)
        assert len(pf.connectors) == len(SourceConnectorKind)

    def test_multiple_topics_with_custom_connectors(self) -> None:
        pf = QueryPortfolio(
            topics=("nlp", "vision"),
            connectors=(SourceConnectorKind.PAPERS, SourceConnectorKind.REPOS),
            known_gaps=("missing benchmark X",),
            contradictory_findings=("paper A vs paper B",),
        )
        assert pf.topics == ("nlp", "vision")
        assert pf.connectors == (SourceConnectorKind.PAPERS, SourceConnectorKind.REPOS)
        assert pf.known_gaps == ("missing benchmark X",)
        assert pf.contradictory_findings == ("paper A vs paper B",)

    def test_empty_topics_raises(self) -> None:
        with pytest.raises(ValueError, match="topics must be a non-empty tuple"):
            QueryPortfolio(topics=())

    def test_empty_topic_string_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a non-empty string"):
            QueryPortfolio(topics=("ok", "  "))

    def test_topics_must_be_tuple_not_list(self) -> None:
        with pytest.raises(ValueError, match="topics must be a non-empty tuple"):
            QueryPortfolio(topics=["ml"])  # type: ignore[arg-type]

    def test_empty_connectors_raises(self) -> None:
        with pytest.raises(ValueError, match="connectors must be a non-empty tuple"):
            QueryPortfolio(topics=("ml",), connectors=())

    def test_defaults_are_empty_for_optional_fields(self) -> None:
        pf = QueryPortfolio(topics=("x",))
        assert pf.known_gaps == ()
        assert pf.contradictory_findings == ()


# ---------------------------------------------------------------------------
# RetrievedItem
# ---------------------------------------------------------------------------


class TestRetrievedItem:
    def test_minimal_construction(self) -> None:
        item = RetrievedItem(
            source_id="src-001",
            locator="https://arxiv.org/abs/2401.00001",
            media_type="text/html",
            sha256=_SHA_SRC,
            fetched_at=1700000000,
            connector=SourceConnectorKind.PAPERS,
        )
        assert item.source_id == "src-001"
        assert item.trusted is False

    def test_every_connector_kind_is_accepted(self) -> None:
        for kind in SourceConnectorKind:
            item = RetrievedItem(
                source_id=f"src-{kind.value}",
                locator=f"https://example.com/{kind.value}",
                media_type="text/html",
                sha256=_SHA_SRC,
                fetched_at=1700000000,
                connector=kind,
            )
            assert item.connector is kind
            assert item.trusted is False

    def test_trusted_true_is_refused_at_construction(self) -> None:
        with pytest.raises(ValueError, match="untrusted by default"):
            RetrievedItem(
                source_id="s",
                locator="https://x",
                media_type="text/html",
                sha256=_SHA_SRC,
                fetched_at=0,
                connector=SourceConnectorKind.PAPERS,
                trusted=True,
            )

    def test_title_sets_correctly(self) -> None:
        item = RetrievedItem(
            source_id="s",
            locator="https://x",
            media_type="text/html",
            sha256=_SHA_SRC,
            fetched_at=0,
            connector=SourceConnectorKind.PAPERS,
            title="My Paper",
        )
        assert item.title == "My Paper"

    def test_empty_source_id_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a non-empty string"):
            RetrievedItem(
                source_id="",
                locator="https://x",
                media_type="text/html",
                sha256=_SHA_SRC,
                fetched_at=0,
                connector=SourceConnectorKind.PAPERS,
            )

    def test_empty_locator_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a non-empty string"):
            RetrievedItem(
                source_id="s",
                locator="",
                media_type="text/html",
                sha256=_SHA_SRC,
                fetched_at=0,
                connector=SourceConnectorKind.PAPERS,
            )

    def test_empty_media_type_raises(self) -> None:
        with pytest.raises(ValueError, match="must be a non-empty string"):
            RetrievedItem(
                source_id="s",
                locator="https://x",
                media_type="",
                sha256=_SHA_SRC,
                fetched_at=0,
                connector=SourceConnectorKind.PAPERS,
            )

    def test_invalid_sha256_raises(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            RetrievedItem(
                source_id="s",
                locator="https://x",
                media_type="text/html",
                sha256="short",
                fetched_at=0,
                connector=SourceConnectorKind.PAPERS,
            )

    def test_invalid_connector_raises(self) -> None:
        with pytest.raises(ValueError, match="connector"):
            RetrievedItem(
                source_id="s",
                locator="https://x",
                media_type="text/html",
                sha256=_SHA_SRC,
                fetched_at=0,
                connector="not_a_connector",  # type: ignore[arg-type]
            )


# ---------------------------------------------------------------------------
# AuthorityScore
# ---------------------------------------------------------------------------

_QUARTER = pytest.param(0.25, id="0.25")
_HALF = pytest.param(0.5, id="0.5")
_ZERO = pytest.param(0.0, id="0.0")
_ONE = pytest.param(1.0, id="1.0")


class TestAuthorityScore:
    def test_equal_weights_produces_midpoint(self) -> None:
        score = AuthorityScore(
            recency=0.5,
            reproducibility=0.5,
            directness=0.5,
            independence=0.5,
            composite=0.5,
        )
        assert score.composite == 0.5

    def test_max_scores(self) -> None:
        score = AuthorityScore(
            recency=1.0,
            reproducibility=1.0,
            directness=1.0,
            independence=1.0,
            composite=1.0,
        )
        assert score.composite == 1.0

    def test_min_scores(self) -> None:
        score = AuthorityScore(
            recency=0.0,
            reproducibility=0.0,
            directness=0.0,
            independence=0.0,
            composite=0.0,
        )
        assert score.composite == 0.0

    @pytest.mark.parametrize("field", ["recency", "reproducibility", "directness", "independence", "composite"])
    def test_negative_value_raises(self, field: str) -> None:
        kwargs = {
            "recency": 0.5,
            "reproducibility": 0.5,
            "directness": 0.5,
            "independence": 0.5,
            "composite": 0.5,
        }
        kwargs[field] = -0.1
        with pytest.raises(ValueError, match=f"{field} must be in"):
            AuthorityScore(**kwargs)

    @pytest.mark.parametrize("field", ["recency", "reproducibility", "directness", "independence", "composite"])
    def test_above_one_raises(self, field: str) -> None:
        kwargs = {
            "recency": 0.5,
            "reproducibility": 0.5,
            "directness": 0.5,
            "independence": 0.5,
            "composite": 0.5,
        }
        kwargs[field] = 1.1
        with pytest.raises(ValueError, match=f"{field} must be in"):
            AuthorityScore(**kwargs)

    def test_method_defaults_to_equal_weight_description(self) -> None:
        score = AuthorityScore(
            recency=0.5,
            reproducibility=0.5,
            directness=0.5,
            independence=0.5,
            composite=0.5,
        )
        assert "equal-weight mean" in score.method

    def test_mismatched_composite_allowed_by_dataclass(self) -> None:
        score = AuthorityScore(
            recency=1.0,
            reproducibility=1.0,
            directness=1.0,
            independence=1.0,
            composite=0.0,
        )
        assert score.composite == 0.0


# ---------------------------------------------------------------------------
# ResearchDiscovery
# ---------------------------------------------------------------------------


class TestResearchDiscoveryConstruction:
    def test_valid_portfolio_accepted(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        assert rd.portfolio is pf

    def test_invalid_portfolio_type_raises(self) -> None:
        with pytest.raises(ValueError, match="portfolio must be a QueryPortfolio"):
            ResearchDiscovery(portfolio="not_a_portfolio")  # type: ignore[arg-type]


class TestSearchSources:
    def test_returns_one_item_per_topic_connector_pair(self) -> None:
        pf = QueryPortfolio(
            topics=("a", "b"),
            connectors=(SourceConnectorKind.PAPERS, SourceConnectorKind.REPOS),
        )
        rd = ResearchDiscovery(portfolio=pf)
        items = rd.search_sources()
        assert len(items) == 4  # 2 topics * 2 connectors

    def test_all_items_are_untrusted(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        for item in rd.search_sources():
            assert item.trusted is False

    def test_all_items_carry_connector_tag(self) -> None:
        pf = QueryPortfolio(
            topics=("ml",),
            connectors=(SourceConnectorKind.PAPERS, SourceConnectorKind.BLOGS),
        )
        rd = ResearchDiscovery(portfolio=pf)
        connectors_found = {item.connector for item in rd.search_sources()}
        assert connectors_found == {SourceConnectorKind.PAPERS, SourceConnectorKind.BLOGS}

    def test_items_are_content_addressed_with_valid_sha256(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        for item in rd.search_sources():
            assert len(item.sha256) == 64
            assert all(c in "0123456789abcdef" for c in item.sha256)

    def test_items_have_titles_mentioning_topic(self) -> None:
        pf = QueryPortfolio(topics=("reinforcement_learning",))
        rd = ResearchDiscovery(portfolio=pf)
        for item in rd.search_sources():
            assert "reinforcement_learning" in item.title

    def test_default_connectors_produce_8_items_for_single_topic(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        items = rd.search_sources()
        assert len(items) == len(SourceConnectorKind)  # 8 connectors

    def test_items_have_deterministic_sha256_for_same_input(self) -> None:
        rd = ResearchDiscovery(portfolio=QueryPortfolio(topics=("ml",)))
        items_a = rd.search_sources()
        items_b = rd.search_sources()
        assert [i.sha256 for i in items_a] == [i.sha256 for i in items_b]


class TestScoreAuthority:
    def test_equal_weight_mean(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        score = rd.score_authority(
            recency=1.0,
            reproducibility=0.0,
            directness=0.0,
            independence=0.0,
        )
        assert score.composite == pytest.approx(0.25)

    def test_all_zeros(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        score = rd.score_authority(
            recency=0.0,
            reproducibility=0.0,
            directness=0.0,
            independence=0.0,
        )
        assert score.composite == 0.0

    def test_all_ones(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        score = rd.score_authority(
            recency=1.0,
            reproducibility=1.0,
            directness=1.0,
            independence=1.0,
        )
        assert score.composite == 1.0

    @pytest.mark.parametrize("field", ["recency", "reproducibility", "directness", "independence"])
    def test_negative_input_raises(self, field: str) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        kwargs = {"recency": 0.5, "reproducibility": 0.5, "directness": 0.5, "independence": 0.5}
        kwargs[field] = -0.01
        with pytest.raises(ValueError, match=f"{field} must be a number in"):
            rd.score_authority(**kwargs)

    @pytest.mark.parametrize("field", ["recency", "reproducibility", "directness", "independence"])
    def test_above_one_raises(self, field: str) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        kwargs = {"recency": 0.5, "reproducibility": 0.5, "directness": 0.5, "independence": 0.5}
        kwargs[field] = 1.1
        with pytest.raises(ValueError, match=f"{field} must be a number in"):
            rd.score_authority(**kwargs)

    def test_non_numeric_input_raises(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        with pytest.raises(ValueError, match="recency must be a number"):
            rd.score_authority(
                recency="high",  # type: ignore[arg-type]
                reproducibility=0.5,
                directness=0.5,
                independence=0.5,
            )

    def test_returns_all_input_axes(self) -> None:
        pf = QueryPortfolio(topics=("ml",))
        rd = ResearchDiscovery(portfolio=pf)
        score = rd.score_authority(
            recency=0.1,
            reproducibility=0.2,
            directness=0.3,
            independence=0.4,
        )
        assert score.recency == 0.1
        assert score.reproducibility == 0.2
        assert score.directness == 0.3
        assert score.independence == 0.4


# ---------------------------------------------------------------------------
# SourceConnectorKind enum
# ---------------------------------------------------------------------------


class TestSourceConnectorKind:
    def test_all_expected_kinds_exist(self) -> None:
        expected = {"papers", "docs", "repos", "issues", "benchmarks", "blogs", "forums", "dataset_catalogs"}
        actual = {k.value for k in SourceConnectorKind}
        assert actual == expected

    def test_each_kind_is_a_string(self) -> None:
        for kind in SourceConnectorKind:
            assert isinstance(kind.value, str)
            assert len(kind.value) > 0
