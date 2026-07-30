"""Unit tests for AIML Phase B: dataset engineering + research discovery.

Spec reference: docs/specs/FEATURE_AI_ML_EXPERT.md
  - §6.1 Data formats and dataset engineering (AIML-005):
      * machine-readable schema, units, ontology/version, null semantics;
      * origin/license/consent records at item or partition granularity;
      * leakage-aware train/validation/test splits + near-duplicate checks;
      * PII, secret, malware, poison, prompt-injection scans;
      * class/distribution summaries, known gaps, data card;
      * immutable manifest with shard digests + reproducible transforms.
  - §5.1 Source discovery (AIML-002):
      * query portfolio across papers/docs/repos/issues/blogs/forums/catalogs;
      * robots/terms/auth/rate-limit/domain-allow-deny policy per connector;
      * retrieved text is DATA, never an instruction;
      * score authority, recency, reproducibility, directness, independence.

TDD red phase -- ``general_ludd.ai_ml.datasets`` and
``general_ludd.ai_ml.research`` must satisfy every assertion below.
"""

from __future__ import annotations

import dataclasses
import hashlib

import pytest

from general_ludd.ai_ml.datasets import (
    DataCard,
    DatasetManifest,
    DatasetSchema,
    ShardDigest,
    select_format,
    validate_dataset,
)
from general_ludd.ai_ml.research import (
    AuthorityScore,
    QueryPortfolio,
    ResearchDiscovery,
    RetrievedItem,
    SourceConnectorKind,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha(content: bytes = b"shard") -> str:
    return hashlib.sha256(content).hexdigest()


def _schema(**overrides: object) -> DatasetSchema:
    base: dict[str, object] = {
        "name": "imdb-mini",
        "ontology_version": "text-classification.v1",
        "units": {},
        "null_semantics": "missing label -> -1",
        "columns": (
            {"name": "text", "dtype": "string", "nullable": False},
            {"name": "label", "dtype": "int8", "nullable": True},
        ),
    }
    base.update(overrides)
    return DatasetSchema(**base)


def _shard(name: str = "train-000.parquet", content: bytes = b"train") -> ShardDigest:
    return ShardDigest(name=name, sha256=_sha(content), byte_size=len(content))


def _manifest(**overrides: object) -> DatasetManifest:
    base: dict[str, object] = {
        "manifest_id": "ds-001",
        "version": "1.0.0",
        "schema": _schema(),
        "license": "CC-BY-4.0",
        "consent_uri": "https://example.com/consent",
        "origin_uri": "https://example.com/imdb",
        "splits": (
            ShardDigest(name="train-000.parquet", sha256=_sha(b"train"), byte_size=5),
            ShardDigest(name="val-000.parquet", sha256=_sha(b"val"), byte_size=3),
            ShardDigest(name="test-000.parquet", sha256=_sha(b"test"), byte_size=4),
        ),
        "transform_sha256": _sha(b"transform"),
        "creator": "fixture",
    }
    base.update(overrides)
    return DatasetManifest(**base)


# ---------------------------------------------------------------------------
# DatasetManifest contract
# ---------------------------------------------------------------------------


class TestDatasetManifestContract:
    def test_manifest_carries_schema_license_and_splits(self) -> None:
        m = _manifest()
        assert m.schema is not None
        assert m.license == "CC-BY-4.0"
        assert len(m.splits) == 3
        for shard in m.splits:
            assert shard.sha256 and shard.byte_size >= 0

    def test_manifest_rejects_missing_schema(self) -> None:
        with pytest.raises((TypeError, ValueError)) as exc_info:
            _manifest(schema=None)  # type: ignore[arg-type]
        assert exc_info.value is not None

    def test_manifest_rejects_empty_license(self) -> None:
        with pytest.raises(ValueError, match="license"):
            _manifest(license="")

    def test_manifest_rejects_empty_splits(self) -> None:
        with pytest.raises(ValueError, match="split"):
            _manifest(splits=())

    def test_manifest_rejects_duplicate_shard_names(self) -> None:
        dup = (_shard("a.parquet"), _shard("a.parquet", b"other"))
        with pytest.raises(ValueError, match="duplicate shard name"):
            _manifest(splits=dup)

    def test_manifest_is_frozen(self) -> None:
        m = _manifest()
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.license = "MIT"  # type: ignore[misc]

    def test_shard_digest_is_frozen_and_immutable(self) -> None:
        s = _shard()
        with pytest.raises(dataclasses.FrozenInstanceError):
            s.sha256 = "0" * 64  # type: ignore[misc]

    def test_schema_rejects_empty_columns(self) -> None:
        with pytest.raises(ValueError, match="columns"):
            _schema(columns=())


# ---------------------------------------------------------------------------
# validate_dataset: leakage, duplicates, PII/secrets
# ---------------------------------------------------------------------------


class TestValidateDataset:
    def test_clean_manifest_has_no_findings(self) -> None:
        findings = validate_dataset(_manifest(), records=())
        assert findings == []

    def test_detects_split_leakage_via_overlapping_keys(self) -> None:
        m = _manifest()
        # Same key present in train and test = leakage.
        records = [
            {"split": "train", "key": "doc-1"},
            {"split": "test", "key": "doc-1"},
        ]
        findings = validate_dataset(m, records=records)
        kinds = {f.kind for f in findings}
        assert "leakage" in kinds

    def test_detects_near_duplicate_within_split(self) -> None:
        m = _manifest()
        records = [
            {"split": "train", "key": "a", "text": "the quick brown fox"},
            {"split": "train", "key": "b", "text": "the quick brown fox"},
        ]
        findings = validate_dataset(m, records=records)
        kinds = {f.kind for f in findings}
        assert "near_duplicate" in kinds

    def test_flags_pii_pattern_in_text(self) -> None:
        m = _manifest()
        records = [
            {"split": "train", "key": "a", "text": "call me at 555-867-5309"},
        ]
        findings = validate_dataset(m, records=records)
        kinds = {f.kind for f in findings}
        assert "pii" in kinds

    def test_flags_secret_token_in_text(self) -> None:
        m = _manifest()
        records = [
            {"split": "train", "key": "a", "text": "AKIAIOSFODNN7EXAMPLE secret"},
        ]
        findings = validate_dataset(m, records=records)
        kinds = {f.kind for f in findings}
        assert "secret" in kinds

    def test_findings_carry_split_and_severity(self) -> None:
        m = _manifest()
        records = [
            {"split": "train", "key": "a", "text": "ssn 123-45-6789"},
        ]
        findings = validate_dataset(m, records=records)
        assert findings, "expected at least one finding"
        f = findings[0]
        assert f.split == "train"
        assert f.severity in {"low", "medium", "high", "critical"}


# ---------------------------------------------------------------------------
# DataCard generation
# ---------------------------------------------------------------------------


class TestDataCard:
    def test_data_card_summarizes_splits_and_classes(self) -> None:
        m = _manifest()
        card = DataCard.from_manifest(
            m,
            class_distribution={"neg": 12500, "pos": 12500},
            known_gaps=("no examples before 2010",),
        )
        assert card.manifest_id == m.manifest_id
        assert card.license == m.license
        assert card.num_shards == len(m.splits)
        assert card.class_distribution == {"neg": 12500, "pos": 12500}
        assert "no examples before 2010" in card.known_gaps

    def test_data_card_is_frozen(self) -> None:
        card = DataCard.from_manifest(_manifest())
        with pytest.raises(dataclasses.FrozenInstanceError):
            card.license = "MIT"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# select_format: evaluate fit across candidates
# ---------------------------------------------------------------------------


class TestSelectFormat:
    def test_prefers_parquet_for_columnar_tabular(self) -> None:
        result = select_format(
            schema=_schema(),
            priorities=("column_pruning", "compression", "schema_evolution"),
        )
        assert result.selected in {"parquet", "arrow"}
        assert result.selected not in {"jsonl", "hdf5"}

    def test_prefers_jsonl_for_streaming(self) -> None:
        result = select_format(
            schema=_schema(),
            priorities=("streaming", "interoperability"),
        )
        assert result.selected == "jsonl"

    def test_prefers_safetensors_for_dense_tensors(self) -> None:
        result = select_format(
            schema=_schema(columns=({"name": "embeddings", "dtype": "float32", "nullable": False},)),
            priorities=("random_access", "interoperability"),
            dense_tensors=True,
        )
        assert result.selected == "safetensors"

    def test_prefers_hdf5_for_multimodal_scientific(self) -> None:
        result = select_format(
            schema=_schema(),
            priorities=("multimodal_payloads", "random_access"),
            multimodal=True,
        )
        assert result.selected == "hdf5"

    def test_never_picks_format_with_zero_score(self) -> None:
        result = select_format(
            schema=_schema(),
            priorities=("column_pruning",),
        )
        # jsonl has no column-pruning capability -> score 0 -> never selected
        for tradeoff in result.tradeoffs:
            if tradeoff.format_id == "jsonl":
                assert tradeoff.score == 0.0
        assert result.selected != "jsonl"

    def test_returns_tradeoff_table_with_rationale(self) -> None:
        result = select_format(schema=_schema(), priorities=("compression",))
        assert len(result.tradeoffs) >= 4
        for t in result.tradeoffs:
            assert t.format_id
            assert 0.0 <= t.score <= 1.0
            assert t.rationale


# ---------------------------------------------------------------------------
# Research discovery
# ---------------------------------------------------------------------------


class TestResearchDiscovery:
    def test_query_portfolio_accepts_topic_and_connector_filter(self) -> None:
        portfolio = QueryPortfolio(
            topics=("transformer long-context",),
            connectors=(SourceConnectorKind.PAPERS, SourceConnectorKind.REPOS),
        )
        assert SourceConnectorKind.PAPERS in portfolio.connectors

    def test_search_sources_returns_untrusted_items(self) -> None:
        rd = ResearchDiscovery(portfolio=QueryPortfolio(topics=("x",)))
        items = rd.search_sources()
        assert items, "stub must return at least one item"
        for item in items:
            assert isinstance(item, RetrievedItem)
            assert item.trusted is False, "retrieved content is always untrusted"

    def test_score_authority_blends_four_axes(self) -> None:
        rd = ResearchDiscovery(portfolio=QueryPortfolio(topics=("x",)))
        score = rd.score_authority(
            recency=0.9,
            reproducibility=0.8,
            directness=1.0,
            independence=0.7,
        )
        assert isinstance(score, AuthorityScore)
        assert 0.0 <= score.composite <= 1.0
        assert score.composite > 0.0

    def test_score_authority_rejects_out_of_range_inputs(self) -> None:
        rd = ResearchDiscovery(portfolio=QueryPortfolio(topics=("x",)))
        with pytest.raises(ValueError):
            rd.score_authority(recency=1.5, reproducibility=0.5, directness=0.5, independence=0.5)

    def test_retrieved_item_carries_locator_and_untrusted_flag(self) -> None:
        item = RetrievedItem(
            source_id="src-1",
            locator="https://arxiv.org/abs/1234",
            media_type="text/html",
            sha256=_sha(b"paper"),
            fetched_at=1,
            connector=SourceConnectorKind.PAPERS,
        )
        assert item.trusted is False
        assert item.connector == SourceConnectorKind.PAPERS
