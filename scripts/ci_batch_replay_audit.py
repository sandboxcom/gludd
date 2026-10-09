#!/usr/bin/env python3
"""Audit exact prior batch receipts without authorizing replay or skips."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from scripts.ci_batch_receipts import (
        MAX_FAILURE_RECEIPTS_PER_GENERATION,
        MAX_RECEIPT_GENERATIONS,
        RECEIPT_SCHEMA_VERSION,
        action_identity_error,
        canonical_json_sha256,
        coverage_semantic_sha256,
        file_sha256,
        outcome_manifest_error,
        private_path_error,
        read_strict_json,
        validate_batch_receipt,
        validate_failure_receipt,
    )
    from scripts.ci_gate_progress import receipt_duration_seconds
    from scripts.ci_receipt_auth import (
        ReceiptAuthStatus,
        ReceiptTrustPolicy,
        authenticate_receipt,
    )
else:
    from ci_batch_receipts import (
        MAX_FAILURE_RECEIPTS_PER_GENERATION,
        MAX_RECEIPT_GENERATIONS,
        RECEIPT_SCHEMA_VERSION,
        action_identity_error,
        canonical_json_sha256,
        coverage_semantic_sha256,
        file_sha256,
        outcome_manifest_error,
        private_path_error,
        read_strict_json,
        validate_batch_receipt,
        validate_failure_receipt,
    )
    from ci_gate_progress import receipt_duration_seconds
    from ci_receipt_auth import (
        ReceiptAuthStatus,
        ReceiptTrustPolicy,
        authenticate_receipt,
    )

MAX_AUDIT_CANDIDATES = 512
MAX_AUDIT_INDEX_BYTES = 16 * 1024**2
MAX_AUDIT_MANIFEST_BYTES = 256 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ReplayAuditRequest:
    """Current executed evidence compared with prior pass receipts."""

    action_identity: Mapping[str, object]
    observed_action_identity: Mapping[str, object]
    coverage_path: Path
    outcome_manifest: Mapping[str, object] | None
    returncode: int
    cleanup_returncode: int


@dataclass(frozen=True)
class ReplayAuditResult:
    """Bounded report-only result; ``skip_authorized`` is always false."""

    eligible: bool
    reason: str
    action_digest: str | None = None
    receipt_path: Path | None = None
    skip_authorized: bool = False
    authentication: ReceiptAuthStatus | Literal["not-observed"] = "not-observed"


@dataclass(frozen=True)
class ReplayAdmissionRequest:
    """Exact current action identity and owned coverage destination."""

    action_identity: Mapping[str, object]
    observed_action_identity: Mapping[str, object]
    coverage_destination: Path


@dataclass(frozen=True)
class ReplayAdmissionResult:
    """Fail-closed decision for one authenticated exact-SHA pass receipt."""

    admitted: bool
    reason: str
    action_digest: str | None = None
    receipt_path: Path | None = None
    authentication: ReceiptAuthStatus | Literal["not-observed"] = "not-observed"
    duration_seconds: float | None = None


@dataclass(frozen=True)
class _IndexedReceipt:
    action_digest: str
    path: Path
    identity: dict[str, object]
    coordinate: tuple[object, ...]
    authentication: ReceiptAuthStatus


@dataclass(frozen=True)
class _IndexedFailure:
    action_digest: str
    path: Path
    coordinate: tuple[object, ...]
    authentication: ReceiptAuthStatus


def _failure_authentication(
    failures: list[_IndexedFailure],
) -> ReceiptAuthStatus:
    statuses = {failure.authentication for failure in failures}
    if len(statuses) != 1:
        return "malformed"
    return next(iter(statuses))


def _bounded_entries(path: Path, limit: int) -> tuple[list[Path], bool]:
    entries: list[Path] = []
    for entry in path.iterdir():
        if len(entries) == limit:
            return entries, True
        entries.append(entry)
    return entries, False


def _batch_coordinate(identity: Mapping[str, object]) -> tuple[object, ...]:
    source = identity.get("source")
    plan = identity.get("plan")
    if not isinstance(source, Mapping) or not isinstance(plan, Mapping):
        return ()
    return (
        source.get("candidate_sha"),
        source.get("test_files_sha256"),
        plan.get("shard"),
        plan.get("batch_index"),
        plan.get("collection_manifest_sha256"),
    )


def _failure_coordinate(identity: Mapping[str, object]) -> tuple[object, ...]:
    return (
        identity.get("candidate_sha"),
        identity.get("test_files_sha256"),
        identity.get("shard"),
        identity.get("batch_index"),
        identity.get("collection_manifest_sha256"),
    )


def _drift_reason(
    prior: Mapping[str, object], current: Mapping[str, object]
) -> str | None:
    changed = {
        family
        for family in (
            "source",
            "plan",
            "runner",
            "toolchain",
            "plugins",
            "coverage",
            "platform",
            "environment",
            "external_inputs",
        )
        if prior.get(family) != current.get(family)
    }
    if prior.get("schema_version") != current.get("schema_version"):
        changed.add("schema_version")
    if not changed:
        return None
    reason_groups = {
        "candidate-drift": {"source"},
        "command-drift": {"plan", "runner"},
        "dependency-drift": {"toolchain", "plugins"},
        "coverage-config-drift": {"coverage"},
        "platform-drift": {"platform"},
        "environment-drift": {"environment"},
        "input-drift": {"external_inputs"},
        "schema-drift": {"schema_version"},
    }
    matched = [
        reason for reason, families in reason_groups.items() if changed.intersection(families)
    ]
    return matched[0] if len(matched) == 1 else "identity-drift-ambiguous"


def _dirty_source(identity: Mapping[str, object]) -> bool:
    source = identity.get("source")
    return not isinstance(source, Mapping) or any(
        source.get(name) is not True for name in ("clean", "exact_sha", "queries_ok")
    )


def _restore_coverage_fragment(source: Path, destination: Path) -> str | None:
    """Atomically restore one validated receipt fragment into an owned directory."""
    parent = destination.parent
    if (
        parent.is_symlink()
        or not parent.is_dir()
        or destination.exists()
        or destination.is_symlink()
    ):
        return "destination-unsafe"
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=parent,
        )
        temporary = Path(temporary_name)
        os.close(descriptor)
        descriptor = -1
        source_digest_before = file_sha256(source)
        shutil.copyfile(source, temporary)
        os.chmod(temporary, 0o600)
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        restored_digest = file_sha256(temporary)
        source_digest_after = file_sha256(source)
        semantic_digest, semantic_error = coverage_semantic_sha256(temporary)
        if (
            source_digest_before != restored_digest
            or source_digest_after != restored_digest
            or semantic_error is not None
            or semantic_digest is None
        ):
            return "coverage-drift"
        os.replace(temporary, destination)
        directory_descriptor = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return None
    except (OSError, ValueError):
        return "coverage-restore-failed"
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ShadowReplayAuditor:
    """Index a bounded prior generation and report exact replay eligibility."""

    def __init__(
        self,
        cache_root: Path,
        *,
        candidate_sha: str,
        max_candidates: int = MAX_AUDIT_CANDIDATES,
        max_index_bytes: int = MAX_AUDIT_INDEX_BYTES,
        trust_policy: ReceiptTrustPolicy | None = None,
    ) -> None:
        if not _GIT_SHA.fullmatch(candidate_sha):
            raise ValueError("candidate_sha must be one exact lowercase Git SHA")
        if max_candidates < 1 or max_candidates > MAX_AUDIT_CANDIDATES:
            raise ValueError("max_candidates is outside the shadow-audit bound")
        if max_index_bytes < 1 or max_index_bytes > MAX_AUDIT_INDEX_BYTES:
            raise ValueError("max_index_bytes is outside the shadow-audit bound")
        self._cache_root = cache_root
        self._candidate_sha = candidate_sha
        self._max_candidates = max_candidates
        self._max_index_bytes = max_index_bytes
        self._trust_policy = trust_policy
        self._receipts: dict[str, _IndexedReceipt] = {}
        self._failures: dict[str, list[_IndexedFailure]] = {}
        self._index_error: str | None = None
        self._index_prior_generation()

    def _refuse(self, reason: str) -> None:
        self._receipts.clear()
        self._failures.clear()
        self._index_error = reason

    def _index_prior_generation(self) -> None:
        if not self._cache_root.exists() and not self._cache_root.is_symlink():
            return
        try:
            root_error = private_path_error(self._cache_root, directory=True)
            if root_error is not None:
                self._refuse(f"cache-tree-{root_error}")
                return
            version_root = self._cache_root / f"v{RECEIPT_SCHEMA_VERSION}"
            if not version_root.exists() and not version_root.is_symlink():
                return
            version_error = private_path_error(version_root, directory=True)
            if version_error is not None:
                self._refuse(f"cache-tree-{version_error}")
                return
            generations, too_many_generations = _bounded_entries(
                version_root, MAX_RECEIPT_GENERATIONS
            )
            if too_many_generations:
                self._refuse("cache-tree-generation-limit")
                return
            for generation in generations:
                generation_error = private_path_error(generation, directory=True)
                if generation_error is not None:
                    self._refuse(f"cache-tree-{generation_error}")
                    return
                if not _GIT_SHA.fullmatch(generation.name):
                    self._refuse("cache-tree-invalid-generation")
                    return

            generation = version_root / self._candidate_sha
            if not generation.exists():
                return
            generation_entries, too_many_generation_entries = _bounded_entries(
                generation, self._max_candidates + 1
            )
            if too_many_generation_entries:
                self._refuse("candidate-set-too-large")
                return
            failures_root: Path | None = None
            candidates: list[Path] = []
            for entry in generation_entries:
                if entry.name == "failures":
                    failures_root = entry
                else:
                    candidates.append(entry)
            remaining = self._max_candidates - len(candidates)
            if remaining < 0:
                self._refuse("candidate-set-too-large")
                return
            failures: list[Path] = []
            if failures_root is not None:
                failure_root_error = private_path_error(
                    failures_root,
                    directory=True,
                )
                if failure_root_error is not None:
                    self._refuse("cache-corrupt")
                    return
                failure_limit = min(
                    remaining,
                    MAX_FAILURE_RECEIPTS_PER_GENERATION,
                )
                failures, too_many_failures = _bounded_entries(
                    failures_root,
                    failure_limit,
                )
                if too_many_failures:
                    self._refuse("candidate-set-too-large")
                    return
            index_bytes = 0
            for candidate in sorted(candidates, key=lambda item: item.name):
                candidate_error = private_path_error(candidate, directory=True)
                if candidate_error is not None or not _SHA256.fullmatch(candidate.name):
                    self._refuse("cache-corrupt")
                    return
                manifest_path = candidate / "manifest.json"
                manifest_error = private_path_error(manifest_path, directory=False)
                if manifest_error is not None:
                    self._refuse("cache-corrupt")
                    return
                manifest_size = manifest_path.stat().st_size
                if manifest_size < 1 or manifest_size > MAX_AUDIT_MANIFEST_BYTES:
                    self._refuse("candidate-index-too-large")
                    return
                index_bytes += manifest_size
                if index_bytes > self._max_index_bytes:
                    self._refuse("candidate-index-too-large")
                    return
                manifest = read_strict_json(manifest_path)
                if not isinstance(manifest, dict):
                    self._refuse("cache-corrupt")
                    return
                identity = manifest.get("action_identity")
                if not isinstance(identity, dict):
                    self._refuse("cache-corrupt")
                    return
                if action_identity_error(identity) is not None:
                    self._refuse("cache-corrupt")
                    return
                action_digest = canonical_json_sha256(identity)
                if action_digest != candidate.name:
                    self._refuse("cache-corrupt")
                    return
                validation = validate_batch_receipt(
                    candidate, expected_action_identity=identity
                )
                if not validation.valid:
                    self._refuse("cache-corrupt")
                    return
                authentication = authenticate_receipt(
                    candidate,
                    expected_kind="pass",
                    expected_action_digest=action_digest,
                    expected_content_sha256=file_sha256(manifest_path),
                    trust_policy=self._trust_policy,
                )
                self._receipts[action_digest] = _IndexedReceipt(
                    action_digest=action_digest,
                    path=candidate,
                    identity=identity,
                    coordinate=_batch_coordinate(identity),
                    authentication=authentication.status,
                )
            for failure in sorted(failures, key=lambda item: item.name):
                failure_error = private_path_error(failure, directory=True)
                if failure_error is not None or not _SHA256.fullmatch(failure.name):
                    self._refuse("cache-corrupt")
                    return
                manifest_path = failure / "manifest.json"
                manifest_error = private_path_error(manifest_path, directory=False)
                if manifest_error is not None:
                    self._refuse("cache-corrupt")
                    return
                manifest_size = manifest_path.stat().st_size
                if manifest_size < 1 or manifest_size > MAX_AUDIT_MANIFEST_BYTES:
                    self._refuse("candidate-index-too-large")
                    return
                index_bytes += manifest_size
                if index_bytes > self._max_index_bytes:
                    self._refuse("candidate-index-too-large")
                    return
                validation = validate_failure_receipt(failure)
                if not validation.valid:
                    self._refuse("cache-corrupt")
                    return
                manifest = read_strict_json(manifest_path)
                if not isinstance(manifest, dict):
                    self._refuse("cache-corrupt")
                    return
                batch_identity = manifest.get("batch_identity")
                if not isinstance(batch_identity, dict):
                    self._refuse("cache-corrupt")
                    return
                failure_action_digest = batch_identity.get("action_digest")
                if not isinstance(
                    failure_action_digest,
                    str,
                ) or not _SHA256.fullmatch(
                    failure_action_digest
                ):
                    self._refuse("cache-corrupt")
                    return
                authentication = authenticate_receipt(
                    failure,
                    expected_kind="failure",
                    expected_action_digest=failure_action_digest,
                    expected_content_sha256=file_sha256(manifest_path),
                    trust_policy=self._trust_policy,
                )
                self._failures.setdefault(failure_action_digest, []).append(
                    _IndexedFailure(
                        action_digest=failure_action_digest,
                        path=failure,
                        coordinate=_failure_coordinate(batch_identity),
                        authentication=authentication.status,
                    )
                )
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            self._refuse("cache-corrupt")

    def _lookup_exact_receipt(
        self,
        action_identity: Mapping[str, object],
        observed_action_identity: Mapping[str, object],
    ) -> ReplayAuditResult:
        """Return one revalidated authenticated receipt for an exact action."""
        if self._index_error is not None:
            return ReplayAuditResult(False, self._index_error)
        if _dirty_source(observed_action_identity):
            return ReplayAuditResult(False, "dirty-inputs")

        expected_error = action_identity_error(action_identity)
        observed_error = action_identity_error(observed_action_identity)
        if expected_error is not None or observed_error is not None:
            if "restricted-environment" in {expected_error, observed_error}:
                return ReplayAuditResult(False, "dirty-inputs")
            return ReplayAuditResult(False, "current-identity-invalid")
        source = action_identity.get("source")
        assert isinstance(source, Mapping)
        if source.get("candidate_sha") != self._candidate_sha:
            return ReplayAuditResult(False, "candidate-drift")

        observation_drift = _drift_reason(action_identity, observed_action_identity)
        if observation_drift is not None:
            return ReplayAuditResult(False, observation_drift)

        action_digest = canonical_json_sha256(action_identity)
        exact_failures = self._failures.get(action_digest)
        if exact_failures:
            return ReplayAuditResult(
                False,
                "prior-failure-non-reusable",
                action_digest=action_digest,
                authentication=_failure_authentication(exact_failures),
            )
        receipt = self._receipts.get(action_digest)
        if receipt is None:
            coordinate = _batch_coordinate(action_identity)
            related_failures = [
                failure
                for failures in self._failures.values()
                for failure in failures
                if failure.coordinate == coordinate
            ]
            if related_failures:
                return ReplayAuditResult(
                    False,
                    "prior-failure-non-reusable",
                    action_digest=action_digest,
                    authentication=_failure_authentication(related_failures),
                )
            related = [
                indexed
                for indexed in self._receipts.values()
                if indexed.coordinate == coordinate
            ]
            if not related:
                return ReplayAuditResult(
                    False, "no-prior-candidate", action_digest=action_digest
                )
            if len(related) != 1:
                return ReplayAuditResult(
                    False, "candidate-ambiguous", action_digest=action_digest
                )
            return ReplayAuditResult(
                False,
                _drift_reason(related[0].identity, action_identity)
                or "identity-drift-ambiguous",
                action_digest=action_digest,
                receipt_path=related[0].path,
            )

        validation = validate_batch_receipt(
            receipt.path,
            expected_action_identity=action_identity,
        )
        if not validation.valid:
            return ReplayAuditResult(
                False,
                "candidate-corrupt",
                action_digest=action_digest,
                receipt_path=receipt.path,
            )
        try:
            authentication = authenticate_receipt(
                receipt.path,
                expected_kind="pass",
                expected_action_digest=action_digest,
                expected_content_sha256=file_sha256(receipt.path / "manifest.json"),
                trust_policy=self._trust_policy,
            )
        except OSError:
            return ReplayAuditResult(
                False,
                "receipt-auth-malformed",
                action_digest=action_digest,
                receipt_path=receipt.path,
                authentication="malformed",
            )
        if not authentication.verified:
            return ReplayAuditResult(
                False,
                f"receipt-auth-{authentication.status}",
                action_digest=action_digest,
                receipt_path=receipt.path,
                authentication=authentication.status,
            )
        return ReplayAuditResult(
            True,
            "exact-safe-candidate",
            action_digest=action_digest,
            receipt_path=receipt.path,
            authentication=authentication.status,
        )

    def audit(self, request: ReplayAuditRequest) -> ReplayAuditResult:
        """Compare current executed evidence; never return skip authorization."""
        if self._index_error is not None:
            return ReplayAuditResult(False, self._index_error)
        if request.returncode != 0:
            return ReplayAuditResult(False, "current-not-passing")
        if request.cleanup_returncode != 0:
            return ReplayAuditResult(False, "cleanup-incomplete")
        if outcome_manifest_error(request.outcome_manifest) is not None:
            return ReplayAuditResult(False, "outcomes-invalid")
        current_coverage_digest, coverage_error = coverage_semantic_sha256(
            request.coverage_path
        )
        if coverage_error is not None or current_coverage_digest is None:
            return ReplayAuditResult(False, "coverage-invalid")
        lookup = self._lookup_exact_receipt(
            request.action_identity,
            request.observed_action_identity,
        )
        if not lookup.eligible:
            return lookup
        action_digest = lookup.action_digest
        receipt_path = lookup.receipt_path
        assert action_digest is not None
        assert receipt_path is not None
        try:
            prior_outcomes = read_strict_json(receipt_path / "outcomes.json")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return ReplayAuditResult(
                False,
                "candidate-corrupt",
                action_digest=action_digest,
                receipt_path=receipt_path,
            )
        if not isinstance(prior_outcomes, Mapping):
            return ReplayAuditResult(
                False,
                "candidate-corrupt",
                action_digest=action_digest,
                receipt_path=receipt_path,
            )
        assert request.outcome_manifest is not None
        if prior_outcomes.get("node_id_sha256") != request.outcome_manifest.get(
            "node_id_sha256"
        ):
            return ReplayAuditResult(
                False,
                "node-id-drift",
                action_digest=action_digest,
                receipt_path=receipt_path,
            )
        if (
            prior_outcomes.get("terminal_outcome_sha256")
            != request.outcome_manifest.get("terminal_outcome_sha256")
            or prior_outcomes.get("counts") != request.outcome_manifest.get("counts")
        ):
            return ReplayAuditResult(
                False,
                "outcome-drift",
                action_digest=action_digest,
                receipt_path=receipt_path,
            )
        prior_coverage_digest, prior_coverage_error = coverage_semantic_sha256(
            receipt_path / "coverage.data"
        )
        if prior_coverage_error is not None or prior_coverage_digest is None:
            return ReplayAuditResult(
                False,
                "candidate-corrupt",
                action_digest=action_digest,
                receipt_path=receipt_path,
            )
        if prior_coverage_digest != current_coverage_digest:
            return ReplayAuditResult(
                False,
                "coverage-drift",
                action_digest=action_digest,
                receipt_path=receipt_path,
            )
        return ReplayAuditResult(
            True,
            "exact-safe-candidate",
            action_digest=action_digest,
            receipt_path=receipt_path,
            authentication=lookup.authentication,
        )

    def admit(self, request: ReplayAdmissionRequest) -> ReplayAdmissionResult:
        """Restore one authenticated exact-action pass receipt, otherwise miss."""
        lookup = self._lookup_exact_receipt(
            request.action_identity,
            request.observed_action_identity,
        )
        if not lookup.eligible:
            return ReplayAdmissionResult(
                False,
                lookup.reason,
                action_digest=lookup.action_digest,
                receipt_path=lookup.receipt_path,
                authentication=lookup.authentication,
            )
        action_digest = lookup.action_digest
        receipt_path = lookup.receipt_path
        assert action_digest is not None
        assert receipt_path is not None
        try:
            outcomes = read_strict_json(receipt_path / "outcomes.json")
            manifest = read_strict_json(receipt_path / "manifest.json")
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return ReplayAdmissionResult(
                False,
                "candidate-corrupt",
                action_digest=action_digest,
                receipt_path=receipt_path,
                authentication=lookup.authentication,
            )
        if (
            not isinstance(outcomes, Mapping)
            or outcome_manifest_error(outcomes) is not None
            or not isinstance(manifest, Mapping)
        ):
            return ReplayAdmissionResult(
                False,
                "candidate-corrupt",
                action_digest=action_digest,
                receipt_path=receipt_path,
                authentication=lookup.authentication,
            )
        duration = receipt_duration_seconds(
            manifest.get("started_at"),
            manifest.get("completed_at"),
        )
        if duration is None:
            return ReplayAdmissionResult(
                False,
                "receipt-duration-invalid",
                action_digest=action_digest,
                receipt_path=receipt_path,
                authentication=lookup.authentication,
            )
        restore_error = _restore_coverage_fragment(
            receipt_path / "coverage.data",
            request.coverage_destination,
        )
        if restore_error is not None:
            request.coverage_destination.unlink(missing_ok=True)
            return ReplayAdmissionResult(
                False,
                restore_error,
                action_digest=action_digest,
                receipt_path=receipt_path,
                authentication=lookup.authentication,
            )
        return ReplayAdmissionResult(
            True,
            "exact-safe-receipt",
            action_digest=action_digest,
            receipt_path=receipt_path,
            authentication=lookup.authentication,
            duration_seconds=duration,
        )

    def snapshot_receipts(self) -> tuple[Path, ...]:
        """Return the already-validated bounded snapshot, never live cache state."""
        if self._index_error is not None:
            return ()
        return tuple(
            receipt.path
            for receipt in sorted(
                self._receipts.values(),
                key=lambda item: item.action_digest,
            )
        )


__all__ = [
    "MAX_AUDIT_CANDIDATES",
    "MAX_AUDIT_INDEX_BYTES",
    "ReplayAdmissionRequest",
    "ReplayAdmissionResult",
    "ReplayAuditRequest",
    "ReplayAuditResult",
    "ShadowReplayAuditor",
]
