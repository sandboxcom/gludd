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
import time

import pytest

from general_ludd.ai_ml.datasets import (
    _CAPABILITY_MATRIX,
    _VALID_PRIORITIES,
    DataCard,
    DatasetManifest,
    DatasetSchema,
    FindingKind,
    FormatSelection,
    FormatSelector,
    FormatTradeoff,
    Severity,
    ShardDigest,
    ValidationFinding,
    _check_leakage,
    _check_near_duplicates,
    _check_pii,
    _check_secrets,
    _split_of,
    _text_of,
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


# ==============================================================================
# Deep tests: schema, digest, manifest, validation, format selection edge cases
# ==============================================================================


class TestDatasetSchemaDeep:
    """Schema validation edge cases not covered by TestDatasetManifestContract."""

    def test_rejects_non_dict_column(self) -> None:
        with pytest.raises(ValueError, match="each column must be a dict"):
            DatasetSchema(
                name="test",
                ontology_version="1.0",
                columns=(123,),  # type: ignore[arg-type]
            )

    def test_rejects_column_with_empty_name(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            DatasetSchema(
                name="test",
                ontology_version="1.0",
                columns=({"name": "  ", "dtype": "str", "nullable": True},),
            )

    def test_rejects_column_missing_name_key(self) -> None:
        with pytest.raises(ValueError, match="non-empty 'name'"):
            DatasetSchema(
                name="test",
                ontology_version="1.0",
                columns=({"dtype": "str", "nullable": True},),
            )

    def test_rejects_duplicate_column_names(self) -> None:
        with pytest.raises(ValueError, match="duplicate column name"):
            DatasetSchema(
                name="test",
                ontology_version="1.0",
                columns=(
                    {"name": "col_a", "dtype": "int", "nullable": False},
                    {"name": "col_a", "dtype": "float", "nullable": True},
                ),
            )

    def test_rejects_empty_name(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            DatasetSchema(name="", ontology_version="1.0")

    def test_rejects_empty_ontology_version(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            DatasetSchema(name="test", ontology_version="")


class TestShardDigestDeep:
    """ShardDigest validation beyond basic frozen check."""

    def test_rejects_negative_byte_size(self) -> None:
        with pytest.raises(ValueError, match="non-negative int"):
            ShardDigest(name="a.parquet", sha256=_sha(), byte_size=-1)

    def test_rejects_invalid_sha256_too_short(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ShardDigest(name="a.parquet", sha256="abc123", byte_size=0)

    def test_rejects_invalid_sha256_uppercase(self) -> None:
        with pytest.raises(ValueError, match="sha256"):
            ShardDigest(name="a.parquet", sha256="A" * 64, byte_size=0)

    def test_accepts_zero_byte_size(self) -> None:
        s = ShardDigest(name="empty.parquet", sha256=_sha(b""), byte_size=0)
        assert s.byte_size == 0


class TestDatasetManifestDeep:
    """Manifest edge cases not covered by basic contract tests."""

    def test_rejects_whitespace_only_supersedes(self) -> None:
        with pytest.raises(ValueError, match="supersedes"):
            _manifest(supersedes="   ")

    def test_rejects_non_shard_digest_in_splits(self) -> None:
        with pytest.raises(ValueError, match="each split must be a ShardDigest"):
            _manifest(splits=("not-a-shard",))  # type: ignore[arg-type]

    def test_rejects_invalid_transform_sha256(self) -> None:
        with pytest.raises(ValueError, match="transform_sha256"):
            _manifest(transform_sha256="bad-hash")

    def test_rejects_empty_consent_uri(self) -> None:
        with pytest.raises(ValueError, match="consent_uri"):
            _manifest(consent_uri="")

    def test_rejects_empty_origin_uri(self) -> None:
        with pytest.raises(ValueError, match="origin_uri"):
            _manifest(origin_uri="")

    def test_rejects_empty_creator(self) -> None:
        with pytest.raises(ValueError, match="creator"):
            _manifest(creator="")

    def test_accepts_none_supersedes(self) -> None:
        m = _manifest(supersedes=None)
        assert m.supersedes is None

    def test_created_at_is_populated_by_default(self) -> None:
        before = int(time.time())
        m = _manifest()
        after = int(time.time())
        assert before <= m.created_at <= after + 1


# ---------------------------------------------------------------------------
# Validation helpers (_split_of, _text_of) + internal checkers
# ---------------------------------------------------------------------------


class TestValidationHelpers:
    def test_split_of_returns_unknown_for_missing_split(self) -> None:
        assert _split_of({}) == "unknown"

    def test_split_of_returns_str_value(self) -> None:
        assert _split_of({"split": "train"}) == "train"
        assert _split_of({"split": 42}) == "42"

    def test_text_of_returns_text_field(self) -> None:
        assert _text_of({"text": "hello"}) == "hello"

    def test_text_of_falls_back_to_content(self) -> None:
        assert _text_of({"content": "fallback text"}) == "fallback text"

    def test_text_of_returns_empty_str_when_both_missing(self) -> None:
        assert _text_of({}) == ""

    def test_text_of_converts_non_str_value(self) -> None:
        assert _text_of({"text": 123}) == "123"

    def test_text_of_prefers_text_over_content(self) -> None:
        assert _text_of({"text": "primary", "content": "fallback"}) == "primary"

    def test_text_of_handles_none_text(self) -> None:
        assert _text_of({"text": None, "content": "fallback"}) == "fallback"


class TestCheckLeakageDeep:
    def test_no_records_returns_empty(self) -> None:
        assert _check_leakage([]) == []

    def test_all_unique_keys_no_leakage(self) -> None:
        records = [
            {"split": "train", "key": "a"},
            {"split": "test", "key": "b"},
        ]
        assert _check_leakage(records) == []

    def test_records_without_key_field_ignored(self) -> None:
        records = [
            {"split": "train", "text": "no key here"},
            {"split": "test", "text": "also no key"},
        ]
        assert _check_leakage(records) == []

    def test_multiple_splits_same_key(self) -> None:
        records = [
            {"split": "train", "key": "dupe"},
            {"split": "val", "key": "dupe"},
            {"split": "test", "key": "dupe"},
        ]
        findings = _check_leakage(records)
        assert len(findings) == 1
        assert findings[0].kind == FindingKind.LEAKAGE
        assert "train" in findings[0].split
        assert "test" in findings[0].split
        assert "val" in findings[0].split

    def test_non_string_key_ignored(self) -> None:
        records = [
            {"split": "train", "key": 1},
            {"split": "test", "key": 1},
        ]
        assert _check_leakage(records) == []


class TestCheckNearDuplicatesDeep:
    def test_no_records_returns_empty(self) -> None:
        assert _check_near_duplicates([]) == []

    def test_different_split_same_text_not_duplicate(self) -> None:
        records = [
            {"split": "train", "key": "a", "text": "same text"},
            {"split": "test", "key": "b", "text": "same text"},
        ]
        assert _check_near_duplicates(records) == []

    def test_case_insensitive_normalization(self) -> None:
        records = [
            {"split": "train", "key": "a", "text": "Hello World"},
            {"split": "train", "key": "b", "text": "hello world  "},
        ]
        findings = _check_near_duplicates(records)
        assert len(findings) == 1
        assert findings[0].kind == FindingKind.NEAR_DUPLICATE

    def test_empty_text_ignored(self) -> None:
        records = [
            {"split": "train", "key": "a", "text": "   "},
            {"split": "train", "key": "b", "text": "   "},
        ]
        assert _check_near_duplicates(records) == []

    def test_content_field_fallback(self) -> None:
        records = [
            {"split": "train", "key": "a", "content": "duplicate content"},
            {"split": "train", "key": "b", "content": "duplicate content"},
        ]
        findings = _check_near_duplicates(records)
        assert len(findings) == 1

    def test_three_way_duplicate_flags_once(self) -> None:
        records = [
            {"split": "train", "key": "a", "text": "dup"},
            {"split": "train", "key": "b", "text": "dup"},
            {"split": "train", "key": "c", "text": "dup"},
        ]
        findings = _check_near_duplicates(records)
        assert len(findings) == 1
        assert "3 records" in findings[0].detail


class TestCheckPiiDeep:
    def test_no_pii_in_clean_text(self) -> None:
        records = [{"split": "train", "key": "a", "text": "hello world"}]
        assert _check_pii(records) == []

    def test_detects_email_pii(self) -> None:
        records = [{"split": "train", "key": "a", "text": "email me at test@example.com"}]
        findings = _check_pii(records)
        assert len(findings) == 1
        assert "email" in findings[0].detail

    def test_detects_credit_card_pii(self) -> None:
        records = [{"split": "train", "key": "a", "text": "card: 4111-1111-1111-1111"}]
        findings = _check_pii(records)
        assert len(findings) >= 1
        assert findings[0].kind == FindingKind.PII

    def test_detects_us_ssn_pii(self) -> None:
        records = [{"split": "train", "key": "a", "text": "ssn: 123-45-6789"}]
        findings = _check_pii(records)
        assert len(findings) == 1
        assert findings[0].severity == Severity.HIGH

    def test_multiple_pii_patterns_in_one_record(self) -> None:
        records = [
            {
                "split": "train",
                "key": "a",
                "text": "email: a@b.com phone: 555-123-4567",
            }
        ]
        findings = _check_pii(records)
        assert len(findings) >= 2

    def test_no_text_field_no_findings(self) -> None:
        records = [{"split": "train", "key": "a"}]
        assert _check_pii(records) == []


class TestCheckSecretsDeep:
    def test_no_secrets_in_clean_text(self) -> None:
        records = [{"split": "train", "key": "a", "text": "hello world"}]
        assert _check_secrets(records) == []

    def test_detects_github_pat(self) -> None:
        records = [{"split": "train", "key": "a", "text": "ghp_" + "A" * 36}]
        findings = _check_secrets(records)
        assert len(findings) == 1
        assert findings[0].kind == FindingKind.SECRET
        assert findings[0].severity == Severity.CRITICAL

    def test_detects_google_api_key(self) -> None:
        records = [{"split": "train", "key": "a", "text": "key: AIza" + "A" * 35}]
        findings = _check_secrets(records)
        assert len(findings) >= 1

    def test_detects_slack_token(self) -> None:
        records = [{"split": "train", "key": "a", "text": "token: xoxb-abcdefghijk"}]
        findings = _check_secrets(records)
        assert len(findings) >= 1

    def test_detects_pem_private_key(self) -> None:
        records = [
            {
                "split": "train",
                "key": "a",
                "text": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----",
            }
        ]
        findings = _check_secrets(records)
        assert len(findings) == 1

    def test_detects_aws_secret_access_key(self) -> None:
        records = [
            {
                "split": "train",
                "key": "a",
                "text": "aws_secret_access_key=AbCdEfGhIjKlMnOpQrStUvWxYz12345678abcd+/=",
            }
        ]
        findings = _check_secrets(records)
        assert len(findings) >= 1


class TestValidateDatasetDeep:
    """Edge cases around the public validate_dataset API."""

    def test_rejects_non_list_records(self) -> None:
        m = _manifest()
        with pytest.raises(TypeError, match="records must be a list or tuple"):
            validate_dataset(m, records={"not": "a list"})  # type: ignore[arg-type]

    def test_accepts_tuple_records(self) -> None:
        m = _manifest()
        records = ({"split": "train", "key": "a", "text": "hello"},)
        findings = validate_dataset(m, records=records)
        assert isinstance(findings, list)

    def test_findings_sorted_by_kind_then_split_then_severity(self) -> None:
        m = _manifest()
        records = [
            {"split": "train", "key": "dup", "text": "AKIAIOSFODNN7EXAMPLE"},
            {"split": "test", "key": "dup", "text": "clean text"},
            {"split": "train", "key": "a", "text": "the quick brown fox"},
            {"split": "train", "key": "b", "text": "the quick brown fox"},
        ]
        findings = validate_dataset(m, records=records)
        kinds = [f.kind for f in findings]
        assert kinds == sorted(kinds)

    def test_manifest_arg_accepted_but_not_validated(self) -> None:
        m = _manifest()
        findings = validate_dataset(m, records=())
        assert findings == []


class TestValidationFindingDeep:
    """ValidationFinding post_init edge cases."""

    def test_finding_kind_enum_value(self) -> None:
        f = ValidationFinding(
            kind=FindingKind.SECRET,
            split="train",
            severity=Severity.CRITICAL,
            detail="test secret",
        )
        assert f.kind == "secret"

    def test_finding_default_key_is_empty_string(self) -> None:
        f = ValidationFinding(
            kind=FindingKind.PII,
            split="train",
            severity=Severity.HIGH,
            detail="test",
        )
        assert f.key == ""


class TestDataCardDeep:
    """DataCard edge cases not covered by TestDataCard."""

    def test_default_summary_when_summary_empty(self) -> None:
        m = _manifest()
        card = DataCard.from_manifest(m)
        assert m.schema.name in card.summary
        assert m.version in card.summary

    def test_none_class_distribution_becomes_empty_dict(self) -> None:
        m = _manifest()
        card = DataCard.from_manifest(m, class_distribution=None)
        assert card.class_distribution == {}

    def test_list_gaps_converts_to_tuple(self) -> None:
        m = _manifest()
        card = DataCard.from_manifest(m, known_gaps=["gap a", "gap b"])
        assert isinstance(card.known_gaps, tuple)
        assert "gap a" in card.known_gaps

    def test_from_manifest_fills_all_fields(self) -> None:
        m = _manifest()
        card = DataCard.from_manifest(m)
        assert card.manifest_id == m.manifest_id
        assert card.license == m.license
        assert card.origin_uri == m.origin_uri
        assert card.num_shards == len(m.splits)


class TestFormatTradeoffDeep:
    """FormatTradeoff post_init edge cases."""

    def test_score_at_zero_allowed(self) -> None:
        ft = FormatTradeoff(format_id="jsonl", score=0.0, rationale="no column pruning")
        assert ft.score == 0.0

    def test_score_at_one_allowed(self) -> None:
        ft = FormatTradeoff(format_id="best", score=1.0, rationale="perfect match")
        assert ft.score == 1.0

    def test_score_below_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="score"):
            FormatTradeoff(format_id="bad", score=-0.1, rationale="bad")

    def test_score_above_one_rejected(self) -> None:
        with pytest.raises(ValueError, match="score"):
            FormatTradeoff(format_id="bad", score=1.1, rationale="bad")

    def test_empty_rationale_rejected(self) -> None:
        with pytest.raises(ValueError, match="rationale"):
            FormatTradeoff(format_id="x", score=0.5, rationale="")


class TestFormatSelectionDeep:
    """FormatSelection post_init edge cases."""

    def test_empty_tradeoffs_rejected(self) -> None:
        with pytest.raises(ValueError, match="tradeoffs"):
            FormatSelection(
                selected="jsonl",
                tradeoffs=(),
                priorities=(),
            )

    def test_empty_selected_rejected(self) -> None:
        ft = FormatTradeoff(format_id="x", score=0.5, rationale="ok")
        with pytest.raises(ValueError, match="selected"):
            FormatSelection(selected="", tradeoffs=(ft,), priorities=())


class TestFormatSelectorDeep:
    """FormatSelector edge cases not covered by TestSelectFormat."""

    def test_unknown_priority_raises(self) -> None:
        sel = FormatSelector()
        with pytest.raises(ValueError, match="unknown priority"):
            sel.select(schema=_schema(), priorities=("nonexistent_priority",))

    def test_empty_priorities_after_normalization_raises(self) -> None:
        sel = FormatSelector()
        with pytest.raises(ValueError, match="priorities"):
            sel.select(schema=_schema(), priorities=("   ",))

    def test_tradeoff_table_includes_all_formats(self) -> None:
        sel = FormatSelector()
        result = sel.select(schema=_schema(), priorities=("interoperability",))
        format_ids = {t.format_id for t in result.tradeoffs}
        assert len(format_ids) == len(_CAPABILITY_MATRIX)

    def test_sort_is_descending_by_score(self) -> None:
        sel = FormatSelector()
        result = sel.select(schema=_schema(), priorities=("interoperability",))
        scores = [t.score for t in result.tradeoffs]
        assert scores == sorted(scores, reverse=True)

    def test_zero_score_format_never_selected(self) -> None:
        sel = FormatSelector()
        result = sel.select(schema=_schema(), priorities=("column_pruning",))
        for t in result.tradeoffs:
            if t.score == 0.0:
                assert t.format_id != result.selected

    def test_dense_tensors_boost_only_safetensors(self) -> None:
        sel = FormatSelector()
        base = sel.select(schema=_schema(), priorities=("interoperability",))
        boosted = sel.select(
            schema=_schema(),
            priorities=("interoperability",),
            dense_tensors=True,
        )
        base_by_id = {t.format_id: t.score for t in base.tradeoffs}
        boosted_by_id = {t.format_id: t.score for t in boosted.tradeoffs}
        assert boosted_by_id["safetensors"] > base_by_id["safetensors"]
        for fmt_id in base_by_id:
            if fmt_id != "safetensors":
                assert boosted_by_id[fmt_id] == base_by_id[fmt_id]

    def test_multimodal_boost_only_hdf5(self) -> None:
        sel = FormatSelector()
        base = sel.select(schema=_schema(), priorities=("scale",))
        boosted = sel.select(
            schema=_schema(),
            priorities=("scale",),
            multimodal=True,
        )
        base_by_id = {t.format_id: t.score for t in base.tradeoffs}
        boosted_by_id = {t.format_id: t.score for t in boosted.tradeoffs}
        assert boosted_by_id["hdf5"] > base_by_id["hdf5"]
        for fmt_id in base_by_id:
            if fmt_id != "hdf5":
                assert boosted_by_id[fmt_id] == base_by_id[fmt_id]

    def test_score_capped_at_one_by_dense_tensors_boost(self) -> None:
        sel = FormatSelector()
        result = sel.select(
            schema=_schema(),
            priorities=("random_access", "interoperability"),
            dense_tensors=True,
        )
        for t in result.tradeoffs:
            assert t.score <= 1.0

    def test_all_valid_priorities_work(self) -> None:
        sel = FormatSelector()
        for priority in sorted(_VALID_PRIORITIES):
            result = sel.select(schema=_schema(), priorities=(priority,))
            assert result.selected

    def test_priorities_normalized_to_lowercase(self) -> None:
        sel = FormatSelector()
        result = sel.select(schema=_schema(), priorities=("  Interoperability  ",))
        assert result.selected
        assert "interoperability" in result.priorities

    def test_select_function_wrapper_returns_same_as_selector(self) -> None:
        scheme = _schema()
        priorities: tuple[str, ...] = ("compression",)
        from_wrapper = select_format(schema=scheme, priorities=priorities)
        from_selector = FormatSelector().select(schema=scheme, priorities=priorities)
        assert from_wrapper.selected == from_selector.selected
        assert len(from_wrapper.tradeoffs) == len(from_selector.tradeoffs)
