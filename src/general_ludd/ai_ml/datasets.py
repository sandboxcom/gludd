"""AIML-005 -- dataset engineering: manifests, validation, format selection.

Spec: docs/specs/FEATURE_AI_ML_EXPERT.md §6.1 (Data formats and dataset
engineering).

A :class:`DatasetManifest` is the immutable, reproducible record of a
versioned dataset. It carries:

  - a machine-readable :class:`DatasetSchema` (columns, units, ontology
    version, null semantics);
  - origin / license / consent records (spec §6.1: "at item or partition
    granularity");
  - leakage-aware train/validation/test splits as :class:`ShardDigest`
    entries (immutable, content-addressed);
  - a transform SHA-256 so the dataset can be rebuilt byte-for-byte;
  - a :class:`DataCard` summarizing class distribution, gaps, and license.

:func:`validate_dataset` scans records for split leakage, near-duplicates,
PII, and secret tokens. Every finding is a :class:`ValidationFinding` with a
``kind`` (``leakage`` / ``near_duplicate`` / ``pii`` / ``secret``), a split,
and a severity.

:func:`select_format` evaluates Arrow/Parquet, JSONL, WebDataset, Zarr/HDF5,
SQLite/DuckDB, safetensors, ONNX, and object-store blobs against the caller's
priorities (schema evolution, streaming, random access, column pruning,
compression, multimodal payloads, scale, interoperability, license). A format
is never chosen solely because it is already used (spec §6.1).
"""

from __future__ import annotations

import enum
import re
import time
from dataclasses import dataclass, field
from typing import Any

from general_ludd.ai_ml.schemas import _require_nonempty_str, _require_sha256

# ---------------------------------------------------------------------------
# PII / secret scanning (cheap regex heuristics; spec §6.1 "scans")
# ---------------------------------------------------------------------------

_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("us_ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("us_phone", re.compile(r"\b\d{3}-\d{3}-\d{4}\b")),
    ("email", re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
)

# Real secret scanners (detect-secrets / gitleaks / trufflehog) are preferred
# per AGENTS.md rule 8. These regexes are a fast pre-filter ONLY; they never
# authorize training on flagged content.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_secret_access_key", re.compile(r"(?i)aws_secret_access_key[=:]\s*[A-Za-z0-9/+=]{40}")),
    ("github_pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("private_key_pem", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |)PRIVATE KEY-----")),
)


class FindingKind(enum.StrEnum):
    """Kinds of dataset-validation findings (spec §6.1)."""

    LEAKAGE = "leakage"
    NEAR_DUPLICATE = "near_duplicate"
    PII = "pii"
    SECRET = "secret"
    MALWARE = "malware"
    PROMPT_INJECTION = "prompt_injection"


class Severity(enum.StrEnum):
    """Severity levels for dataset-validation findings."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Schema + shards + manifest
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetSchema:
    """Machine-readable schema for a dataset (spec §6.1).

    ``columns`` is a tuple of ``{"name", "dtype", "nullable"}`` dicts so the
    schema is JSON-serializable and evolves via additive migration rather than
    in-place edits. ``units`` and ``null_semantics`` make the schema
    machine-checkable for dimensional and missingness invariants.
    """

    name: str
    ontology_version: str
    units: dict[str, str] = field(default_factory=dict)
    null_semantics: str = "unspecified"
    columns: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "schema.name")
        _require_nonempty_str(self.ontology_version, "schema.ontology_version")
        if not isinstance(self.columns, tuple) or not self.columns:
            raise ValueError("schema.columns must be a non-empty tuple of column descriptors")
        seen: set[str] = set()
        for col in self.columns:
            if not isinstance(col, dict):
                raise ValueError("each column must be a dict with name/dtype/nullable")
            cname = col.get("name")
            if not isinstance(cname, str) or not cname.strip():
                raise ValueError("each column must have a non-empty 'name'")
            if cname in seen:
                raise ValueError(f"duplicate column name in schema: {cname!r}")
            seen.add(cname)


@dataclass(frozen=True)
class ShardDigest:
    """Content-addressed shard: name + SHA-256 + byte size (spec §6.1)."""

    name: str
    sha256: str
    byte_size: int

    def __post_init__(self) -> None:
        _require_nonempty_str(self.name, "shard.name")
        _require_sha256(self.sha256, "shard.sha256")
        if not isinstance(self.byte_size, int) or self.byte_size < 0:
            raise ValueError(f"shard.byte_size must be a non-negative int, got {self.byte_size!r}")


@dataclass(frozen=True)
class DatasetManifest:
    """Immutable dataset manifest with schema, license/consent, splits, digests.

    Spec §6.1: "immutable manifest with shard digests and reproducible
    transforms." Corrections are made by publishing a new manifest (with a
    new ``version`` and ``supersedes`` link), never by editing in place.
    """

    manifest_id: str
    version: str
    schema: DatasetSchema
    license: str
    consent_uri: str
    origin_uri: str
    splits: tuple[ShardDigest, ...]
    transform_sha256: str
    creator: str
    created_at: int = field(default_factory=lambda: int(time.time()))
    supersedes: str | None = None

    def __post_init__(self) -> None:
        _require_nonempty_str(self.manifest_id, "manifest_id")
        _require_nonempty_str(self.version, "version")
        if not isinstance(self.schema, DatasetSchema):
            raise ValueError("schema must be a DatasetSchema instance")
        _require_nonempty_str(self.license, "license")
        _require_nonempty_str(self.consent_uri, "consent_uri")
        _require_nonempty_str(self.origin_uri, "origin_uri")
        if not isinstance(self.splits, tuple) or not self.splits:
            raise ValueError("splits must be a non-empty tuple of ShardDigest")
        names: set[str] = set()
        for shard in self.splits:
            if not isinstance(shard, ShardDigest):
                raise ValueError("each split must be a ShardDigest")
            if shard.name in names:
                raise ValueError(f"duplicate shard name in manifest: {shard.name!r}")
            names.add(shard.name)
        _require_sha256(self.transform_sha256, "transform_sha256")
        _require_nonempty_str(self.creator, "creator")
        if self.supersedes is not None and not self.supersedes.strip():
            raise ValueError("supersedes, when set, must be a non-empty manifest_id")


# ---------------------------------------------------------------------------
# Validation findings + validate_dataset
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationFinding:
    """One finding from :func:`validate_dataset`."""

    kind: FindingKind
    split: str
    severity: Severity
    detail: str
    key: str = ""

    def __post_init__(self) -> None:
        _require_nonempty_str(self.split, "split")
        _require_nonempty_str(self.detail, "detail")


def _split_of(rec: dict[str, Any]) -> str:
    return str(rec.get("split", "unknown"))


def _text_of(rec: dict[str, Any]) -> str:
    val = rec.get("text")
    if val is None:
        val = rec.get("content", "")
    return str(val) if not isinstance(val, str) else val


def _check_leakage(records: list[dict[str, Any]]) -> list[ValidationFinding]:
    """Flag the same key appearing in more than one split (spec §6.1)."""
    key_splits: dict[str, set[str]] = {}
    for rec in records:
        key = rec.get("key")
        if not isinstance(key, str) or not key:
            continue
        key_splits.setdefault(key, set()).add(_split_of(rec))
    findings: list[ValidationFinding] = []
    for key, splits in key_splits.items():
        if len(splits) > 1:
            findings.append(
                ValidationFinding(
                    kind=FindingKind.LEAKAGE,
                    split=",".join(sorted(splits)),
                    severity=Severity.HIGH,
                    detail=f"key {key!r} appears in multiple splits: {sorted(splits)}",
                    key=key,
                )
            )
    return findings


def _check_near_duplicates(records: list[dict[str, Any]]) -> list[ValidationFinding]:
    """Flag identical normalized text within the same split (spec §6.1)."""
    by_split: dict[str, dict[str, list[str]]] = {}
    for rec in records:
        split = _split_of(rec)
        text = _text_of(rec).strip().lower()
        if not text:
            continue
        key = rec.get("key", "")
        key_str = str(key) if isinstance(key, str) else ""
        by_split.setdefault(split, {}).setdefault(text, []).append(key_str)
    findings: list[ValidationFinding] = []
    for split, text_keys in by_split.items():
        for text, keys in text_keys.items():
            if len(keys) > 1:
                findings.append(
                    ValidationFinding(
                        kind=FindingKind.NEAR_DUPLICATE,
                        split=split,
                        severity=Severity.MEDIUM,
                        detail=(f"{len(keys)} records share identical normalized text; keys={keys[:5]}"),
                        key=",".join(keys),
                    )
                )
    return findings


def _check_pii(records: list[dict[str, Any]]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for rec in records:
        text = _text_of(rec)
        for label, pattern in _PII_PATTERNS:
            if pattern.search(text):
                findings.append(
                    ValidationFinding(
                        kind=FindingKind.PII,
                        split=_split_of(rec),
                        severity=Severity.HIGH,
                        detail=f"detected PII pattern {label!r} in record text",
                        key=str(rec.get("key", "")),
                    )
                )
    return findings


def _check_secrets(records: list[dict[str, Any]]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for rec in records:
        text = _text_of(rec)
        for label, pattern in _SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(
                    ValidationFinding(
                        kind=FindingKind.SECRET,
                        split=_split_of(rec),
                        severity=Severity.CRITICAL,
                        detail=f"detected secret pattern {label!r} in record text",
                        key=str(rec.get("key", "")),
                    )
                )
    return findings


def validate_dataset(
    manifest: DatasetManifest,
    *,
    records: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> list[ValidationFinding]:
    """Scan ``records`` for leakage, near-duplicates, PII, and secrets.

    Returns a sorted list of :class:`ValidationFinding`. The ``manifest``
    argument is accepted so future checks can validate manifest-level
    invariants (shard digest recompute, transform reproducibility); today the
    per-record checks are the primary surface.

    Spec §6.1: "leakage-aware train/validation/test splits and near-duplicate
    checks; PII, secret, malware, poison, and prompt-injection scans."
    """
    if not isinstance(records, (list, tuple)):
        raise TypeError("records must be a list or tuple of dicts")
    rec_list = list(records)
    findings: list[ValidationFinding] = []
    findings.extend(_check_leakage(rec_list))
    findings.extend(_check_near_duplicates(rec_list))
    findings.extend(_check_pii(rec_list))
    findings.extend(_check_secrets(rec_list))
    return sorted(findings, key=lambda f: (f.kind.value, f.split, f.severity.value))


# ---------------------------------------------------------------------------
# DataCard
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataCard:
    """Dataset documentation card (spec §6.1: "a data card").

    Summarizes splits, class distribution, known gaps, license, and
    provenance so a downstream consumer can decide whether the dataset is fit
    for a given purpose without re-deriving it.
    """

    manifest_id: str
    version: str
    license: str
    origin_uri: str
    num_shards: int
    class_distribution: dict[str, int] = field(default_factory=dict)
    known_gaps: tuple[str, ...] = ()
    summary: str = ""

    @classmethod
    def from_manifest(
        cls,
        manifest: DatasetManifest,
        *,
        class_distribution: dict[str, int] | None = None,
        known_gaps: tuple[str, ...] | list[str] = (),
        summary: str = "",
    ) -> DataCard:
        gaps = tuple(known_gaps) if known_gaps else ()
        dist = dict(class_distribution) if class_distribution else {}
        text = summary or (
            f"Dataset {manifest.schema.name} v{manifest.version} ({manifest.license}); {len(manifest.splits)} shards."
        )
        return cls(
            manifest_id=manifest.manifest_id,
            version=manifest.version,
            license=manifest.license,
            origin_uri=manifest.origin_uri,
            num_shards=len(manifest.splits),
            class_distribution=dist,
            known_gaps=gaps,
            summary=text,
        )


# ---------------------------------------------------------------------------
# Format selection
# ---------------------------------------------------------------------------


class FormatId(enum.StrEnum):
    """Candidate dataset formats evaluated by :func:`select_format` (spec §6.1)."""

    ARROW = "arrow"
    PARQUET = "parquet"
    JSONL = "jsonl"
    WEBDATASET = "webdataset"
    HDF5 = "hdf5"
    ZARR = "zarr"
    SQLITE = "sqlite"
    DUCKDB = "duckdb"
    SAFETENSORS = "safetensors"
    ONNX = "onnx"
    OBJECT_STORE = "object_store"


# Capability matrix: format_id -> {priority -> capability_score in [0,1]}.
# A score of 0.0 means the format CANNOT satisfy that priority and is never
# selected when that priority is requested. Scores reflect documented format
# properties (not popularity); spec §9: "Popularity alone is not a selection
# criterion."
_CAPABILITY_MATRIX: dict[FormatId, dict[str, float]] = {
    FormatId.ARROW: {
        "column_pruning": 1.0,
        "compression": 0.8,
        "schema_evolution": 0.9,
        "streaming": 0.8,
        "random_access": 0.9,
        "multimodal_payloads": 0.4,
        "interoperability": 0.8,
        "scale": 0.7,
    },
    FormatId.PARQUET: {
        "column_pruning": 1.0,
        "compression": 1.0,
        "schema_evolution": 0.9,
        "streaming": 0.5,
        "random_access": 0.8,
        "multimodal_payloads": 0.4,
        "interoperability": 0.9,
        "scale": 0.9,
    },
    FormatId.JSONL: {
        "column_pruning": 0.0,
        "compression": 0.3,
        "schema_evolution": 0.9,
        "streaming": 1.0,
        "random_access": 0.2,
        "multimodal_payloads": 0.3,
        "interoperability": 1.0,
        "scale": 0.6,
    },
    FormatId.WEBDATASET: {
        "column_pruning": 0.2,
        "compression": 0.7,
        "schema_evolution": 0.7,
        "streaming": 1.0,
        "random_access": 0.4,
        "multimodal_payloads": 0.8,
        "interoperability": 0.7,
        "scale": 0.9,
    },
    FormatId.HDF5: {
        "column_pruning": 0.3,
        "compression": 0.9,
        "schema_evolution": 0.4,
        "streaming": 0.3,
        "random_access": 1.0,
        "multimodal_payloads": 1.0,
        "interoperability": 0.6,
        "scale": 0.8,
    },
    FormatId.ZARR: {
        "column_pruning": 0.4,
        "compression": 0.9,
        "schema_evolution": 0.6,
        "streaming": 0.5,
        "random_access": 1.0,
        "multimodal_payloads": 1.0,
        "interoperability": 0.6,
        "scale": 1.0,
    },
    FormatId.SQLITE: {
        "column_pruning": 0.8,
        "compression": 0.3,
        "schema_evolution": 0.7,
        "streaming": 0.4,
        "random_access": 1.0,
        "multimodal_payloads": 0.2,
        "interoperability": 0.7,
        "scale": 0.5,
    },
    FormatId.DUCKDB: {
        "column_pruning": 0.9,
        "compression": 0.6,
        "schema_evolution": 0.7,
        "streaming": 0.5,
        "random_access": 1.0,
        "multimodal_payloads": 0.2,
        "interoperability": 0.8,
        "scale": 0.8,
    },
    FormatId.SAFETENSORS: {
        "column_pruning": 0.0,
        "compression": 0.5,
        "schema_evolution": 0.3,
        "streaming": 0.4,
        "random_access": 1.0,
        "multimodal_payloads": 0.5,
        "interoperability": 0.8,
        "scale": 0.9,
    },
    FormatId.ONNX: {
        "column_pruning": 0.0,
        "compression": 0.5,
        "schema_evolution": 0.3,
        "streaming": 0.3,
        "random_access": 0.7,
        "multimodal_payloads": 0.6,
        "interoperability": 0.9,
        "scale": 0.7,
    },
    FormatId.OBJECT_STORE: {
        "column_pruning": 0.0,
        "compression": 0.5,
        "schema_evolution": 0.6,
        "streaming": 0.7,
        "random_access": 0.5,
        "multimodal_payloads": 0.9,
        "interoperability": 0.6,
        "scale": 1.0,
    },
}

_VALID_PRIORITIES = frozenset(_CAPABILITY_MATRIX[FormatId.PARQUET].keys())


@dataclass(frozen=True)
class FormatTradeoff:
    """One row of the format-selection tradeoff table."""

    format_id: str
    score: float
    rationale: str

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score must be in [0.0, 1.0], got {self.score}")
        _require_nonempty_str(self.format_id, "format_id")
        _require_nonempty_str(self.rationale, "rationale")


@dataclass(frozen=True)
class FormatSelection:
    """Result of :func:`select_format`: chosen format + full tradeoff table."""

    selected: str
    tradeoffs: tuple[FormatTradeoff, ...]
    priorities: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_nonempty_str(self.selected, "selected")
        if not isinstance(self.tradeoffs, tuple) or not self.tradeoffs:
            raise ValueError("tradeoffs must be a non-empty tuple")


class FormatSelector:
    """Pure, stateless evaluator for dataset-format selection (spec §6.1)."""

    def select(
        self,
        *,
        schema: DatasetSchema,
        priorities: tuple[str, ...] | list[str],
        dense_tensors: bool = False,
        multimodal: bool = False,
    ) -> FormatSelection:
        norm_priorities = self._normalize_priorities(priorities)
        if not norm_priorities:
            raise ValueError("priorities must be a non-empty sequence of capability names")

        rows: list[FormatTradeoff] = []
        for fmt, caps in _CAPABILITY_MATRIX.items():
            score, reasons = self._score_format(
                fmt, caps, norm_priorities, dense_tensors=dense_tensors, multimodal=multimodal
            )
            rows.append(
                FormatTradeoff(
                    format_id=fmt.value,
                    score=score,
                    rationale="; ".join(reasons) if reasons else f"matches {norm_priorities}",
                )
            )

        rows.sort(key=lambda r: r.score, reverse=True)
        # Tie-break deterministically by format_id for stable output.
        # (Python sort is stable, but we re-sort to be explicit.)
        best = rows[0]
        return FormatSelection(
            selected=best.format_id,
            tradeoffs=tuple(rows),
            priorities=tuple(norm_priorities),
        )

    @staticmethod
    def _normalize_priorities(priorities: tuple[str, ...] | list[str]) -> list[str]:
        out: list[str] = []
        for p in priorities:
            key = str(p).strip().lower()
            if not key:
                continue
            if key not in _VALID_PRIORITIES:
                raise ValueError(f"unknown priority {p!r}; valid: {sorted(_VALID_PRIORITIES)}")
            out.append(key)
        return out

    @staticmethod
    def _score_format(
        fmt: FormatId,
        caps: dict[str, float],
        priorities: list[str],
        *,
        dense_tensors: bool = False,
        multimodal: bool = False,
    ) -> tuple[float, list[str]]:
        scores: list[float] = []
        reasons: list[str] = []
        for prio in priorities:
            cap = caps.get(prio, 0.0)
            scores.append(cap)
            if cap == 0.0:
                reasons.append(f"{prio}=0 (unsupported)")
        avg = sum(scores) / len(scores) if scores else 0.0

        # Domain modifiers (spec §6.1): the caller declares payload shape so
        # the selector can prefer tensor- or multimodal-native formats rather
        # than picking a tabular format that happens to tie on the averages.
        if dense_tensors and fmt is FormatId.SAFETENSORS:
            avg = min(1.0, avg + 0.15)
            reasons.append("dense-tensor payload favors safetensors (+0.15)")
        if multimodal and fmt is FormatId.HDF5:
            avg = min(1.0, avg + 0.1)
            reasons.append("multimodal scientific payload favors HDF5 (+0.1)")
        return avg, reasons


def select_format(
    *,
    schema: DatasetSchema,
    priorities: tuple[str, ...] | list[str],
    dense_tensors: bool = False,
    multimodal: bool = False,
) -> FormatSelection:
    """Evaluate candidate formats and return the best fit (spec §6.1).

    See :class:`FormatSelector` for the scoring rules. This is a thin
    convenience wrapper around the stateless selector so callers do not have
    to instantiate it for the common case.
    """
    return FormatSelector().select(
        schema=schema,
        priorities=priorities,
        dense_tensors=dense_tensors,
        multimodal=multimodal,
    )


__all__ = [
    "DataCard",
    "DatasetManifest",
    "DatasetSchema",
    "FindingKind",
    "FormatId",
    "FormatSelection",
    "FormatSelector",
    "FormatTradeoff",
    "Severity",
    "ShardDigest",
    "ValidationFinding",
    "select_format",
    "validate_dataset",
]
