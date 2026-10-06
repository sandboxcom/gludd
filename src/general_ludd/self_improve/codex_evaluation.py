"""Proposal aggregation, evidence scoring, and bounded retry feedback."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from general_ludd.self_improve.codex_protocol import (
    _MAX_BLOCKER_BYTES,
    _MAX_BLOCKERS,
    _MAX_PLANNER_FEEDBACK_BYTES,
    _MAX_TASK_OBJECTIVE_BYTES,
    _PLANNER_FEEDBACK_KIND,
    _PLANNER_FEEDBACK_SCHEMA_VERSION,
    _PLANNER_FEEDBACK_SOURCE_KIND,
    _PROTOCOL_DIGEST_RE,
    _SECRET_ASSIGNMENT_RE,
    _SHA_RE,
    _SNAPSHOT_MANIFEST_SCHEMA_VERSION,
    _TASK_RE,
    EVALUATION_DIAGNOSIS_PROTOCOL,
    ProposalManifest,
    _parent_proposal_error,
    _safe_relative_path,
)
from general_ludd.self_improve.model_lifecycle import ModelArtifactIdentity


def _validate_proposal_preconditions(
    proposal: ProposalManifest,
    expected_baseline_files: Mapping[str, str | None],
) -> None:
    """Validate every edit sequentially against an immutable parent snapshot."""
    expected_paths = {edit.path for edit in proposal.edits}
    if set(expected_baseline_files) != expected_paths:
        raise _parent_proposal_error(
            "trusted baseline files must cover the exact proposal paths"
        )
    planned: dict[str, str | None] = {}
    for path, content in expected_baseline_files.items():
        if not isinstance(path, str) or not _safe_relative_path(path):
            raise _parent_proposal_error("trusted baseline contains an unsafe path")
        if content is not None and not isinstance(content, str):
            raise _parent_proposal_error("trusted baseline content must be UTF-8 text")
        planned[path] = content

    for edit in proposal.edits:
        current = planned[edit.path]
        if edit.operation == "replace":
            if proposal.schema_version == _SNAPSHOT_MANIFEST_SCHEMA_VERSION:
                if current is None or current != edit.old_text:
                    raise _parent_proposal_error(
                        "replace old_text must equal the complete trusted snapshot"
                    )
                planned[edit.path] = edit.new_text
            else:
                if current is None or current.count(edit.old_text) != 1:
                    raise _parent_proposal_error(
                        "replace old_text must occur exactly once in trusted baseline"
                    )
                planned[edit.path] = current.replace(edit.old_text, edit.new_text, 1)
        elif edit.operation == "create":
            if current is not None:
                raise _parent_proposal_error(
                    "create target must be absent in trusted baseline"
                )
            planned[edit.path] = edit.new_text
        elif edit.operation == "delete":
            if current is None or current != edit.old_text:
                raise _parent_proposal_error(
                    "delete old_text must equal the complete trusted baseline file"
                )
            planned[edit.path] = None
        else:
            raise _parent_proposal_error("proposal operation is unsupported")


def merge_proposal_manifests(
    manifests: tuple[ProposalManifest, ...],
    *,
    expected_path_groups: tuple[tuple[str, ...], ...],
    expected_baseline_sha: str,
    expected_task_id: str,
    expected_tests: tuple[str, ...],
    expected_make_commands: tuple[str, ...],
    expected_baseline_files: Mapping[str, str | None] | None = None,
) -> ProposalManifest:
    """Merge disjoint shard manifests without weakening the final schema."""
    if not manifests or len(manifests) != len(expected_path_groups):
        raise ValueError("proposal shard count does not match the prompt plan")
    if not _SHA_RE.fullmatch(expected_baseline_sha):
        raise ValueError("expected baseline identity is invalid")
    if not _TASK_RE.fullmatch(expected_task_id):
        raise ValueError("expected task identity is invalid")
    if not expected_tests or len(set(expected_tests)) != len(expected_tests):
        raise ValueError("expected tests must be a non-empty unique identity set")
    if not expected_make_commands:
        raise ValueError("expected Make commands must not be empty")

    expected_paths: set[str] = set()
    for group in expected_path_groups:
        if not group:
            raise ValueError("every prompt shard must have focus paths")
        if len(set(group)) != len(group) or expected_paths.intersection(group):
            raise ValueError("prompt shard focus paths must be disjoint")
        if any(not _safe_relative_path(path) for path in group):
            raise ValueError("prompt shard focus path is unsafe")
        expected_paths.update(group)

    manifest_schema_version = manifests[0].schema_version
    edits: list[dict[str, str]] = []
    for manifest, focus_paths in zip(manifests, expected_path_groups, strict=True):
        if manifest.schema_version != manifest_schema_version:
            raise ValueError("proposal shard manifest schema drifted")
        if manifest.baseline_sha != expected_baseline_sha:
            raise ValueError("proposal shard baseline identity drifted")
        if manifest.task_id != expected_task_id:
            raise ValueError("proposal shard task identity drifted")
        actual_paths = {edit.path for edit in manifest.edits}
        if actual_paths != set(focus_paths):
            raise ValueError("proposal shard edits must cover the exact focus paths")
        if (
            len(manifest.tests) != len(expected_tests)
            or frozenset(manifest.tests) != frozenset(expected_tests)
        ):
            raise ValueError("proposal shard test identity drifted")
        if manifest.make_commands != expected_make_commands:
            raise ValueError("proposal shard Make command identity drifted")
        edits.extend(
            {
                "operation": edit.operation,
                "path": edit.path,
                "old_text": edit.old_text,
                "new_text": edit.new_text,
            }
            for edit in manifest.edits
        )

    if {str(edit["path"]) for edit in edits} != expected_paths:
        raise ValueError("merged proposal does not cover every expected path")
    merged = ProposalManifest.from_json(
        json.dumps(
            {
                "schema_version": manifest_schema_version,
                "baseline_sha": expected_baseline_sha,
                "task_id": expected_task_id,
                "edits": edits,
                "tests": list(expected_tests),
                "make_commands": list(expected_make_commands),
                "commit_message": manifests[0].commit_message,
            }
        )
    )
    if expected_baseline_files is not None:
        _validate_proposal_preconditions(merged, expected_baseline_files)
    return merged


@dataclass(frozen=True)
class CandidateEvidence:
    """Deterministic gate and repository evidence for one applied proposal."""

    changed_files: frozenset[str]
    tests_passed: bool
    warnings: int
    coverage_aggregate: float
    coverage_min_file: float
    ruff_passed: bool
    mypy_passed: bool
    docstrings_passed: bool
    markdown_passed: bool
    cleanup_passed: bool
    commit_count: int
    worktree_clean: bool
    elapsed_seconds: float
    changed_lines: int = 0


@dataclass(frozen=True)
class CodexReference:
    """Independent Codex patch boundary used as the comparison oracle."""

    baseline_sha: str
    reference_sha: str
    changed_files: frozenset[str]
    test_files: frozenset[str]
    changed_lines: int
    elapsed_seconds: float


@dataclass(frozen=True)
class ComparisonResult:
    """Scored parity result and deterministic retry feedback."""

    accepted: bool
    score: float
    blockers: tuple[str, ...]
    changed_file_precision: float
    changed_file_recall: float


def _reject_feedback_duplicate_fields(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    """Reject ambiguous duplicate JSON keys in a planner exchange."""
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"planner feedback contains duplicate field: {key}")
        result[key] = value
    return result


def _feedback_mapping(
    value: object,
    fields: frozenset[str],
    *,
    label: str,
) -> dict[str, object]:
    """Return an exact JSON mapping or reject missing and unknown fields."""
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"planner feedback {label} fields are incomplete or unknown")
    return cast(dict[str, object], value)


def _feedback_digest(value: object, label: str) -> str:
    """Validate one immutable SHA-256 identity in a planner exchange."""
    if not isinstance(value, str) or _PROTOCOL_DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"planner feedback {label} must be lowercase SHA-256")
    return value


def _feedback_outcome(value: object) -> ComparisonResult:
    """Hydrate and validate the comparison-only outcome subdocument."""
    mapping = _feedback_mapping(
        value,
        frozenset(
            {
                "accepted",
                "blockers",
                "changed_file_precision",
                "changed_file_recall",
                "score",
            }
        ),
        label="outcome",
    )
    accepted = mapping["accepted"]
    score = mapping["score"]
    precision = mapping["changed_file_precision"]
    recall = mapping["changed_file_recall"]
    blockers = mapping["blockers"]
    if not isinstance(accepted, bool):
        raise ValueError("planner feedback accepted outcome must be a boolean")
    for label, metric, maximum in (
        ("score", score, 100.0),
        ("changed_file_precision", precision, 1.0),
        ("changed_file_recall", recall, 1.0),
    ):
        if (
            isinstance(metric, bool)
            or not isinstance(metric, (int, float))
            or not math.isfinite(float(metric))
            or not 0.0 <= float(metric) <= maximum
        ):
            raise ValueError(f"planner feedback {label} is outside its valid range")
    if not isinstance(blockers, list) or len(blockers) > _MAX_BLOCKERS:
        raise ValueError("planner feedback blockers must be a bounded JSON list")
    normalized_blockers = tuple(blockers)
    if any(
        not isinstance(item, str)
        or not item
        or item.strip() != item
        or len(item.encode("utf-8")) > _MAX_BLOCKER_BYTES
        for item in normalized_blockers
    ) or len(set(normalized_blockers)) != len(normalized_blockers):
        raise ValueError("planner feedback blockers must be unique bounded text")
    normalized_score = float(cast(int | float, score))
    normalized_precision = float(cast(int | float, precision))
    normalized_recall = float(cast(int | float, recall))
    if accepted is not (normalized_score == 100.0 and not normalized_blockers):
        raise ValueError("planner feedback acceptance contradicts its comparison")
    return ComparisonResult(
        accepted=accepted,
        score=normalized_score,
        blockers=normalized_blockers,
        changed_file_precision=normalized_precision,
        changed_file_recall=normalized_recall,
    )


@dataclass(frozen=True, slots=True)
class PlannerFeedbackExchange:
    """Exact immutable bridge from a managed result into model planning."""

    plan_identity_digest: str
    attempt_identity_digest: str
    attempt_number: int
    model_identity: ModelArtifactIdentity
    task_id: str
    task_objective: str
    outcome: ComparisonResult
    source_artifact_digest: str

    def __post_init__(self) -> None:
        """Reconcile every plan, attempt, model, task, and outcome field."""
        _feedback_digest(self.plan_identity_digest, "plan identity digest")
        _feedback_digest(self.attempt_identity_digest, "attempt identity digest")
        _feedback_digest(self.source_artifact_digest, "source artifact digest")
        if (
            isinstance(self.attempt_number, bool)
            or not isinstance(self.attempt_number, int)
            or not 1 <= self.attempt_number <= 32
        ):
            raise ValueError("planner feedback attempt_number must be between 1 and 32")
        if not isinstance(self.model_identity, ModelArtifactIdentity):
            raise ValueError("planner feedback model_identity must be immutable")
        if not isinstance(self.task_id, str) or _TASK_RE.fullmatch(self.task_id) is None:
            raise ValueError("planner feedback task_id is not canonical")
        if (
            not isinstance(self.task_objective, str)
            or not self.task_objective
            or self.task_objective.strip() != self.task_objective
            or "\x00" in self.task_objective
            or len(self.task_objective.encode("utf-8")) > _MAX_TASK_OBJECTIVE_BYTES
        ):
            raise ValueError("planner feedback task_objective must be bounded text")
        if not isinstance(self.outcome, ComparisonResult):
            raise ValueError("planner feedback outcome must be a ComparisonResult")
        _feedback_outcome(self._outcome_value())

    def to_json(self) -> str:
        """Serialize the exact exchange as bounded canonical JSON."""
        encoded = json.dumps(
            self._json_value(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded.encode("utf-8")) > _MAX_PLANNER_FEEDBACK_BYTES:
            raise ValueError("planner feedback exchange exceeds its byte bound")
        return encoded

    @classmethod
    def from_json(cls, raw: str) -> PlannerFeedbackExchange:
        """Hydrate one canonical exchange and reject schema ambiguity."""
        if (
            not isinstance(raw, str)
            or not raw
            or len(raw.encode("utf-8")) > _MAX_PLANNER_FEEDBACK_BYTES
        ):
            raise ValueError("planner feedback exchange must be bounded JSON text")
        try:
            value = json.loads(raw, object_pairs_hook=_reject_feedback_duplicate_fields)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"planner feedback exchange is not valid JSON: {exc}") from exc
        mapping = _feedback_mapping(
            value,
            frozenset(
                {
                    "attempt_identity_digest",
                    "attempt_number",
                    "kind",
                    "model_identity",
                    "outcome",
                    "plan_identity_digest",
                    "schema_version",
                    "source",
                    "task",
                }
            ),
            label="root",
        )
        if mapping["schema_version"] != _PLANNER_FEEDBACK_SCHEMA_VERSION:
            raise ValueError("planner feedback schema_version is unsupported")
        if mapping["kind"] != _PLANNER_FEEDBACK_KIND:
            raise ValueError("planner feedback kind is unsupported")
        model = _feedback_mapping(
            mapping["model_identity"],
            frozenset({"filename", "model_id", "repo_id", "revision"}),
            label="model_identity",
        )
        task = _feedback_mapping(
            mapping["task"],
            frozenset({"objective", "task_id"}),
            label="task",
        )
        source = _feedback_mapping(
            mapping["source"],
            frozenset({"artifact_digest", "kind"}),
            label="source",
        )
        if source["kind"] != _PLANNER_FEEDBACK_SOURCE_KIND:
            raise ValueError("planner feedback source kind is unsupported")
        if not all(isinstance(model[field], str) for field in model):
            raise ValueError("planner feedback model identity fields must be strings")
        if not isinstance(task["task_id"], str) or not isinstance(
            task["objective"], str
        ):
            raise ValueError("planner feedback task fields must be strings")
        return cls(
            plan_identity_digest=_feedback_digest(
                mapping["plan_identity_digest"], "plan identity digest"
            ),
            attempt_identity_digest=_feedback_digest(
                mapping["attempt_identity_digest"], "attempt identity digest"
            ),
            attempt_number=cast(int, mapping["attempt_number"]),
            model_identity=ModelArtifactIdentity(
                model_id=cast(str, model["model_id"]),
                repo_id=cast(str, model["repo_id"]),
                filename=cast(str, model["filename"]),
                revision=cast(str, model["revision"]),
            ),
            task_id=task["task_id"],
            task_objective=task["objective"],
            outcome=_feedback_outcome(mapping["outcome"]),
            source_artifact_digest=_feedback_digest(
                source["artifact_digest"], "source artifact digest"
            ),
        )

    def _outcome_value(self) -> dict[str, object]:
        return {
            "accepted": self.outcome.accepted,
            "blockers": list(self.outcome.blockers),
            "changed_file_precision": self.outcome.changed_file_precision,
            "changed_file_recall": self.outcome.changed_file_recall,
            "score": self.outcome.score,
        }

    def _json_value(self) -> dict[str, object]:
        return {
            "attempt_identity_digest": self.attempt_identity_digest,
            "attempt_number": self.attempt_number,
            "kind": _PLANNER_FEEDBACK_KIND,
            "model_identity": {
                "filename": self.model_identity.filename,
                "model_id": self.model_identity.model_id,
                "repo_id": self.model_identity.repo_id,
                "revision": self.model_identity.revision,
            },
            "outcome": self._outcome_value(),
            "plan_identity_digest": self.plan_identity_digest,
            "schema_version": _PLANNER_FEEDBACK_SCHEMA_VERSION,
            "source": {
                "artifact_digest": self.source_artifact_digest,
                "kind": _PLANNER_FEEDBACK_SOURCE_KIND,
            },
            "task": {"objective": self.task_objective, "task_id": self.task_id},
        }


def compare_with_codex(
    proposal: ProposalManifest,
    evidence: CandidateEvidence,
    reference: CodexReference,
) -> ComparisonResult:
    """Compare all proposed changes and gate evidence to the Codex reference."""
    blockers: list[str] = []
    if proposal.baseline_sha != reference.baseline_sha:
        blockers.append("baseline identity")
    if not evidence.tests_passed:
        blockers.append("tests")
    if evidence.warnings:
        blockers.append("warnings")
    if evidence.coverage_aggregate < 85.0:
        blockers.append("aggregate coverage")
    if evidence.coverage_min_file < 75.0:
        blockers.append("per-file coverage")
    if not evidence.ruff_passed:
        blockers.append("ruff")
    if not evidence.mypy_passed:
        blockers.append("mypy")
    if not evidence.docstrings_passed:
        blockers.append("docstrings")
    if not evidence.markdown_passed:
        blockers.append("markdown")
    if not evidence.cleanup_passed:
        blockers.append("resource cleanup")
    if evidence.commit_count != 1:
        blockers.append("atomic commit")
    if not evidence.worktree_clean:
        blockers.append("clean worktree")

    reference_files = reference.changed_files
    candidate_files = evidence.changed_files
    intersection = reference_files & candidate_files
    precision = len(intersection) / len(candidate_files) if candidate_files else 0.0
    recall = len(intersection) / len(reference_files) if reference_files else 1.0
    if precision < 1.0:
        blockers.append("changed-file precision")
    if recall < 1.0:
        blockers.append("changed-file recall")

    proposed_tests = frozenset(proposal.tests)
    if not reference.test_files <= proposed_tests:
        blockers.append("reference test coverage")

    score = 100.0
    score -= (1.0 - precision) * 20.0
    score -= (1.0 - recall) * 25.0
    score -= (
        max(0, len(blockers) - int(precision < 1.0) - int(recall < 1.0)) * 5.0
    )
    if (
        evidence.changed_lines > 0
        and reference.changed_lines > 0
        and evidence.changed_lines > reference.changed_lines * 1.5
    ):
        blockers.append("diff size")
        score -= min(
            10.0,
            10.0 * evidence.changed_lines / reference.changed_lines / 4.0,
        )
    if (
        reference.elapsed_seconds > 0
        and evidence.elapsed_seconds > reference.elapsed_seconds * 2.0
    ):
        blockers.append("tool efficiency")
        score -= min(10.0, evidence.elapsed_seconds / reference.elapsed_seconds)

    ordered_blockers = tuple(dict.fromkeys(blockers))
    score = round(max(0.0, score), 2)
    return ComparisonResult(
        accepted=not ordered_blockers and score == 100.0,
        score=score,
        blockers=ordered_blockers,
        changed_file_precision=precision,
        changed_file_recall=recall,
    )


_EVALUATION_DIAGNOSIS_FIELDS = frozenset(
    {
        "category",
        "column",
        "command_kind",
        "command_sha256",
        "duration_ms",
        "exit_code",
        "failure_class",
        "finish_reason",
        "finished",
        "hypothesis",
        "line",
        "path_sha256",
        "phase",
        "protocol",
        "schema_version",
    }
)


def _unavailable_evaluation_diagnosis() -> str:
    """Return fixed fail-closed retry evidence without copying rejected input."""
    protocol = EVALUATION_DIAGNOSIS_PROTOCOL
    return json.dumps(
        {
            "category": "none",
            "column": 0,
            "command_kind": "unknown",
            "command_sha256": hashlib.sha256(
                protocol.version.encode("ascii")
            ).hexdigest(),
            "duration_ms": 0,
            "exit_code": 1,
            "failure_class": "diagnosis_unavailable",
            "finish_reason": "unknown",
            "finished": True,
            "hypothesis": protocol.unavailable_hypothesis,
            "line": 0,
            "path_sha256": "",
            "phase": "evaluation",
            "protocol": protocol.version,
            "schema_version": protocol.schema_version,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def safe_evaluation_retry_diagnosis(diagnostics: object) -> str:
    """Return canonical typed evaluation evidence or one fixed redacted fallback."""
    protocol = EVALUATION_DIAGNOSIS_PROTOCOL
    fallback = _unavailable_evaluation_diagnosis()
    if not isinstance(diagnostics, str):
        return fallback
    try:
        encoded = diagnostics.encode("ascii")
    except UnicodeEncodeError:
        return fallback
    if not encoded or len(encoded) > protocol.max_diagnosis_bytes:
        return fallback
    try:
        value = json.loads(diagnostics)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return fallback
    if not isinstance(value, dict) or set(value) != _EVALUATION_DIAGNOSIS_FIELDS:
        return fallback
    phase = value.get("phase")
    command_kind = value.get("command_kind")
    command_digest = value.get("command_sha256")
    duration_ms = value.get("duration_ms")
    exit_code = value.get("exit_code")
    failure_class = value.get("failure_class")
    category = value.get("category")
    path_digest = value.get("path_sha256")
    line = value.get("line")
    column = value.get("column")
    syntax_categories = frozenset(protocol.syntax_categories)
    commit_categories = frozenset(protocol.commit_categories)
    no_syntax_context = (
        category == "none"
        and path_digest == ""
        and line == 0
        and column == 0
        and isinstance(failure_class, str)
        and failure_class not in syntax_categories
    )
    syntax_context = (
        isinstance(category, str)
        and category in syntax_categories
        and category == failure_class
        and isinstance(path_digest, str)
        and _PROTOCOL_DIGEST_RE.fullmatch(path_digest) is not None
        and not isinstance(line, bool)
        and isinstance(line, int)
        and 0 <= line <= protocol.max_coordinate
        and not isinstance(column, bool)
        and isinstance(column, int)
        and 0 <= column <= protocol.max_coordinate
    )
    commit_context = (
        isinstance(category, str)
        and category in commit_categories
        and failure_class == "commit_failed"
        and phase == "commit"
        and command_kind == "repository_commit"
        and path_digest == ""
        and line == 0
        and column == 0
    )
    expected_hypothesis = (
        protocol.unavailable_hypothesis
        if failure_class == "diagnosis_unavailable"
        else protocol.failure_hypothesis
    )
    if (
        (phase, command_kind) not in protocol.phase_kinds
        or not isinstance(command_digest, str)
        or _PROTOCOL_DIGEST_RE.fullmatch(command_digest) is None
        or isinstance(duration_ms, bool)
        or not isinstance(duration_ms, int)
        or not 0 <= duration_ms <= protocol.max_duration_ms
        or isinstance(exit_code, bool)
        or not isinstance(exit_code, int)
        or exit_code == 0
        or not -255 <= exit_code <= 255
        or failure_class not in protocol.diagnosis_failure_classes
        or not (no_syntax_context or syntax_context or commit_context)
        or value.get("finish_reason") != "unknown"
        or value.get("finished") is not True
        or value.get("hypothesis") != expected_hypothesis
        or value.get("protocol") != protocol.version
        or value.get("schema_version") != protocol.schema_version
    ):
        return fallback
    canonical = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return canonical if canonical == diagnostics else fallback


def build_retry_prompt(
    task: str,
    comparison: ComparisonResult,
    *,
    diagnostics: str = "",
    max_diagnostic_bytes: int = 4096,
    independent_candidate: bool = False,
) -> str:
    """Build bounded, secret-redacted evidence for a subsequent local attempt."""
    if (
        isinstance(max_diagnostic_bytes, bool)
        or not isinstance(max_diagnostic_bytes, int)
        or not 1 <= max_diagnostic_bytes <= 4096
    ):
        raise ValueError("max_diagnostic_bytes must be an integer from 1 through 4096")
    if not isinstance(independent_candidate, bool):
        raise ValueError("independent_candidate must be a boolean")
    gaps = ", ".join(comparison.blockers) if comparison.blockers else "none"
    redacted = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        diagnostics.replace("\x00", ""),
    )
    raw_tail = redacted.encode("utf-8")[-max_diagnostic_bytes:]
    diagnostic_tail = raw_tail.decode("utf-8", errors="replace")
    failure_evidence = (
        f"\nExact bounded failure evidence:\n{diagnostic_tail}\n"
        if diagnostic_tail
        else ""
    )
    retry_direction = (
        "The prior candidate failed. Solve the approved task independently from the "
        "trusted baseline; do not repair or infer unseen candidate output. Preserve the "
        "smallest correct diff.\n"
        if independent_candidate
        else ""
    )
    return (
        f"{task}\n\n"
        f"Previous proposal score: {comparison.score:.2f}/100.\n"
        f"Required corrections: {gaps}.\n"
        f"{failure_evidence}"
        f"{retry_direction}"
        "Do not broaden the changed-file set beyond the Codex reference. "
        "Return only the strict proposal JSON object."
    )


__all__ = [
    "CandidateEvidence",
    "CodexReference",
    "ComparisonResult",
    "PlannerFeedbackExchange",
    "build_retry_prompt",
    "compare_with_codex",
    "merge_proposal_manifests",
    "safe_evaluation_retry_diagnosis",
]
