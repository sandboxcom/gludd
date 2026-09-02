"""Canonical durable result artifacts for managed self-improvement runs."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final

from general_ludd.self_improve.codex_comparison import (
    CandidateEvidence,
    ComparisonResult,
    ProposalManifest,
)
from general_ludd.self_improve.managed_runner import (
    AttemptResult,
    ManagedRunResult,
)

_ARTIFACT_KIND: Final = "managed_self_improve_result"
_SCHEMA_VERSION: Final = 1
_MAX_ARTIFACT_BYTES: Final = 5_242_880
_MAX_DIAGNOSTIC_BYTES: Final = 65_536
_MAX_IDENTITY_ITEMS: Final = 32
_MAX_CHANGED_FILES: Final = 256
_DIGEST_RE: Final = re.compile(r"^[0-9a-f]{64}$")


def _canonical_json(value: object) -> str:
    """Encode one deterministic standards-compliant JSON value."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    """Return the SHA-256 identity of one canonical JSON value."""
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _reject_duplicate_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate field names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"managed result artifact has duplicate field: {key}")
        result[key] = value
    return result


def _require_exact_mapping(
    value: object,
    fields: frozenset[str],
    *,
    label: str,
) -> dict[str, Any]:
    """Return an exact string-keyed mapping or reject schema ambiguity."""
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    unknown = set(value) - fields
    missing = fields - set(value)
    if unknown:
        raise ValueError(f"{label} has unknown fields: {sorted(unknown)}")
    if missing:
        raise ValueError(f"{label} is missing fields: {sorted(missing)}")
    return value


def _require_bool(label: str, value: object) -> bool:
    """Return a strict JSON boolean."""
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _require_int(
    label: str,
    value: object,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    """Return a bounded integer while rejecting booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        upper = "unbounded" if maximum is None else str(maximum)
        raise ValueError(f"{label} must be between {minimum} and {upper}")
    return value


def _require_float(
    label: str,
    value: object,
    *,
    minimum: float,
    maximum: float | None = None,
) -> float:
    """Return a finite bounded number while rejecting booleans."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if number < minimum or (maximum is not None and number > maximum):
        upper = "unbounded" if maximum is None else str(maximum)
        raise ValueError(f"{label} must be between {minimum} and {upper}")
    return number


def _require_text(
    label: str,
    value: object,
    *,
    maximum_bytes: int,
    allow_empty: bool = False,
) -> str:
    """Return bounded control-free UTF-8 text."""
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    if len(value.encode("utf-8")) > maximum_bytes:
        raise ValueError(f"{label} exceeds {maximum_bytes} bytes")
    if "\x00" in value:
        raise ValueError(f"{label} must not contain NUL")
    return value


def _require_digest(label: str, value: object) -> str:
    """Return one canonical lowercase SHA-256 digest."""
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be 64 lowercase hex characters")
    return value


def _require_text_tuple(
    label: str,
    value: object,
    *,
    maximum_items: int,
    maximum_item_bytes: int,
) -> tuple[str, ...]:
    """Return one bounded duplicate-free tuple of strings."""
    if not isinstance(value, tuple) or len(value) > maximum_items:
        raise ValueError(f"{label} must be a tuple with at most {maximum_items} items")
    result = tuple(
        _require_text(
            f"{label} item",
            item,
            maximum_bytes=maximum_item_bytes,
        )
        for item in value
    )
    if len(set(result)) != len(result):
        raise ValueError(f"{label} must not contain duplicates")
    return result


def _require_repo_path(value: object) -> str:
    """Return one confined POSIX repository-relative path."""
    path = _require_text("changed file", value, maximum_bytes=4096)
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or "\\" in path
        or path in {".", ".."}
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ValueError(f"changed file is not repository-relative: {path!r}")
    return path


def _proposal_value(proposal: ProposalManifest) -> dict[str, Any]:
    """Revalidate and return a proposal as a canonical JSON mapping."""
    if not isinstance(proposal, ProposalManifest):
        raise ValueError("proposal must be a ProposalManifest")
    raw = proposal.to_json()
    validated = ProposalManifest.from_json(raw)
    value = json.loads(validated.to_json())
    if not isinstance(value, dict):
        raise ValueError("proposal serialization must be a JSON object")
    return value


def _evidence_value(evidence: CandidateEvidence) -> dict[str, object]:
    """Return canonical evidence after strict validation."""
    if not isinstance(evidence, CandidateEvidence):
        raise ValueError("evidence must be CandidateEvidence")
    changed_files = tuple(sorted(_require_repo_path(path) for path in evidence.changed_files))
    if not changed_files or len(changed_files) > _MAX_CHANGED_FILES:
        raise ValueError(
            f"evidence changed_files must contain 1..{_MAX_CHANGED_FILES} paths"
        )
    if len(set(changed_files)) != len(changed_files):
        raise ValueError("evidence changed_files must not contain duplicates")
    return {
        "changed_files": list(changed_files),
        "tests_passed": _require_bool("tests_passed", evidence.tests_passed),
        "warnings": _require_int("warnings", evidence.warnings),
        "coverage_aggregate": _require_float(
            "coverage_aggregate",
            evidence.coverage_aggregate,
            minimum=0.0,
            maximum=100.0,
        ),
        "coverage_min_file": _require_float(
            "coverage_min_file",
            evidence.coverage_min_file,
            minimum=0.0,
            maximum=100.0,
        ),
        "ruff_passed": _require_bool("ruff_passed", evidence.ruff_passed),
        "mypy_passed": _require_bool("mypy_passed", evidence.mypy_passed),
        "docstrings_passed": _require_bool(
            "docstrings_passed", evidence.docstrings_passed
        ),
        "markdown_passed": _require_bool(
            "markdown_passed", evidence.markdown_passed
        ),
        "cleanup_passed": _require_bool("cleanup_passed", evidence.cleanup_passed),
        "commit_count": _require_int("commit_count", evidence.commit_count),
        "worktree_clean": _require_bool("worktree_clean", evidence.worktree_clean),
        "elapsed_seconds": _require_float(
            "elapsed_seconds",
            evidence.elapsed_seconds,
            minimum=0.0,
        ),
        "changed_lines": _require_int("changed_lines", evidence.changed_lines),
    }


def _comparison_value(comparison: ComparisonResult) -> dict[str, object]:
    """Return canonical comparison data after strict validation."""
    if not isinstance(comparison, ComparisonResult):
        raise ValueError("comparison must be ComparisonResult")
    blockers = _require_text_tuple(
        "blockers",
        comparison.blockers,
        maximum_items=64,
        maximum_item_bytes=256,
    )
    return {
        "accepted": _require_bool("comparison accepted", comparison.accepted),
        "score": _require_float("score", comparison.score, minimum=0.0, maximum=100.0),
        "blockers": list(blockers),
        "changed_file_precision": _require_float(
            "changed_file_precision",
            comparison.changed_file_precision,
            minimum=0.0,
            maximum=1.0,
        ),
        "changed_file_recall": _require_float(
            "changed_file_recall",
            comparison.changed_file_recall,
            minimum=0.0,
            maximum=1.0,
        ),
    }


@dataclass(frozen=True, slots=True)
class ManagedSelfImproveResultArtifact:
    """Versioned, digest-bound proposal and evidence for later review."""

    accepted: bool
    attempts: int
    plan_identity_digest: str
    attempt_identity_digest: str
    attempted_model_ids: tuple[str, ...]
    outcome_record_ids: tuple[str, ...]
    proposal: ProposalManifest
    evidence: CandidateEvidence
    comparison: ComparisonResult
    patch_equivalence: str
    diagnostics: str
    artifact_digest: str = ""

    def __post_init__(self) -> None:
        """Validate every nested result and bind it to a canonical digest."""
        _require_bool("accepted", self.accepted)
        _require_int("attempts", self.attempts, minimum=1, maximum=32)
        _require_digest("plan identity digest", self.plan_identity_digest)
        _require_digest("attempt identity digest", self.attempt_identity_digest)
        attempted_models = _require_text_tuple(
            "attempted_model_ids",
            self.attempted_model_ids,
            maximum_items=_MAX_IDENTITY_ITEMS,
            maximum_item_bytes=512,
        )
        outcome_records = _require_text_tuple(
            "outcome_record_ids",
            self.outcome_record_ids,
            maximum_items=_MAX_IDENTITY_ITEMS,
            maximum_item_bytes=512,
        )
        if len(attempted_models) > self.attempts or len(outcome_records) > self.attempts:
            raise ValueError("attempt identity collections cannot exceed attempts")
        proposal_value = _proposal_value(self.proposal)
        evidence_value = _evidence_value(self.evidence)
        comparison_value = _comparison_value(self.comparison)
        if self.accepted is not self.comparison.accepted:
            raise ValueError("artifact acceptance must match the comparison")
        proposal_paths = {str(edit["path"]) for edit in proposal_value["edits"]}
        if proposal_paths != set(self.evidence.changed_files):
            raise ValueError("evidence changed_files must match the proposal edits")
        _require_text(
            "patch_equivalence",
            self.patch_equivalence,
            maximum_bytes=4096,
        )
        _require_text(
            "diagnostics",
            self.diagnostics,
            maximum_bytes=_MAX_DIAGNOSTIC_BYTES,
            allow_empty=True,
        )

        expected_digest = _digest(
            self._payload(
                proposal_value=proposal_value,
                evidence_value=evidence_value,
                comparison_value=comparison_value,
            )
        )
        if self.artifact_digest:
            supplied = _require_digest("artifact digest", self.artifact_digest)
            if not hmac.compare_digest(supplied, expected_digest):
                raise ValueError("artifact digest does not match the result payload")
        else:
            object.__setattr__(self, "artifact_digest", expected_digest)

    @classmethod
    def from_run_result(
        cls,
        result: ManagedRunResult,
    ) -> ManagedSelfImproveResultArtifact:
        """Build a strict durable artifact from one managed runtime result."""
        if not isinstance(result, ManagedRunResult):
            raise ValueError("managed result must be ManagedRunResult")
        final = result.final_result
        if not isinstance(final, AttemptResult):
            raise ValueError("managed final result must be AttemptResult")
        if not isinstance(final.comparison, ComparisonResult):
            raise ValueError("managed comparison must be ComparisonResult")
        return cls(
            accepted=final.comparison.accepted,
            attempts=result.attempts,
            plan_identity_digest=result.plan_identity_digest,
            attempt_identity_digest=result.attempt_identity_digest,
            attempted_model_ids=result.attempted_model_ids,
            outcome_record_ids=result.outcome_record_ids,
            proposal=final.proposal,
            evidence=final.evidence,
            comparison=final.comparison,
            patch_equivalence=final.patch_equivalence,
            diagnostics=final.diagnostics,
        )

    @classmethod
    def from_json(cls, raw: str) -> ManagedSelfImproveResultArtifact:
        """Decode and verify one exact, bounded managed-result artifact."""
        if not isinstance(raw, str):
            raise ValueError("managed result artifact must be JSON text")
        if len(raw.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ValueError(f"managed result artifact exceeds {_MAX_ARTIFACT_BYTES} bytes")
        try:
            value = json.loads(raw, object_pairs_hook=_reject_duplicate_fields)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"managed result artifact is not valid JSON: {exc}") from exc
        fields = frozenset(
            {
                "accepted",
                "artifact_digest",
                "attempt_identity_digest",
                "attempted_model_ids",
                "attempts",
                "comparison",
                "diagnostics",
                "evidence",
                "kind",
                "outcome_record_ids",
                "patch_equivalence",
                "plan_identity_digest",
                "proposal",
                "schema_version",
            }
        )
        payload = _require_exact_mapping(value, fields, label="managed result artifact")
        if payload["kind"] != _ARTIFACT_KIND:
            raise ValueError(f"managed result artifact kind must be {_ARTIFACT_KIND!r}")
        schema_version = _require_int(
            "managed result artifact schema_version",
            payload["schema_version"],
            minimum=_SCHEMA_VERSION,
            maximum=_SCHEMA_VERSION,
        )
        if schema_version != _SCHEMA_VERSION:
            raise ValueError("managed result artifact schema_version must be 1")

        proposal = ProposalManifest.from_json(_canonical_json(payload["proposal"]))
        evidence = _parse_evidence(payload["evidence"])
        comparison = _parse_comparison(payload["comparison"])
        attempted_model_ids = _parse_text_list(
            "attempted_model_ids", payload["attempted_model_ids"]
        )
        outcome_record_ids = _parse_text_list(
            "outcome_record_ids", payload["outcome_record_ids"]
        )
        return cls(
            accepted=_require_bool("accepted", payload["accepted"]),
            attempts=_require_int(
                "attempts", payload["attempts"], minimum=1, maximum=32
            ),
            plan_identity_digest=_require_digest(
                "plan identity digest", payload["plan_identity_digest"]
            ),
            attempt_identity_digest=_require_digest(
                "attempt identity digest", payload["attempt_identity_digest"]
            ),
            attempted_model_ids=attempted_model_ids,
            outcome_record_ids=outcome_record_ids,
            proposal=proposal,
            evidence=evidence,
            comparison=comparison,
            patch_equivalence=_require_text(
                "patch_equivalence",
                payload["patch_equivalence"],
                maximum_bytes=4096,
            ),
            diagnostics=_require_text(
                "diagnostics",
                payload["diagnostics"],
                maximum_bytes=_MAX_DIAGNOSTIC_BYTES,
                allow_empty=True,
            ),
            artifact_digest=_require_digest(
                "artifact digest", payload["artifact_digest"]
            ),
        )

    def to_json(self) -> str:
        """Encode this validated artifact as canonical JSON."""
        payload = self._payload(
            proposal_value=_proposal_value(self.proposal),
            evidence_value=_evidence_value(self.evidence),
            comparison_value=_comparison_value(self.comparison),
        )
        payload["artifact_digest"] = self.artifact_digest
        encoded = _canonical_json(payload)
        if len(encoded.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ValueError(f"managed result artifact exceeds {_MAX_ARTIFACT_BYTES} bytes")
        return encoded

    def _payload(
        self,
        *,
        proposal_value: dict[str, Any],
        evidence_value: dict[str, object],
        comparison_value: dict[str, object],
    ) -> dict[str, object]:
        """Return the digest input without its self-referential digest field."""
        return {
            "accepted": self.accepted,
            "attempt_identity_digest": self.attempt_identity_digest,
            "attempted_model_ids": list(self.attempted_model_ids),
            "attempts": self.attempts,
            "comparison": comparison_value,
            "diagnostics": self.diagnostics,
            "evidence": evidence_value,
            "kind": _ARTIFACT_KIND,
            "outcome_record_ids": list(self.outcome_record_ids),
            "patch_equivalence": self.patch_equivalence,
            "plan_identity_digest": self.plan_identity_digest,
            "proposal": proposal_value,
            "schema_version": _SCHEMA_VERSION,
        }


def _parse_text_list(label: str, value: object) -> tuple[str, ...]:
    """Parse one JSON list before applying tuple validation."""
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON list")
    return _require_text_tuple(
        label,
        tuple(value),
        maximum_items=_MAX_IDENTITY_ITEMS,
        maximum_item_bytes=512,
    )


def _parse_evidence(value: object) -> CandidateEvidence:
    """Parse exact deterministic candidate evidence."""
    fields = frozenset(
        {
            "changed_files",
            "changed_lines",
            "cleanup_passed",
            "commit_count",
            "coverage_aggregate",
            "coverage_min_file",
            "docstrings_passed",
            "elapsed_seconds",
            "markdown_passed",
            "mypy_passed",
            "ruff_passed",
            "tests_passed",
            "warnings",
            "worktree_clean",
        }
    )
    payload = _require_exact_mapping(value, fields, label="managed result evidence")
    changed_files_raw = payload["changed_files"]
    if not isinstance(changed_files_raw, list):
        raise ValueError("evidence changed_files must be a JSON list")
    changed_files = frozenset(_require_repo_path(path) for path in changed_files_raw)
    if len(changed_files) != len(changed_files_raw):
        raise ValueError("evidence changed_files must not contain duplicates")
    return CandidateEvidence(
        changed_files=changed_files,
        tests_passed=_require_bool("tests_passed", payload["tests_passed"]),
        warnings=_require_int("warnings", payload["warnings"]),
        coverage_aggregate=_require_float(
            "coverage_aggregate",
            payload["coverage_aggregate"],
            minimum=0.0,
            maximum=100.0,
        ),
        coverage_min_file=_require_float(
            "coverage_min_file",
            payload["coverage_min_file"],
            minimum=0.0,
            maximum=100.0,
        ),
        ruff_passed=_require_bool("ruff_passed", payload["ruff_passed"]),
        mypy_passed=_require_bool("mypy_passed", payload["mypy_passed"]),
        docstrings_passed=_require_bool(
            "docstrings_passed", payload["docstrings_passed"]
        ),
        markdown_passed=_require_bool(
            "markdown_passed", payload["markdown_passed"]
        ),
        cleanup_passed=_require_bool("cleanup_passed", payload["cleanup_passed"]),
        commit_count=_require_int("commit_count", payload["commit_count"]),
        worktree_clean=_require_bool("worktree_clean", payload["worktree_clean"]),
        elapsed_seconds=_require_float(
            "elapsed_seconds", payload["elapsed_seconds"], minimum=0.0
        ),
        changed_lines=_require_int("changed_lines", payload["changed_lines"]),
    )


def _parse_comparison(value: object) -> ComparisonResult:
    """Parse exact deterministic comparison evidence."""
    fields = frozenset(
        {
            "accepted",
            "blockers",
            "changed_file_precision",
            "changed_file_recall",
            "score",
        }
    )
    payload = _require_exact_mapping(value, fields, label="managed result comparison")
    blockers_raw = payload["blockers"]
    if not isinstance(blockers_raw, list):
        raise ValueError("comparison blockers must be a JSON list")
    blockers = _require_text_tuple(
        "blockers",
        tuple(blockers_raw),
        maximum_items=64,
        maximum_item_bytes=256,
    )
    return ComparisonResult(
        accepted=_require_bool("comparison accepted", payload["accepted"]),
        score=_require_float(
            "score", payload["score"], minimum=0.0, maximum=100.0
        ),
        blockers=blockers,
        changed_file_precision=_require_float(
            "changed_file_precision",
            payload["changed_file_precision"],
            minimum=0.0,
            maximum=1.0,
        ),
        changed_file_recall=_require_float(
            "changed_file_recall",
            payload["changed_file_recall"],
            minimum=0.0,
            maximum=1.0,
        ),
    )
