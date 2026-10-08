#!/usr/bin/env python3
"""Render bounded shadow-only gate progress from plans and pass receipts."""

from __future__ import annotations

import json
import math
import re
import statistics
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from scripts.ci_batch_receipts import (
        action_identity_error,
        canonical_json_bytes,
        canonical_json_sha256,
        file_sha256,
        private_path_error,
        read_strict_json,
        validate_batch_receipt,
    )
    from scripts.ci_receipt_auth import (
        ReceiptAuthStatus,
        ReceiptTrustPolicy,
        authenticate_receipt,
    )
else:
    from ci_batch_receipts import (
        action_identity_error,
        canonical_json_bytes,
        canonical_json_sha256,
        file_sha256,
        private_path_error,
        read_strict_json,
        validate_batch_receipt,
    )
    from ci_receipt_auth import (
        ReceiptAuthStatus,
        ReceiptTrustPolicy,
        authenticate_receipt,
    )

MAX_PROGRESS_BATCHES = 512
MAX_TIMING_SAMPLES = 64
MAX_PROGRESS_OUTPUT_BYTES = 4096
MAX_RECEIPT_DURATION_SECONDS = 24 * 60 * 60
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHARD = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"
)
ReceiptStatus = Literal["eligible", "missing", "ineligible", "failure"]


class ProgressCounts(TypedDict):
    """Fixed aggregate execution and receipt classifications."""

    executed: int
    failed: int
    failure_receipts: int
    ineligible: int
    missing: int
    passed: int
    receipt_candidates: int
    remaining: int
    total: int


class TimingSummary(TypedDict):
    """Fixed estimate-only timing evidence."""

    basis: str
    authentication_status: str
    eta_kind: str
    eta_seconds_estimate: float | None
    evidence_status: str
    samples_retained: int
    samples_truncated: bool


class GateProgressSummary(TypedDict):
    """Machine-readable shadow progress schema."""

    schema_version: int
    kind: str
    candidate_sha: str
    progress: ProgressCounts
    timing: TimingSummary
    gate_result: str
    overall_green: None
    terminal_phases_complete: bool
    skips: int


@dataclass(frozen=True)
class ProgressBatch:
    """Content-free identity of one batch in the canonical known plan."""

    shard: str
    batch_index: int
    test_files_sha256: str
    collection_manifest_sha256: str

    def __post_init__(self) -> None:
        if not _SHARD.fullmatch(self.shard):
            raise ValueError("shard is invalid")
        if type(self.batch_index) is not int or self.batch_index < 1:
            raise ValueError("batch_index must be a positive integer")
        if not _SHA256.fullmatch(self.test_files_sha256):
            raise ValueError("test_files_sha256 is invalid")
        if not _SHA256.fullmatch(self.collection_manifest_sha256):
            raise ValueError("collection_manifest_sha256 is invalid")

    @property
    def coordinate(self) -> tuple[str, int, str, str]:
        """Return the stable content-free coordinate used by receipts."""
        return (
            self.shard,
            self.batch_index,
            self.test_files_sha256,
            self.collection_manifest_sha256,
        )


@dataclass(frozen=True)
class ProgressExecution:
    """One completed current-run batch classification."""

    batch: ProgressBatch
    passed: bool
    receipt_status: ReceiptStatus

    def __post_init__(self) -> None:
        if type(self.passed) is not bool:
            raise ValueError("passed must be a boolean")
        if self.receipt_status not in {
            "eligible",
            "failure",
            "ineligible",
            "missing",
        }:
            raise ValueError("receipt_status is invalid")


@dataclass(frozen=True)
class _ReceiptTiming:
    coordinate: tuple[str, int, str, str]
    action_digest: str
    duration_seconds: float


def _receipt_coordinate(identity: Mapping[str, object]) -> tuple[str, int, str, str] | None:
    source = identity.get("source")
    plan = identity.get("plan")
    if not isinstance(source, Mapping) or not isinstance(plan, Mapping):
        return None
    shard = plan.get("shard")
    batch_index = plan.get("batch_index")
    test_files_sha256 = source.get("test_files_sha256")
    collection_manifest_sha256 = plan.get("collection_manifest_sha256")
    if (
        not isinstance(shard, str)
        or type(batch_index) is not int
        or not isinstance(test_files_sha256, str)
        or not isinstance(collection_manifest_sha256, str)
    ):
        return None
    try:
        batch = ProgressBatch(
            shard=shard,
            batch_index=batch_index,
            test_files_sha256=test_files_sha256,
            collection_manifest_sha256=collection_manifest_sha256,
        )
    except ValueError:
        return None
    return batch.coordinate


def _parse_duration(started_at: object, completed_at: object) -> float | None:
    if (
        not isinstance(started_at, str)
        or not isinstance(completed_at, str)
        or not _UTC_TIMESTAMP.fullmatch(started_at)
        or not _UTC_TIMESTAMP.fullmatch(completed_at)
    ):
        return None
    try:
        started = datetime.fromisoformat(started_at.removesuffix("Z") + "+00:00")
        completed = datetime.fromisoformat(completed_at.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    duration = (completed - started).total_seconds()
    if (
        not math.isfinite(duration)
        or duration < 0
        or duration > MAX_RECEIPT_DURATION_SECONDS
    ):
        return None
    return duration


class ShadowGateProgress:
    """Track bounded progress while keeping terminal gate status unknown."""

    def __init__(
        self,
        *,
        candidate_sha: str,
        batches: Iterable[ProgressBatch],
        completed_receipts: Iterable[Path] = (),
        max_timing_samples: int = MAX_TIMING_SAMPLES,
        trust_policy: ReceiptTrustPolicy | None = None,
    ) -> None:
        if not _GIT_SHA.fullmatch(candidate_sha):
            raise ValueError("candidate_sha must be one exact lowercase Git SHA")
        planned = list(batches)
        if not planned or len(planned) > MAX_PROGRESS_BATCHES:
            raise ValueError("batch plan is empty or exceeds the progress bound")
        if max_timing_samples < 1 or max_timing_samples > MAX_TIMING_SAMPLES:
            raise ValueError("max_timing_samples exceeds the progress bound")
        coordinates = [batch.coordinate for batch in planned]
        if len(set(coordinates)) != len(coordinates):
            raise ValueError("batch plan contains duplicate coordinates")

        self._candidate_sha = candidate_sha
        self._planned = {batch.coordinate: batch for batch in planned}
        self._order = {batch.coordinate: index for index, batch in enumerate(planned)}
        self._max_timing_samples = max_timing_samples
        self._trust_policy = trust_policy
        self._executions: dict[tuple[str, int, str, str], ProgressExecution] = {}
        self._durations: dict[tuple[str, int, str, str], float] = {}
        self._actions: dict[tuple[str, int, str, str], str] = {}
        self._timing_evidence_status = "none"
        self._authentication_status = "not-observed"
        self._samples_truncated = False
        self._load_completed_receipts(completed_receipts)

    def _reject_timing(
        self,
        reason: str,
        *,
        authentication_status: str | None = None,
    ) -> None:
        self._durations.clear()
        self._actions.clear()
        self._timing_evidence_status = reason
        if authentication_status is not None:
            self._authentication_status = authentication_status
        self._samples_truncated = False

    def _read_receipt_timing(
        self,
        receipt: Path,
    ) -> tuple[_ReceiptTiming | None, str | None, ReceiptAuthStatus | None]:
        if private_path_error(receipt, directory=True) is not None:
            return None, "rejected-malformed-receipt", "malformed"
        manifest_path = receipt / "manifest.json"
        if private_path_error(manifest_path, directory=False) is not None:
            return None, "rejected-malformed-receipt", "malformed"
        try:
            manifest = read_strict_json(manifest_path)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
            return None, "rejected-malformed-receipt", "malformed"
        if not isinstance(manifest, dict):
            return None, "rejected-malformed-receipt", "malformed"
        identity = manifest.get("action_identity")
        if not isinstance(identity, dict) or action_identity_error(identity) is not None:
            return None, "rejected-malformed-receipt", "malformed"
        source = identity.get("source")
        assert isinstance(source, Mapping)
        if source.get("candidate_sha") != self._candidate_sha:
            return None, "rejected-mixed-candidate", None
        coordinate = _receipt_coordinate(identity)
        if coordinate is None:
            return None, "rejected-malformed-receipt", "malformed"
        if coordinate not in self._planned:
            return None, "rejected-foreign-plan", None
        action_digest = canonical_json_sha256(identity)
        validation = validate_batch_receipt(
            receipt,
            expected_action_identity=identity,
        )
        if not validation.valid or validation.action_digest != action_digest:
            return None, "rejected-malformed-receipt", "malformed"
        try:
            authentication = authenticate_receipt(
                receipt,
                expected_kind="pass",
                expected_action_digest=action_digest,
                expected_content_sha256=file_sha256(manifest_path),
                trust_policy=self._trust_policy,
            )
        except OSError:
            return None, "rejected-auth-malformed", "malformed"
        if not authentication.verified:
            return (
                None,
                f"rejected-auth-{authentication.status}",
                authentication.status,
            )
        duration = _parse_duration(
            manifest.get("started_at"),
            manifest.get("completed_at"),
        )
        if duration is None:
            return None, "rejected-malformed-receipt", "malformed"
        return _ReceiptTiming(coordinate, action_digest, duration), None, "verified"

    def _load_completed_receipts(self, receipts: Iterable[Path]) -> None:
        observed: dict[tuple[str, int, str, str], _ReceiptTiming] = {}
        for receipt_count, receipt in enumerate(receipts, start=1):
            if receipt_count > MAX_PROGRESS_BATCHES:
                self._reject_timing("rejected-unbounded-evidence")
                return
            timing, error, authentication = self._read_receipt_timing(receipt)
            if error is not None or timing is None:
                self._reject_timing(
                    error or "rejected-malformed-receipt",
                    authentication_status=authentication,
                )
                return
            prior = observed.get(timing.coordinate)
            if prior is not None and prior.action_digest != timing.action_digest:
                self._reject_timing("rejected-ambiguous-receipts")
                return
            observed[timing.coordinate] = timing
        if not observed:
            return
        self._authentication_status = "verified"
        for coordinate, timing in sorted(
            observed.items(), key=lambda item: self._order[item[0]]
        ):
            self._actions[coordinate] = timing.action_digest
            if len(self._durations) < self._max_timing_samples:
                self._durations[coordinate] = timing.duration_seconds
            else:
                self._samples_truncated = True
        self._timing_evidence_status = "valid"

    def _observe_completed_receipt(
        self,
        receipt: Path,
        *,
        expected_coordinate: tuple[str, int, str, str],
    ) -> None:
        if self._timing_evidence_status.startswith("rejected-"):
            return
        timing, error, authentication = self._read_receipt_timing(receipt)
        if error is not None or timing is None:
            self._reject_timing(
                error or "rejected-malformed-receipt",
                authentication_status=authentication,
            )
            return
        if timing.coordinate != expected_coordinate:
            self._reject_timing("rejected-execution-receipt-mismatch")
            return
        prior_action = self._actions.get(timing.coordinate)
        if prior_action is not None and prior_action != timing.action_digest:
            self._reject_timing("rejected-ambiguous-receipts")
            return
        if prior_action == timing.action_digest:
            return
        self._actions[timing.coordinate] = timing.action_digest
        if len(self._durations) < self._max_timing_samples:
            self._durations[timing.coordinate] = timing.duration_seconds
        else:
            self._samples_truncated = True
        self._timing_evidence_status = "valid"
        self._authentication_status = "verified"

    def record(
        self,
        execution: ProgressExecution,
        *,
        completed_receipt: Path | None = None,
    ) -> GateProgressSummary:
        """Record one executed batch and return its fixed-schema snapshot."""
        coordinate = execution.batch.coordinate
        if coordinate not in self._planned:
            raise ValueError("executed batch is not in the canonical plan")
        if coordinate in self._executions:
            raise ValueError("executed batch was already recorded")
        self._executions[coordinate] = execution
        if completed_receipt is not None:
            self._observe_completed_receipt(
                completed_receipt,
                expected_coordinate=coordinate,
            )
        return self.summary()

    def _eta_seconds(self) -> float | None:
        if not self._durations:
            return None
        remaining = [
            coordinate
            for coordinate in self._planned
            if coordinate not in self._executions
        ]
        fallback = float(statistics.median(self._durations.values()))
        estimate = sum(self._durations.get(coordinate, fallback) for coordinate in remaining)
        return round(estimate, 3)

    def summary(self) -> GateProgressSummary:
        """Return content-free progress without inferring terminal success."""
        executions = tuple(self._executions.values())
        passed = sum(execution.passed for execution in executions)
        failed = len(executions) - passed
        eligible = sum(
            execution.receipt_status == "eligible" for execution in executions
        )
        missing = sum(
            execution.receipt_status == "missing" for execution in executions
        )
        failure_receipts = sum(
            execution.receipt_status == "failure" for execution in executions
        )
        ineligible = len(executions) - eligible - missing - failure_receipts
        return {
            "schema_version": 1,
            "kind": "shadow-gate-progress",
            "candidate_sha": self._candidate_sha,
            "progress": {
                "executed": len(executions),
                "failed": failed,
                "failure_receipts": failure_receipts,
                "ineligible": ineligible,
                "missing": missing,
                "passed": passed,
                "receipt_candidates": eligible,
                "remaining": len(self._planned) - len(executions),
                "total": len(self._planned),
            },
            "timing": {
                "basis": "completed-shadow-receipts",
                "authentication_status": self._authentication_status,
                "eta_kind": "estimate",
                "eta_seconds_estimate": self._eta_seconds(),
                "evidence_status": self._timing_evidence_status,
                "samples_retained": len(self._durations),
                "samples_truncated": self._samples_truncated,
            },
            "gate_result": "unknown",
            "overall_green": None,
            "terminal_phases_complete": False,
            "skips": 0,
        }

    def render(self, summary: Mapping[str, object] | None = None) -> str:
        """Render one canonical JSON snapshot within the hard output bound."""
        payload = self.summary() if summary is None else dict(summary)
        encoded = canonical_json_bytes(payload)
        if len(encoded) > MAX_PROGRESS_OUTPUT_BYTES:
            raise ValueError("progress output exceeds the hard byte bound")
        return encoded.decode("ascii")


__all__ = [
    "MAX_PROGRESS_BATCHES",
    "MAX_PROGRESS_OUTPUT_BYTES",
    "MAX_TIMING_SAMPLES",
    "ProgressBatch",
    "ProgressExecution",
    "ReceiptStatus",
    "ShadowGateProgress",
]
