#!/usr/bin/env python3
"""Validate pinned typed-decision fixtures and fail-closed profile updates.

This checker is intentionally offline.  It validates the documentation contract,
normalization examples, and immutable profile transitions without contacting an
endpoint, loading a model, or enabling runtime admission.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, SchemaError

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = Path(
    "docs/releases/typed-decision-adapter-contract-v1.schema.json",
)
DEFAULT_EXAMPLES = Path(
    "docs/releases/typed-decision-adapter-contract-v1.examples.json",
)
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_PREPARED_UPDATE_BYTES = 256 * 1024
MAX_ACTOR_ID_CHARS = 256

PREPARED_UPDATE_VERSION = "typed-decision-adapter-prepared-update/v1"
UPDATE_PLAN_VERSION = "typed-decision-adapter-update-plan/v1"
UPDATE_RECEIPT_VERSION = "typed-decision-adapter-update-receipt/v1"
COMPATIBILITY_EVIDENCE_VERSION = "typed-decision-adapter-compatibility-evidence/v1"
ROLLBACK_METADATA_VERSION = "typed-decision-adapter-rollback/v1"

PROTECTED_UPDATE_SECTIONS = (
    "batching",
    "capabilities",
    "compatibility",
    "limits",
    "redaction",
    "residency",
    "rollout",
    "transport",
    "update_policy",
)
UPDATE_PLAN_ACTIONS = (
    "validate_schema_and_immutable_pins",
    "verify_compatibility_evidence",
    "persist_disabled_candidate",
    "verify_fail_closed_rollback_metadata",
    "emit_redacted_receipt",
)
RECEIPT_REDACTED_FIELDS = (
    "actor_id",
    "credentials",
    "request.state",
    "response.answers",
    "transport.endpoint",
)

_EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_UTC_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_SHA256_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PIN_PATHS = (
    ("identity", "backend_protocol_version"),
    ("identity", "calibration_digest"),
    ("identity", "fingerprint"),
    ("identity", "model_digest"),
    ("identity", "model_revision"),
    ("identity", "renderer_digest"),
    ("identity", "runtime_digest"),
    ("identity", "server_digest"),
    ("identity", "tokenizer_digest"),
    ("source_pin", "commit"),
    ("source_pin", "release"),
    ("source_pin", "reviewed_at"),
    ("source_pin", "source_archive_digest"),
)

_DIGEST_FIELDS = (
    "fingerprint",
    "server_digest",
    "model_digest",
    "tokenizer_digest",
    "renderer_digest",
    "calibration_digest",
    "runtime_digest",
)
_IDENTITY_STABLE_FIELDS = (
    "contract_version",
    "backend_implementation",
    "endpoint_trust",
)
_IDENTITY_BOUND_FIELDS = (
    "backend_protocol_version",
    "server_digest",
    "model_revision",
    "model_digest",
    "tokenizer_digest",
    "renderer_digest",
    "calibration_digest",
    "runtime_digest",
)
_MOVING_ALIAS = re.compile(
    r"(?:^|[-_.@/])(latest|preview|main|master|example|unresolved)(?:$|[-_.@/])",
    re.IGNORECASE,
)


class ContractInputError(RuntimeError):
    """Raised when a bounded local contract input cannot be trusted."""


class UpdatePlanError(RuntimeError):
    """Raised when update evidence cannot be built, replayed, or persisted safely."""


class CheckStatus(StrEnum):
    """Stable checker outcomes for automation and review evidence."""

    VALID_EXAMPLES = "VALID_EXAMPLES"
    COMPATIBLE_NOOP = "COMPATIBLE_NOOP"
    COMPATIBLE_PINNED_UPDATE = "COMPATIBLE_PINNED_UPDATE"
    INCOMPATIBLE = "INCOMPATIBLE"
    INVALID = "INVALID"


class PersistenceStatus(StrEnum):
    """Stable outcomes for create-only prepared-update persistence."""

    CREATED = "CREATED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True, slots=True)
class CheckReport:
    """Deterministic compatibility result with no runtime side effects."""

    ok: bool
    status: CheckStatus
    findings: tuple[str, ...]
    profile_count: int = 0
    example_count: int = 0


@dataclass(frozen=True, slots=True)
class ProfilePinEvidence:
    """Immutable, non-secret identity for one exact adapter profile."""

    profile_id: str
    fingerprint: str
    profile_digest: str
    source_release: str
    source_commit: str
    source_archive_digest: str

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation."""

        return {
            "fingerprint": self.fingerprint,
            "profile_digest": self.profile_digest,
            "profile_id": self.profile_id,
            "source_archive_digest": self.source_archive_digest,
            "source_commit": self.source_commit,
            "source_release": self.source_release,
        }


@dataclass(frozen=True, slots=True)
class CompatibilityEvidence:
    """Immutable proof that only allowed pin-bound fields changed."""

    schema_digest: str
    baseline_profile_digest: str
    candidate_profile_digest: str
    checker_status: str
    changed_pin_paths: tuple[str, ...]
    protected_sections_verified: tuple[str, ...]
    findings: tuple[str, ...]
    evidence_digest: str

    def body_dict(self) -> dict[str, object]:
        """Return the digest-covered evidence body."""

        return {
            "baseline_profile_digest": self.baseline_profile_digest,
            "candidate_profile_digest": self.candidate_profile_digest,
            "changed_pin_paths": list(self.changed_pin_paths),
            "checker_status": self.checker_status,
            "findings": list(self.findings),
            "protected_sections_verified": list(self.protected_sections_verified),
            "schema_digest": self.schema_digest,
            "version": COMPATIBILITY_EVIDENCE_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation."""

        return {**self.body_dict(), "evidence_digest": self.evidence_digest}


@dataclass(frozen=True, slots=True)
class RollbackMetadata:
    """Exact disabled baseline needed to fail closed after a rejected update."""

    profile_id: str
    identity_fingerprint: str
    baseline_profile_digest: str
    source_commit: str
    source_archive_digest: str
    target_state: str
    required: bool
    rollback_digest: str

    def body_dict(self) -> dict[str, object]:
        """Return the digest-covered rollback body."""

        return {
            "baseline_profile_digest": self.baseline_profile_digest,
            "identity_fingerprint": self.identity_fingerprint,
            "profile_id": self.profile_id,
            "required": self.required,
            "source_archive_digest": self.source_archive_digest,
            "source_commit": self.source_commit,
            "target_state": self.target_state,
            "version": ROLLBACK_METADATA_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation."""

        return {**self.body_dict(), "rollback_digest": self.rollback_digest}


@dataclass(frozen=True, slots=True)
class UpdatePlan:
    """Deterministic evidence-only old-pin to new-pin plan."""

    event_id: str
    recorded_at: str
    actor_digest: str
    baseline: ProfilePinEvidence
    candidate: ProfilePinEvidence
    compatibility_evidence: CompatibilityEvidence
    rollback: RollbackMetadata
    actions: tuple[str, ...]
    admission_enabled: bool
    network_accessed: bool
    runtime_changed: bool
    plan_digest: str

    def body_dict(self) -> dict[str, object]:
        """Return the digest-covered plan body."""

        return {
            "actions": list(self.actions),
            "actor_digest": self.actor_digest,
            "admission_enabled": self.admission_enabled,
            "baseline": self.baseline.to_dict(),
            "candidate": self.candidate.to_dict(),
            "compatibility_evidence": self.compatibility_evidence.to_dict(),
            "event_id": self.event_id,
            "network_accessed": self.network_accessed,
            "recorded_at": self.recorded_at,
            "rollback": self.rollback.to_dict(),
            "runtime_changed": self.runtime_changed,
            "version": UPDATE_PLAN_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation."""

        return {**self.body_dict(), "plan_digest": self.plan_digest}


@dataclass(frozen=True, slots=True)
class UpdateReceipt:
    """Redacted immutable receipt for an evidence-only prepared update."""

    event_id: str
    recorded_at: str
    actor_digest: str
    status: str
    plan_digest: str
    evidence_digest: str
    rollback_digest: str
    redacted_fields: tuple[str, ...]
    admission_enabled: bool
    network_accessed: bool
    runtime_changed: bool
    receipt_digest: str

    def body_dict(self) -> dict[str, object]:
        """Return the digest-covered receipt body."""

        return {
            "actor_digest": self.actor_digest,
            "admission_enabled": self.admission_enabled,
            "event_id": self.event_id,
            "evidence_digest": self.evidence_digest,
            "network_accessed": self.network_accessed,
            "plan_digest": self.plan_digest,
            "recorded_at": self.recorded_at,
            "redacted_fields": list(self.redacted_fields),
            "rollback_digest": self.rollback_digest,
            "runtime_changed": self.runtime_changed,
            "status": self.status,
            "version": UPDATE_RECEIPT_VERSION,
        }

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation."""

        return {**self.body_dict(), "receipt_digest": self.receipt_digest}


@dataclass(frozen=True, slots=True)
class PreparedUpdate:
    """One immutable plan/receipt pair ready for offline operator review."""

    plan: UpdatePlan
    receipt: UpdateReceipt

    def to_dict(self) -> dict[str, object]:
        """Return the canonical JSON representation."""

        return {
            "plan": self.plan.to_dict(),
            "receipt": self.receipt.to_dict(),
            "version": PREPARED_UPDATE_VERSION,
        }


def _sorted_unique(findings: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(findings)))


def _load_json_object_bounded(path: Path, *, max_bytes: int) -> dict[str, object]:
    """Read one bounded, regular UTF-8 JSON object without following symlinks."""

    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ContractInputError(f"{path}: input is missing or unreadable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise ContractInputError(f"{path}: input must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise ContractInputError(f"{path}: input must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ContractInputError(f"{path}: input is missing or unreadable") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ContractInputError(f"{path}: input must be a regular file")
        data = _read_descriptor_bounded(descriptor, max_bytes=max_bytes)
    finally:
        os.close(descriptor)
    if len(data) > max_bytes:
        raise ContractInputError(
            f"{path}: input exceeds {max_bytes} byte limit",
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractInputError(f"{path}: input is not valid UTF-8") from exc
    try:
        loaded: object = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractInputError(
            f"{path}:{exc.lineno}: invalid JSON: {exc.msg}",
        ) from exc
    if not isinstance(loaded, dict):
        raise ContractInputError(f"{path}: input must contain a JSON object")
    return {str(key): value for key, value in loaded.items()}


def _read_descriptor_bounded(descriptor: int, *, max_bytes: int) -> bytes:
    """Read through short system reads while retaining a strict size ceiling."""

    chunks: list[bytes] = []
    total = 0
    while total <= max_bytes:
        chunk = os.read(descriptor, min(64 * 1024, max_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def load_json_object(path: Path) -> dict[str, object]:
    """Read one bounded, regular UTF-8 JSON object without following symlinks."""

    return _load_json_object_bounded(path, max_bytes=MAX_JSON_BYTES)


def _format_error_path(prefix: str, path: Sequence[object]) -> str:
    rendered = prefix
    for part in path:
        rendered += f"[{part}]" if isinstance(part, int) else f".{part}"
    return rendered


def _schema_findings(
    schema: Mapping[str, object],
    instance: object,
    *,
    profile_only: bool,
) -> tuple[str, ...]:
    validation_schema: Mapping[str, object]
    if profile_only:
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict) or "profile" not in definitions:
            return ("invalid JSON Schema: missing $defs.profile",)
        validation_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": definitions,
            "$ref": "#/$defs/profile",
        }
        prefix = "profile"
    else:
        validation_schema = schema
        prefix = "contract"

    try:
        Draft202012Validator.check_schema(validation_schema)
    except SchemaError as exc:
        return (f"invalid JSON Schema: {exc.message}",)

    validator = Draft202012Validator(
        validation_schema,
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (
            tuple(str(part) for part in error.absolute_path),
            error.message,
        ),
    )
    return tuple(
        f"{_format_error_path(prefix, list(error.absolute_path))}: {error.message}"
        for error in errors
    )


def validate_profile(
    schema: Mapping[str, object],
    profile: Mapping[str, object],
) -> tuple[str, ...]:
    """Validate one profile against the normative Draft 2020-12 definition."""

    return _schema_findings(schema, profile, profile_only=True)


def _mapping(parent: Mapping[str, object], key: str) -> Mapping[str, object] | None:
    value = parent.get(key)
    return value if isinstance(value, dict) else None


def _is_repeated_sentinel(value: str, *, prefix: str = "") -> bool:
    payload = value.removeprefix(prefix)
    if not payload:
        return True
    for width in (1, 2, 4, 5, 8, 10, 16, 20):
        if len(payload) % width == 0 and payload == payload[:width] * (
            len(payload) // width
        ):
            return True
    return False


def _has_moving_alias(value: object) -> bool:
    return isinstance(value, str) and _MOVING_ALIAS.search(value) is not None


def check_candidate_pins(profile: Mapping[str, object]) -> tuple[str, ...]:
    """Reject example, enabled, moving, sentinel, or otherwise unpinned profiles."""

    findings: list[str] = []
    if profile.get("example_only") is not False:
        findings.append("candidate profile is example-only")
    if profile.get("profile_state") != "disabled":
        findings.append("candidate profile must remain disabled")

    source = _mapping(profile, "source_pin")
    if source is None:
        findings.append("source_pin must be an object")
    else:
        repository = source.get("repository")
        if not isinstance(repository, str) or not repository.startswith("https://"):
            findings.append("source repository must use https")
        commit = source.get("commit")
        if not isinstance(commit, str) or _is_repeated_sentinel(commit):
            findings.append("source commit is a sentinel")
        archive = source.get("source_archive_digest")
        if not isinstance(archive, str) or _is_repeated_sentinel(
            archive,
            prefix="sha256:",
        ):
            findings.append("source_archive_digest is a sentinel")
        release = source.get("release")
        if _has_moving_alias(release):
            findings.append("source release contains a moving alias")

    identity = _mapping(profile, "identity")
    if identity is None:
        findings.append("identity must be an object")
    else:
        if _has_moving_alias(identity.get("model_revision")):
            findings.append("model_revision contains a moving alias")
        for field in _DIGEST_FIELDS:
            value = identity.get(field)
            if not isinstance(value, str) or _is_repeated_sentinel(
                value,
                prefix="sha256:",
            ):
                findings.append(f"identity.{field} is a sentinel digest")

    transport = _mapping(profile, "transport")
    if transport is None:
        findings.append("transport must be an object")
    elif _has_moving_alias(transport.get("model_id")):
        findings.append("transport model_id contains a moving alias")
    return _sorted_unique(findings)


def _normalization_kinds(bundle: Mapping[str, object]) -> list[str]:
    examples = bundle.get("normalization_examples")
    if not isinstance(examples, list):
        return []
    kinds: list[str] = []
    for example in examples:
        if not isinstance(example, dict):
            continue
        request = _mapping(example, "upstream_request")
        questions = _mapping(request, "questions") if request is not None else None
        if questions is None:
            continue
        for question in questions.values():
            if isinstance(question, dict) and isinstance(question.get("type"), str):
                kinds.append(question["type"])
    return kinds


def check_example_bundle(
    schema: Mapping[str, object],
    bundle: Mapping[str, object],
) -> tuple[str, ...]:
    """Validate canonical examples and prove that none is runtime-admissible."""

    findings = list(_schema_findings(schema, bundle, profile_only=False))
    profiles = bundle.get("profiles")
    examples = bundle.get("normalization_examples")
    known_profiles: set[str] = set()
    if isinstance(profiles, list):
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_id = profile.get("profile_id")
            if not isinstance(profile_id, str):
                continue
            known_profiles.add(profile_id)
            if profile.get("example_only") is not True:
                findings.append(f"example profile {profile_id} must be example-only")
            if profile.get("profile_state") != "disabled":
                findings.append(f"example profile {profile_id} must remain disabled")

    if sorted(_normalization_kinds(bundle)) != ["choice", "noul", "score"]:
        findings.append(
            "normalization examples must cover choice, noul, and score exactly",
        )

    if isinstance(examples, list):
        for example in examples:
            if not isinstance(example, dict):
                continue
            name = example.get("name", "<unknown>")
            profile_id = example.get("profile_id")
            if profile_id not in known_profiles:
                findings.append(f"example {name} references an unknown profile")
    return _sorted_unique(findings)


def check_canonical_contract(schema_path: Path, examples_path: Path) -> CheckReport:
    """Validate the tracked schema and non-runnable normalization examples."""

    try:
        schema = load_json_object(schema_path)
        examples = load_json_object(examples_path)
    except ContractInputError as exc:
        return CheckReport(False, CheckStatus.INVALID, (str(exc),))

    findings = check_example_bundle(schema, examples)
    profiles = examples.get("profiles")
    normalization = examples.get("normalization_examples")
    profile_count = len(profiles) if isinstance(profiles, list) else 0
    example_count = len(normalization) if isinstance(normalization, list) else 0
    return CheckReport(
        not findings,
        CheckStatus.VALID_EXAMPLES if not findings else CheckStatus.INVALID,
        findings,
        profile_count,
        example_count,
    )


def _value_changed(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    field: str,
) -> bool:
    return baseline.get(field) != candidate.get(field)


def _review_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def check_profile_update(
    schema: Mapping[str, object],
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> CheckReport:
    """Approve only a disabled, schema-equivalent, atomically re-pinned update."""

    findings: list[str] = []
    findings.extend(f"baseline {item}" for item in validate_profile(schema, baseline))
    findings.extend(f"candidate {item}" for item in validate_profile(schema, candidate))
    findings.extend(f"baseline {item}" for item in check_candidate_pins(baseline))
    findings.extend(check_candidate_pins(candidate))
    if findings:
        return CheckReport(False, CheckStatus.INCOMPATIBLE, _sorted_unique(findings))

    if baseline == candidate:
        return CheckReport(True, CheckStatus.COMPATIBLE_NOOP, ())

    if _value_changed(baseline, candidate, "profile_id"):
        findings.append("profile_id changed")
    if _value_changed(baseline, candidate, "example_only"):
        findings.append("example_only changed")

    baseline_source = _mapping(baseline, "source_pin")
    candidate_source = _mapping(candidate, "source_pin")
    baseline_identity = _mapping(baseline, "identity")
    candidate_identity = _mapping(candidate, "identity")
    if None in (
        baseline_source,
        candidate_source,
        baseline_identity,
        candidate_identity,
    ):
        findings.append("required update sections are missing")
        return CheckReport(False, CheckStatus.INCOMPATIBLE, _sorted_unique(findings))

    assert baseline_source is not None
    assert candidate_source is not None
    assert baseline_identity is not None
    assert candidate_identity is not None

    if baseline_source.get("repository") != candidate_source.get("repository"):
        findings.append("source repository changed")
    if baseline_source.get("license") != candidate_source.get("license"):
        findings.append("source license changed")

    commit_changed = baseline_source.get("commit") != candidate_source.get("commit")
    archive_changed = baseline_source.get(
        "source_archive_digest",
    ) != candidate_source.get("source_archive_digest")
    if commit_changed != archive_changed:
        findings.append("source commit and archive digest must change together")

    old_reviewed = _review_date(baseline_source.get("reviewed_at"))
    new_reviewed = _review_date(candidate_source.get("reviewed_at"))
    if old_reviewed is None or new_reviewed is None or new_reviewed < old_reviewed:
        findings.append("review date moved backward")

    if any(
        baseline_identity.get(field) != candidate_identity.get(field)
        for field in _IDENTITY_STABLE_FIELDS
    ):
        findings.append("identity contract changed")

    protected_sections = {
        "transport": "transport contract changed",
        "capabilities": "capability contract changed",
        "limits": "limit contract changed",
        "batching": "batching contract changed",
        "residency": "residency contract changed",
        "redaction": "redaction contract changed",
        "compatibility": "compatibility contract changed",
        "update_policy": "update policy changed",
        "rollout": "rollout contract changed",
    }
    for section, message in protected_sections.items():
        if _value_changed(baseline, candidate, section):
            findings.append(message)

    source_identity_changed = any(
        baseline_source.get(field) != candidate_source.get(field)
        for field in ("release", "commit", "source_archive_digest")
    ) or any(
        baseline_identity.get(field) != candidate_identity.get(field)
        for field in _IDENTITY_BOUND_FIELDS
    )
    fingerprint_changed = baseline_identity.get(
        "fingerprint",
    ) != candidate_identity.get("fingerprint")
    if source_identity_changed and not fingerprint_changed:
        findings.append("identity-bound inputs changed without a new fingerprint")
    if fingerprint_changed and not source_identity_changed:
        findings.append("fingerprint changed without identity-bound inputs")

    normalized = _sorted_unique(findings)
    if normalized:
        return CheckReport(False, CheckStatus.INCOMPATIBLE, normalized)
    return CheckReport(True, CheckStatus.COMPATIBLE_PINNED_UPDATE, ())


def canonical_json_bytes(value: object) -> bytes:
    """Serialize one value deterministically for hashing and immutable storage."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise UpdatePlanError("value is not canonical JSON") from exc
    return rendered.encode("ascii")


def canonical_digest(value: object) -> str:
    """Return the SHA-256 identity of one canonical JSON value."""

    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def _required_mapping(
    parent: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> Mapping[str, object]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise UpdatePlanError(f"{context}.{key} must be an object")
    return value


def _required_string(
    parent: Mapping[str, object],
    key: str,
    *,
    context: str,
) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value:
        raise UpdatePlanError(f"{context}.{key} must be a non-empty string")
    return value


def _validate_event_metadata(event_id: str, recorded_at: str) -> None:
    if _EVENT_ID.fullmatch(event_id) is None:
        raise UpdatePlanError("event_id must be 1-128 conservative identifier characters")
    if _UTC_TIMESTAMP.fullmatch(recorded_at) is None:
        raise UpdatePlanError("recorded_at must be an exact UTC second ending in Z")
    try:
        datetime.strptime(recorded_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise UpdatePlanError("recorded_at is not a valid UTC calendar instant") from exc


def _actor_digest(actor_id: str) -> str:
    if (
        not actor_id
        or actor_id.strip() != actor_id
        or len(actor_id) > MAX_ACTOR_ID_CHARS
    ):
        raise UpdatePlanError(
            f"actor_id must be 1-{MAX_ACTOR_ID_CHARS} trimmed characters",
        )
    return "sha256:" + hashlib.sha256(actor_id.encode("utf-8")).hexdigest()


def _profile_pin_evidence(profile: Mapping[str, object]) -> ProfilePinEvidence:
    source = _required_mapping(profile, "source_pin", context="profile")
    identity = _required_mapping(profile, "identity", context="profile")
    return ProfilePinEvidence(
        profile_id=_required_string(profile, "profile_id", context="profile"),
        fingerprint=_required_string(identity, "fingerprint", context="profile.identity"),
        profile_digest=canonical_digest(profile),
        source_release=_required_string(source, "release", context="profile.source_pin"),
        source_commit=_required_string(source, "commit", context="profile.source_pin"),
        source_archive_digest=_required_string(
            source,
            "source_archive_digest",
            context="profile.source_pin",
        ),
    )


def _changed_pin_paths(
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> tuple[str, ...]:
    changed: list[str] = []
    for section, field in _PIN_PATHS:
        baseline_section = _required_mapping(baseline, section, context="baseline")
        candidate_section = _required_mapping(candidate, section, context="candidate")
        if baseline_section.get(field) != candidate_section.get(field):
            changed.append(f"{section}.{field}")
    return tuple(sorted(changed))


def _build_prepared_update_with_actor_digest(
    schema: Mapping[str, object],
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    event_id: str,
    recorded_at: str,
    actor_digest: str,
) -> PreparedUpdate:
    _validate_event_metadata(event_id, recorded_at)
    if _SHA256_DIGEST.fullmatch(actor_digest) is None:
        raise UpdatePlanError("actor_digest must be one lowercase SHA-256 digest")

    report = check_profile_update(schema, baseline, candidate)
    if report.status != CheckStatus.COMPATIBLE_PINNED_UPDATE:
        detail = "; ".join(report.findings) or report.status.value
        raise UpdatePlanError(f"update requires a compatible pinned update: {detail}")

    baseline_pin = _profile_pin_evidence(baseline)
    candidate_pin = _profile_pin_evidence(candidate)
    evidence_without_digest = CompatibilityEvidence(
        schema_digest=canonical_digest(schema),
        baseline_profile_digest=baseline_pin.profile_digest,
        candidate_profile_digest=candidate_pin.profile_digest,
        checker_status=report.status.value,
        changed_pin_paths=_changed_pin_paths(baseline, candidate),
        protected_sections_verified=PROTECTED_UPDATE_SECTIONS,
        findings=report.findings,
        evidence_digest="",
    )
    evidence = CompatibilityEvidence(
        schema_digest=evidence_without_digest.schema_digest,
        baseline_profile_digest=evidence_without_digest.baseline_profile_digest,
        candidate_profile_digest=evidence_without_digest.candidate_profile_digest,
        checker_status=evidence_without_digest.checker_status,
        changed_pin_paths=evidence_without_digest.changed_pin_paths,
        protected_sections_verified=evidence_without_digest.protected_sections_verified,
        findings=evidence_without_digest.findings,
        evidence_digest=canonical_digest(evidence_without_digest.body_dict()),
    )

    rollback_without_digest = RollbackMetadata(
        profile_id=baseline_pin.profile_id,
        identity_fingerprint=baseline_pin.fingerprint,
        baseline_profile_digest=baseline_pin.profile_digest,
        source_commit=baseline_pin.source_commit,
        source_archive_digest=baseline_pin.source_archive_digest,
        target_state="disabled",
        required=True,
        rollback_digest="",
    )
    rollback = RollbackMetadata(
        profile_id=rollback_without_digest.profile_id,
        identity_fingerprint=rollback_without_digest.identity_fingerprint,
        baseline_profile_digest=rollback_without_digest.baseline_profile_digest,
        source_commit=rollback_without_digest.source_commit,
        source_archive_digest=rollback_without_digest.source_archive_digest,
        target_state=rollback_without_digest.target_state,
        required=rollback_without_digest.required,
        rollback_digest=canonical_digest(rollback_without_digest.body_dict()),
    )

    plan_without_digest = UpdatePlan(
        event_id=event_id,
        recorded_at=recorded_at,
        actor_digest=actor_digest,
        baseline=baseline_pin,
        candidate=candidate_pin,
        compatibility_evidence=evidence,
        rollback=rollback,
        actions=UPDATE_PLAN_ACTIONS,
        admission_enabled=False,
        network_accessed=False,
        runtime_changed=False,
        plan_digest="",
    )
    plan = UpdatePlan(
        event_id=plan_without_digest.event_id,
        recorded_at=plan_without_digest.recorded_at,
        actor_digest=plan_without_digest.actor_digest,
        baseline=plan_without_digest.baseline,
        candidate=plan_without_digest.candidate,
        compatibility_evidence=plan_without_digest.compatibility_evidence,
        rollback=plan_without_digest.rollback,
        actions=plan_without_digest.actions,
        admission_enabled=plan_without_digest.admission_enabled,
        network_accessed=plan_without_digest.network_accessed,
        runtime_changed=plan_without_digest.runtime_changed,
        plan_digest=canonical_digest(plan_without_digest.body_dict()),
    )

    receipt_without_digest = UpdateReceipt(
        event_id=event_id,
        recorded_at=recorded_at,
        actor_digest=actor_digest,
        status="prepared",
        plan_digest=plan.plan_digest,
        evidence_digest=evidence.evidence_digest,
        rollback_digest=rollback.rollback_digest,
        redacted_fields=RECEIPT_REDACTED_FIELDS,
        admission_enabled=False,
        network_accessed=False,
        runtime_changed=False,
        receipt_digest="",
    )
    receipt = UpdateReceipt(
        event_id=receipt_without_digest.event_id,
        recorded_at=receipt_without_digest.recorded_at,
        actor_digest=receipt_without_digest.actor_digest,
        status=receipt_without_digest.status,
        plan_digest=receipt_without_digest.plan_digest,
        evidence_digest=receipt_without_digest.evidence_digest,
        rollback_digest=receipt_without_digest.rollback_digest,
        redacted_fields=receipt_without_digest.redacted_fields,
        admission_enabled=receipt_without_digest.admission_enabled,
        network_accessed=receipt_without_digest.network_accessed,
        runtime_changed=receipt_without_digest.runtime_changed,
        receipt_digest=canonical_digest(receipt_without_digest.body_dict()),
    )
    return PreparedUpdate(plan=plan, receipt=receipt)


def build_prepared_update(
    schema: Mapping[str, object],
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
    *,
    event_id: str,
    recorded_at: str,
    actor_id: str,
) -> PreparedUpdate:
    """Build a deterministic, redacted plan and receipt without runtime effects."""

    return _build_prepared_update_with_actor_digest(
        schema,
        baseline,
        candidate,
        event_id=event_id,
        recorded_at=recorded_at,
        actor_digest=_actor_digest(actor_id),
    )


def verify_prepared_update(
    document: Mapping[str, object],
    schema: Mapping[str, object],
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> PreparedUpdate:
    """Replay one stored bundle and reject any mutation or unsafe state."""

    if document.get("version") != PREPARED_UPDATE_VERSION:
        raise UpdatePlanError("prepared update version mismatch")
    plan = _required_mapping(document, "plan", context="prepared_update")
    receipt = _required_mapping(document, "receipt", context="prepared_update")
    evidence = _required_mapping(
        plan,
        "compatibility_evidence",
        context="prepared_update.plan",
    )
    rollback = _required_mapping(plan, "rollback", context="prepared_update.plan")

    for name, section in (("plan", plan), ("receipt", receipt)):
        for flag in ("admission_enabled", "network_accessed", "runtime_changed"):
            if section.get(flag) is not False:
                raise UpdatePlanError(f"fail-closed {name}.{flag} must be false")
    if rollback.get("required") is not True:
        raise UpdatePlanError("fail-closed rollback.required must be true")
    if rollback.get("target_state") != "disabled":
        raise UpdatePlanError("fail-closed rollback target must remain disabled")
    if evidence.get("checker_status") != CheckStatus.COMPATIBLE_PINNED_UPDATE.value:
        raise UpdatePlanError("fail-closed compatibility evidence status mismatch")

    expected = _build_prepared_update_with_actor_digest(
        schema,
        baseline,
        candidate,
        event_id=_required_string(plan, "event_id", context="prepared_update.plan"),
        recorded_at=_required_string(
            plan,
            "recorded_at",
            context="prepared_update.plan",
        ),
        actor_digest=_required_string(
            plan,
            "actor_digest",
            context="prepared_update.plan",
        ),
    )
    if canonical_json_bytes(document) != canonical_json_bytes(expected.to_dict()):
        raise UpdatePlanError("prepared update content or digest mismatch")
    return expected


def _existing_prepared_bytes(path: Path) -> bytes | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UpdatePlanError(f"{path}: cannot inspect prepared update") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise UpdatePlanError(f"{path}: prepared update target must not be a symlink")
    if not stat.S_ISREG(metadata.st_mode):
        raise UpdatePlanError(f"{path}: prepared update target must be a regular file")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise UpdatePlanError(f"{path}: prepared update target mode must be 0600")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UpdatePlanError(f"{path}: cannot read prepared update safely") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UpdatePlanError(f"{path}: prepared update target must be a regular file")
        data = _read_descriptor_bounded(
            descriptor,
            max_bytes=MAX_PREPARED_UPDATE_BYTES,
        )
    finally:
        os.close(descriptor)
    if len(data) > MAX_PREPARED_UPDATE_BYTES:
        raise UpdatePlanError(
            f"{path}: prepared update exceeds {MAX_PREPARED_UPDATE_BYTES} byte limit",
        )
    return data


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise UpdatePlanError(f"{path}: cannot durably sync parent directory") from exc


def persist_prepared_update(
    path: Path,
    prepared: PreparedUpdate,
) -> PersistenceStatus:
    """Create one mode-0600 bundle atomically; never overwrite existing content."""

    payload = canonical_json_bytes(prepared.to_dict()) + b"\n"
    if len(payload) > MAX_PREPARED_UPDATE_BYTES:
        raise UpdatePlanError(
            f"prepared update exceeds {MAX_PREPARED_UPDATE_BYTES} byte limit",
        )
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise UpdatePlanError(f"{path}: prepared update parent must be a real directory")

    existing = _existing_prepared_bytes(path)
    if existing is not None:
        if existing == payload:
            return PersistenceStatus.UNCHANGED
        raise UpdatePlanError(f"{path}: existing prepared update has different content")

    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            raced = _existing_prepared_bytes(path)
            if raced == payload:
                return PersistenceStatus.UNCHANGED
            raise UpdatePlanError(
                f"{path}: existing prepared update has different content",
            ) from None
        _fsync_directory(path.parent)
        return PersistenceStatus.CREATED
    except UpdatePlanError:
        raise
    except OSError as exc:
        raise UpdatePlanError(f"{path}: could not atomically persist prepared update") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError as exc:
                raise UpdatePlanError(
                    f"{temporary}: could not clean temporary prepared update",
                ) from exc


def load_prepared_update(
    path: Path,
    schema: Mapping[str, object],
    baseline: Mapping[str, object],
    candidate: Mapping[str, object],
) -> PreparedUpdate:
    """Load and replay one bounded local bundle without applying its candidate."""

    try:
        document = _load_json_object_bounded(
            path,
            max_bytes=MAX_PREPARED_UPDATE_BYTES,
        )
    except ContractInputError as exc:
        raise UpdatePlanError(str(exc)) from exc
    return verify_prepared_update(document, schema, baseline, candidate)


def _print_report(report: CheckReport, *, label: str) -> None:
    for finding in report.findings:
        print(f"{label}: {finding}")
    rendered_status = (
        "ERROR" if report.status == CheckStatus.INVALID else report.status.value
    )
    summary = f"{label}: {rendered_status}"
    if report.status == CheckStatus.VALID_EXAMPLES:
        summary += (
            f" profiles={report.profile_count} examples={report.example_count}"
        )
    target = sys.stdout if report.ok else sys.stderr
    print(summary, file=target)


def main(argv: list[str] | None = None) -> int:
    """Run canonical validation and an optional baseline/candidate comparison."""

    parser = argparse.ArgumentParser(
        description="Check pinned typed-decision adapter contract updates.",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--schema", type=Path)
    parser.add_argument("--examples", type=Path)
    parser.add_argument("--baseline-profile", type=Path)
    parser.add_argument("--candidate-profile", type=Path)
    args = parser.parse_args(argv)

    if (args.baseline_profile is None) != (args.candidate_profile is None):
        print(
            "typed-decision-adapter-update: ERROR: --baseline-profile and "
            "--candidate-profile must be provided together",
            file=sys.stderr,
        )
        return 2

    schema_path = args.schema or args.root / DEFAULT_SCHEMA
    examples_path = args.examples or args.root / DEFAULT_EXAMPLES
    canonical = check_canonical_contract(schema_path, examples_path)
    _print_report(canonical, label="typed-decision-adapter-contract")
    if not canonical.ok:
        return 2
    if args.baseline_profile is None or args.candidate_profile is None:
        return 0

    try:
        schema = load_json_object(schema_path)
        baseline = load_json_object(args.baseline_profile)
        candidate = load_json_object(args.candidate_profile)
    except ContractInputError as exc:
        print(f"typed-decision-adapter-update: ERROR: {exc}", file=sys.stderr)
        return 2

    update = check_profile_update(schema, baseline, candidate)
    _print_report(update, label="typed-decision-adapter-update")
    return 0 if update.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
