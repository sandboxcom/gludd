"""Versioned, content-free Azure operational-availability evidence.

This infrastructure-owned module keeps provider placement outcomes separate
from model-quality calibration. Its records bind a region, provider inventory
SKU, immutable runtime, and generic serving topology while excluding model
names, prompts, endpoints, credentials, and provider-authored error text.

The legacy self-improvement module is only a compatibility facade; universal
workloads can use this implementation without importing that subsystem.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from typing import Any

from general_ludd.infra.azure_operational_availability_contracts import (
    _DIGEST_RE,
    _SCOPE_VERSION,
    AzureAvailabilityAssessment,
    AzureAvailabilityIndex,
    AzureAvailabilityScope,
    AzureAvailabilityTerminal,
    AzureInfrastructurePhase,
    _canonical_digest,
    _coordinate,
    _timestamp,
    build_azure_availability_scope,
)
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_SCHEMA_VERSION = 1
_COLLECTION = "infra.azure_operational_availability"
_LEGACY_COLLECTIONS = frozenset({"self_improve.azure_operational_availability"})
_OWNED_COLLECTIONS = frozenset({_COLLECTION, *_LEGACY_COLLECTIONS})
_MAX_EVIDENCE_AGE_SECONDS = 86_400
_MAX_FAILURE_THRESHOLD = 100
_RECORD_KEYS = frozenset(
    {
        "collection",
        "deployment_identity_digest",
        "evidence_digest",
        "location",
        "outcome",
        "phase",
        "registered_at",
        "resource_sku",
        "runtime_version_digest",
        "schema_version",
        "scope_digest",
        "scope_version",
        "topology_digest",
    }
)


class AzureOperationalEvidenceError(ValueError):
    """Refuse malformed or drifted records from the owned collection."""


def record_azure_availability_terminal(
    store: CapabilityEvidenceStore,
    *,
    scope: AzureAvailabilityScope,
    deployment_identity_digest: str,
    phase: AzureInfrastructurePhase,
    outcome: AzureAvailabilityTerminal,
    clock: Callable[[], float] = time.time,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Persist one validated terminal placement outcome and a censored trace."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    if not isinstance(scope, AzureAvailabilityScope):
        raise ValueError("scope must be an AzureAvailabilityScope")
    checked_deployment = _coordinate(
        deployment_identity_digest,
        _DIGEST_RE,
        "deployment_identity_digest",
    )
    if phase not in {
        AzureInfrastructurePhase.PREFLIGHT,
        AzureInfrastructurePhase.CANDIDATE_STARTUP,
    }:
        raise ValueError("phase is not eligible for placement learning")
    if not isinstance(outcome, AzureAvailabilityTerminal):
        raise ValueError("outcome must be an AzureAvailabilityTerminal")
    if not callable(clock):
        raise ValueError("clock must be callable")
    try:
        registered_at = _timestamp(clock(), "clock")
    except ValueError:
        raise
    except Exception:
        raise ValueError("clock must return one finite non-negative timestamp") from None
    if trace_sink is not None and not callable(trace_sink):
        raise ValueError("trace_sink must be callable")
    payload: dict[str, object] = {
        "collection": _COLLECTION,
        "deployment_identity_digest": checked_deployment,
        "location": scope.location,
        "outcome": outcome.value,
        "phase": phase.value,
        "registered_at": registered_at,
        "resource_sku": scope.resource_sku,
        "runtime_version_digest": scope.runtime_version_digest,
        "schema_version": _SCHEMA_VERSION,
        "scope_digest": scope.scope_digest,
        "scope_version": scope.scope_version,
        "topology_digest": scope.topology_digest,
    }
    record = {**payload, "evidence_digest": _canonical_digest(payload)}
    count = store.register_evidence(record)
    if trace_sink is not None:
        try:
            trace_sink(
                {
                    "deployment_identity_digest": checked_deployment,
                    "event": "GLUDD_AZURE_AVAILABILITY_RECORDED",
                    "outcome": outcome.value,
                    "phase": phase.value,
                    "resource_sku": scope.resource_sku,
                    "schema_version": _SCHEMA_VERSION,
                    "scope_digest": scope.scope_digest,
                    "scope_version": scope.scope_version,
                }
            )
        except Exception:
            raise RuntimeError(
                "Azure availability trace publication failed"
            ) from None
    return count


def _owned_record(raw: Mapping[str, Any]) -> bool:
    return raw.get("collection") in _OWNED_COLLECTIONS


def _validated_record(
    record: Mapping[str, Any],
    *,
    now_epoch: float,
) -> tuple[AzureAvailabilityScope, AzureAvailabilityTerminal, float]:
    if set(record) != _RECORD_KEYS:
        raise AzureOperationalEvidenceError("availability record schema is malformed")
    if record.get("collection") not in _OWNED_COLLECTIONS:
        raise AzureOperationalEvidenceError("availability collection is invalid")
    if record.get("schema_version") != _SCHEMA_VERSION:
        raise AzureOperationalEvidenceError("availability schema version drifted")
    if record.get("scope_version") != _SCOPE_VERSION:
        raise AzureOperationalEvidenceError("availability scope version drifted")
    try:
        observed_at = _timestamp(record.get("registered_at"), "registered_at")
    except ValueError as exc:
        raise AzureOperationalEvidenceError(str(exc)) from None
    if observed_at > now_epoch:
        raise AzureOperationalEvidenceError("availability record is from the future")
    try:
        scope = AzureAvailabilityScope(
            location=record["location"],
            resource_sku=record["resource_sku"],
            runtime_version_digest=record["runtime_version_digest"],
            topology_digest=record["topology_digest"],
            scope_version=record["scope_version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        label = str(exc) or "scope"
        raise AzureOperationalEvidenceError(label) from None
    if record.get("scope_digest") != scope.scope_digest:
        raise AzureOperationalEvidenceError("availability scope digest drifted")
    try:
        _coordinate(
            record.get("deployment_identity_digest"),
            _DIGEST_RE,
            "deployment_identity_digest",
        )
        phase_value = record.get("phase")
        outcome_value = record.get("outcome")
        if not isinstance(phase_value, str) or not isinstance(outcome_value, str):
            raise ValueError("phase or outcome is invalid")
        phase = AzureInfrastructurePhase(phase_value)
        outcome = AzureAvailabilityTerminal(outcome_value)
    except (TypeError, ValueError) as exc:
        raise AzureOperationalEvidenceError(str(exc)) from None
    if phase not in {
        AzureInfrastructurePhase.PREFLIGHT,
        AzureInfrastructurePhase.CANDIDATE_STARTUP,
    }:
        raise AzureOperationalEvidenceError("availability phase drifted")
    payload = {
        key: value for key, value in record.items() if key != "evidence_digest"
    }
    if record.get("evidence_digest") != _canonical_digest(payload):
        raise AzureOperationalEvidenceError("availability evidence digest drifted")
    return scope, outcome, observed_at


def load_azure_availability_index(
    store: CapabilityEvidenceStore,
    *,
    max_age_seconds: int,
    minimum_failures: int,
    now_epoch: float | None = None,
) -> AzureAvailabilityIndex:
    """Load strict recent placement evidence without mutating durable history."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    if (
        isinstance(max_age_seconds, bool)
        or not isinstance(max_age_seconds, int)
        or not 1 <= max_age_seconds <= _MAX_EVIDENCE_AGE_SECONDS
    ):
        raise ValueError("max_age_seconds is outside its hard bound")
    if (
        isinstance(minimum_failures, bool)
        or not isinstance(minimum_failures, int)
        or not 1 <= minimum_failures <= _MAX_FAILURE_THRESHOLD
    ):
        raise ValueError("minimum_failures is outside its hard bound")
    current = _timestamp(time.time() if now_epoch is None else now_epoch, "now_epoch")
    grouped: dict[
        str,
        list[tuple[AzureAvailabilityScope, AzureAvailabilityTerminal, float]],
    ] = defaultdict(list)
    for raw in store.list_all():
        if not _owned_record(raw):
            continue
        scope, outcome, observed_at = _validated_record(raw, now_epoch=current)
        if current - observed_at <= max_age_seconds:
            grouped[scope.scope_digest].append((scope, outcome, observed_at))
    assessments: list[AzureAvailabilityAssessment] = []
    for scope_digest, records in grouped.items():
        ordered = sorted(records, key=lambda item: item[2])
        successful = sum(
            outcome is AzureAvailabilityTerminal.AVAILABLE
            for _scope, outcome, _observed_at in ordered
        )
        failed = len(ordered) - successful
        consecutive_failures = 0
        for _scope, outcome, _observed_at in ordered:
            if outcome is AzureAvailabilityTerminal.AVAILABLE:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
        assessments.append(
            AzureAvailabilityAssessment(
                scope_digest=scope_digest,
                observed_outcomes=len(ordered),
                successful_outcomes=successful,
                failed_outcomes=failed,
                consecutive_failures=consecutive_failures,
                availability_score=(1.0 + successful) / (2.0 + len(ordered)),
                feasible=consecutive_failures < minimum_failures,
                last_observed_at=ordered[-1][2],
            )
        )
    return AzureAvailabilityIndex(
        assessments=tuple(sorted(assessments, key=lambda item: item.scope_digest))
    )


__all__ = (
    "AzureAvailabilityAssessment",
    "AzureAvailabilityIndex",
    "AzureAvailabilityScope",
    "AzureAvailabilityTerminal",
    "AzureInfrastructurePhase",
    "AzureOperationalEvidenceError",
    "build_azure_availability_scope",
    "load_azure_availability_index",
    "record_azure_availability_terminal",
)
