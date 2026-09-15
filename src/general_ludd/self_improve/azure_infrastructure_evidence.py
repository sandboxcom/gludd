"""Durable operational evidence for Azure accelerator placement failures.

This channel is deliberately separate from model-quality calibration.  It
contains only validated infrastructure coordinates and fixed failure classes;
provider messages, model identifiers, prompts, and image repository names are
never persisted.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any

from general_ludd.self_improve.model_candidates import BackendFailure
from general_ludd.small_models.evidence_store import CapabilityEvidenceStore

_COLLECTION = "self_improve.azure_infrastructure"
_SCHEMA_VERSION = 1
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_IMAGE_RE = re.compile(r"[^@\s]+@(sha256:[0-9a-f]{64})")
_LOCATION_RE = re.compile(r"[a-z][a-z0-9]{1,31}")
_PROFILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9./_-]{0,199}")
_MAX_EVIDENCE_AGE_SECONDS = 86_400
_MAX_FAILURE_THRESHOLD = 100
_OPERATIONAL_FAILURES = frozenset(
    {
        BackendFailure.RATE_LIMITED,
        BackendFailure.TIMEOUT,
        BackendFailure.UNAVAILABLE,
    }
)
_RECORD_KEYS = frozenset(
    {
        "collection",
        "container_image_digest",
        "deployment_identity_digest",
        "evidence_digest",
        "failure",
        "location",
        "phase",
        "registered_at",
        "schema_version",
        "workload_profile_type",
    }
)


class AzureInfrastructurePhase(StrEnum):
    """Fixed effect phases eligible for infrastructure availability learning."""

    PREFLIGHT = "preflight"
    CANDIDATE_STARTUP = "candidate_startup"
    REQUEST = "request"


def _image_digest(container_image: object) -> str:
    if not isinstance(container_image, str):
        raise ValueError("container_image must use one immutable SHA-256 digest")
    matched = _IMAGE_RE.fullmatch(container_image)
    if matched is None:
        raise ValueError("container_image must use one immutable SHA-256 digest")
    return matched.group(1)


def _coordinate(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} is invalid")
    return value


def _timestamp(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"{label} must return one finite non-negative timestamp")
    return float(value)


def _canonical_digest(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def record_azure_infrastructure_failure(
    store: CapabilityEvidenceStore,
    *,
    location: str,
    workload_profile_type: str,
    container_image: str,
    deployment_identity_digest: str,
    phase: AzureInfrastructurePhase,
    failure: BackendFailure,
    clock: Callable[[], float] = time.time,
    trace_sink: Callable[[dict[str, object]], None] | None = None,
) -> int:
    """Persist one censored operational failure and emit its safe coordinates."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    checked_location = _coordinate(location, _LOCATION_RE, "location")
    checked_profile = _coordinate(
        workload_profile_type,
        _PROFILE_RE,
        "workload_profile_type",
    )
    checked_image_digest = _image_digest(container_image)
    checked_deployment = _coordinate(
        deployment_identity_digest,
        _DIGEST_RE,
        "deployment_identity_digest",
    )
    if not isinstance(phase, AzureInfrastructurePhase):
        raise ValueError("phase must be an AzureInfrastructurePhase")
    if not isinstance(failure, BackendFailure):
        raise ValueError("failure must be a BackendFailure")
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
        "container_image_digest": checked_image_digest,
        "deployment_identity_digest": checked_deployment,
        "failure": failure.value,
        "location": checked_location,
        "phase": phase.value,
        "schema_version": _SCHEMA_VERSION,
        "workload_profile_type": checked_profile,
    }
    record = {
        **payload,
        "evidence_digest": _canonical_digest(payload),
        "registered_at": registered_at,
    }
    count = store.register_evidence(record)
    if trace_sink is not None:
        try:
            trace_sink(
                {
                    "deployment_identity_digest": checked_deployment,
                    "event": "SELF_IMPROVE_AZURE_INFRASTRUCTURE_RECORDED",
                    "failure": failure.value,
                    "location": checked_location,
                    "phase": phase.value,
                    "schema_version": _SCHEMA_VERSION,
                    "workload_profile_type": checked_profile,
                }
            )
        except Exception:
            raise RuntimeError(
                "Azure infrastructure evidence trace publication failed"
            ) from None
    return count


def _validated_record(record: Mapping[str, Any]) -> tuple[str, BackendFailure, float] | None:
    if set(record) != _RECORD_KEYS:
        return None
    registered_at = record.get("registered_at")
    payload = {
        key: value
        for key, value in record.items()
        if key not in {"evidence_digest", "registered_at"}
    }
    if (
        record.get("collection") != _COLLECTION
        or record.get("schema_version") != _SCHEMA_VERSION
        or record.get("evidence_digest") != _canonical_digest(payload)
    ):
        return None
    try:
        location = _coordinate(record.get("location"), _LOCATION_RE, "location")
        profile = _coordinate(
            record.get("workload_profile_type"),
            _PROFILE_RE,
            "workload_profile_type",
        )
        _coordinate(
            record.get("deployment_identity_digest"),
            _DIGEST_RE,
            "deployment_identity_digest",
        )
        image_digest = _coordinate(
            record.get("container_image_digest"),
            re.compile(r"sha256:[0-9a-f]{64}"),
            "container_image_digest",
        )
        phase_value = record.get("phase")
        failure_value = record.get("failure")
        if not isinstance(phase_value, str) or not isinstance(failure_value, str):
            return None
        phase = AzureInfrastructurePhase(phase_value)
        failure = BackendFailure(failure_value)
        observed_at = _timestamp(registered_at, "registered_at")
    except (TypeError, ValueError):
        return None
    if phase not in {
        AzureInfrastructurePhase.PREFLIGHT,
        AzureInfrastructurePhase.CANDIDATE_STARTUP,
    }:
        return None
    return f"{location}\0{image_digest}\0{profile}", failure, observed_at


def load_recent_unavailable_profiles(
    store: CapabilityEvidenceStore,
    *,
    location: str,
    container_image: str,
    max_age_seconds: int,
    minimum_failures: int,
    now_epoch: float | None = None,
) -> frozenset[str]:
    """Return exact profiles exceeding a recent operational-failure threshold."""
    if not isinstance(store, CapabilityEvidenceStore):
        raise ValueError("store must be a CapabilityEvidenceStore")
    checked_location = _coordinate(location, _LOCATION_RE, "location")
    checked_image = _image_digest(container_image)
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
    prefix = f"{checked_location}\0{checked_image}\0"
    counts: Counter[str] = Counter()
    for raw in store.list_all():
        validated = _validated_record(raw)
        if validated is None:
            continue
        scope, failure, observed_at = validated
        age = current - observed_at
        if (
            scope.startswith(prefix)
            and failure in _OPERATIONAL_FAILURES
            and 0 <= age <= max_age_seconds
        ):
            counts[scope.removeprefix(prefix)] += 1
    return frozenset(
        profile for profile, count in counts.items() if count >= minimum_failures
    )


__all__ = (
    "AzureInfrastructurePhase",
    "load_recent_unavailable_profiles",
    "record_azure_infrastructure_failure",
)
