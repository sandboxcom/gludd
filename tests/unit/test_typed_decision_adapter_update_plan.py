"""Atomic plan and receipt contracts for typed-decision adapter pin updates."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import stat
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_typed_decision_adapter_update.py"
SCHEMA = ROOT / "docs" / "releases" / "typed-decision-adapter-contract-v1.schema.json"
EXAMPLES = (
    ROOT
    / "docs"
    / "releases"
    / "typed-decision-adapter-contract-v1.examples.json"
)


def _load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_typed_decision_adapter_update_plan",
        SCRIPT,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


class _EvidenceLike(Protocol):
    checker_status: str
    changed_pin_paths: tuple[str, ...]
    protected_sections_verified: tuple[str, ...]
    findings: tuple[str, ...]
    schema_digest: str
    baseline_profile_digest: str
    candidate_profile_digest: str
    evidence_digest: str


class _RollbackLike(Protocol):
    required: bool
    target_state: str
    baseline_profile_digest: str
    profile_id: str
    identity_fingerprint: str
    source_commit: str
    source_archive_digest: str
    rollback_digest: str


class _PlanLike(Protocol):
    actions: tuple[str, ...]
    actor_digest: str
    compatibility_evidence: _EvidenceLike
    rollback: _RollbackLike
    plan_digest: str
    admission_enabled: bool
    network_accessed: bool
    runtime_changed: bool


class _ReceiptLike(Protocol):
    status: str
    redacted_fields: tuple[str, ...]
    plan_digest: str
    evidence_digest: str
    rollback_digest: str
    admission_enabled: bool
    network_accessed: bool
    runtime_changed: bool


class _PreparedLike(Protocol):
    plan: _PlanLike
    receipt: _ReceiptLike

    def to_dict(self) -> dict[str, object]:
        """Return a serializable prepared-update document."""


def _digests(seed: str) -> Iterator[str]:
    index = 0
    while True:
        yield "sha256:" + hashlib.sha256(f"{seed}-{index}".encode()).hexdigest()
        index += 1


def _replace_digests(value: object, digests: Iterator[str]) -> object:
    if isinstance(value, dict):
        return {key: _replace_digests(item, digests) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace_digests(item, digests) for item in value]
    if isinstance(value, str) and value.startswith("sha256:"):
        return next(digests)
    return value


def _profiles() -> tuple[dict[str, object], dict[str, object]]:
    bundle = json.loads(EXAMPLES.read_text(encoding="utf-8"))
    assert isinstance(bundle, dict)
    profiles = bundle["profiles"]
    assert isinstance(profiles, list) and isinstance(profiles[0], dict)
    baseline = _replace_digests(copy.deepcopy(profiles[0]), _digests("baseline"))
    assert isinstance(baseline, dict)
    baseline["example_only"] = False
    source = baseline["source_pin"]
    identity = baseline["identity"]
    assert isinstance(source, dict) and isinstance(identity, dict)
    source["release"] = "v1.0.0"
    source["commit"] = hashlib.sha1(b"baseline-source").hexdigest()
    identity["model_revision"] = "model@v1.0.0"

    candidate = copy.deepcopy(baseline)
    source = candidate["source_pin"]
    identity = candidate["identity"]
    assert isinstance(source, dict) and isinstance(identity, dict)
    source["release"] = "v1.0.1"
    source["commit"] = hashlib.sha1(b"candidate-source").hexdigest()
    source["source_archive_digest"] = next(_digests("candidate-archive"))
    source["reviewed_at"] = "2026-09-28"
    identity["fingerprint"] = next(_digests("candidate-fingerprint"))
    identity["backend_protocol_version"] = "observed-2026-09-28"
    identity["server_digest"] = next(_digests("candidate-server"))
    identity["runtime_digest"] = next(_digests("candidate-runtime"))
    return baseline, candidate


def _inputs() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    schema = checker.load_json_object(SCHEMA)
    baseline, candidate = _profiles()
    return schema, baseline, candidate


def _prepared(
    *,
    actor_id: str = "release-operator@example.invalid",
    event_id: str = "update-2026-09-28-001",
) -> tuple[_PreparedLike, dict[str, object], dict[str, object], dict[str, object]]:
    schema, baseline, candidate = _inputs()
    prepared = checker.build_prepared_update(
        schema,
        baseline,
        candidate,
        event_id=event_id,
        recorded_at="2026-09-28T12:30:45Z",
        actor_id=actor_id,
    )
    return prepared, schema, baseline, candidate


def test_build_is_deterministic_redacted_and_runtime_inert() -> None:
    first, _, _, _ = _prepared()
    second, _, _, _ = _prepared()
    assert first == second
    assert first.plan.actions == checker.UPDATE_PLAN_ACTIONS
    assert first.plan.compatibility_evidence.changed_pin_paths == (
        "identity.backend_protocol_version",
        "identity.fingerprint",
        "identity.runtime_digest",
        "identity.server_digest",
        "source_pin.commit",
        "source_pin.release",
        "source_pin.reviewed_at",
        "source_pin.source_archive_digest",
    )
    assert first.receipt.status == "prepared"
    assert first.receipt.redacted_fields == checker.RECEIPT_REDACTED_FIELDS
    rendered = checker.canonical_json_bytes(first.to_dict())
    assert b"release-operator@example.invalid" not in rendered
    assert first.plan.actor_digest.startswith("sha256:")
    assert first.plan.admission_enabled is False
    assert first.plan.network_accessed is False
    assert first.plan.runtime_changed is False
    assert first.receipt.admission_enabled is False
    assert first.receipt.network_accessed is False
    assert first.receipt.runtime_changed is False


def test_plan_captures_compatibility_and_exact_rollback_evidence() -> None:
    prepared, schema, baseline, candidate = _prepared()
    baseline_source = baseline["source_pin"]
    baseline_identity = baseline["identity"]
    assert isinstance(baseline_source, dict) and isinstance(baseline_identity, dict)
    evidence = prepared.plan.compatibility_evidence
    rollback = prepared.plan.rollback
    assert evidence.checker_status == "COMPATIBLE_PINNED_UPDATE"
    assert evidence.findings == ()
    assert evidence.schema_digest == checker.canonical_digest(schema)
    assert evidence.baseline_profile_digest == checker.canonical_digest(baseline)
    assert evidence.candidate_profile_digest == checker.canonical_digest(candidate)
    assert evidence.protected_sections_verified == checker.PROTECTED_UPDATE_SECTIONS
    assert rollback.required is True
    assert rollback.target_state == "disabled"
    assert rollback.baseline_profile_digest == checker.canonical_digest(baseline)
    assert rollback.profile_id == baseline["profile_id"]
    assert rollback.identity_fingerprint == baseline_identity["fingerprint"]
    assert rollback.source_commit == baseline_source["commit"]
    assert rollback.source_archive_digest == baseline_source["source_archive_digest"]
    assert prepared.receipt.plan_digest == prepared.plan.plan_digest
    assert prepared.receipt.evidence_digest == evidence.evidence_digest
    assert prepared.receipt.rollback_digest == rollback.rollback_digest


@pytest.mark.parametrize(
    "mutation",
    [
        "noop",
        "shadow",
        "example-only",
        "sentinel",
        "contract-change",
    ],
)
def test_build_rejects_every_non_pinned_compatible_transition(mutation: str) -> None:
    schema, baseline, candidate = _inputs()
    if mutation == "noop":
        candidate = copy.deepcopy(baseline)
    elif mutation == "shadow":
        candidate["profile_state"] = "shadow"
    elif mutation == "example-only":
        candidate["example_only"] = True
    elif mutation == "sentinel":
        source = candidate["source_pin"]
        assert isinstance(source, dict)
        source["commit"] = "0" * 40
    else:
        candidate["transport"] = {"changed": True}
    with pytest.raises(checker.UpdatePlanError, match="pinned update"):
        checker.build_prepared_update(
            schema,
            baseline,
            candidate,
            event_id="update-001",
            recorded_at="2026-09-28T12:30:45Z",
            actor_id="operator",
        )


@pytest.mark.parametrize(
    ("event_id", "recorded_at", "actor_id", "expected"),
    [
        ("", "2026-09-28T12:30:45Z", "operator", "event_id"),
        ("contains spaces", "2026-09-28T12:30:45Z", "operator", "event_id"),
        ("update-001", "2026-09-28", "operator", "recorded_at"),
        ("update-001", "2026-09-28T12:30:45+00:00", "operator", "recorded_at"),
        ("update-001", "2026-09-28T12:30:45Z", "", "actor_id"),
        ("update-001", "2026-09-28T12:30:45Z", "x" * 257, "actor_id"),
    ],
)
def test_build_rejects_ambiguous_or_unbounded_metadata(
    event_id: str,
    recorded_at: str,
    actor_id: str,
    expected: str,
) -> None:
    schema, baseline, candidate = _inputs()
    with pytest.raises(checker.UpdatePlanError, match=expected):
        checker.build_prepared_update(
            schema,
            baseline,
            candidate,
            event_id=event_id,
            recorded_at=recorded_at,
            actor_id=actor_id,
        )


def test_verify_replays_idempotently_and_rejects_tampering() -> None:
    prepared, schema, baseline, candidate = _prepared()
    document = prepared.to_dict()
    assert checker.verify_prepared_update(document, schema, baseline, candidate) == prepared

    tampered_documents: list[dict[str, object]] = []
    for section, field, value in (
        ("plan", "runtime_changed", True),
        ("receipt", "network_accessed", True),
        ("plan", "plan_digest", "sha256:" + "1" * 64),
        ("receipt", "receipt_digest", "sha256:" + "2" * 64),
    ):
        tampered = copy.deepcopy(document)
        nested = tampered[section]
        assert isinstance(nested, dict)
        nested[field] = value
        tampered_documents.append(tampered)

    tampered = copy.deepcopy(document)
    plan = tampered["plan"]
    assert isinstance(plan, dict)
    rollback = plan["rollback"]
    assert isinstance(rollback, dict)
    rollback["required"] = False
    tampered_documents.append(tampered)

    tampered = copy.deepcopy(document)
    plan = tampered["plan"]
    assert isinstance(plan, dict)
    evidence = plan["compatibility_evidence"]
    assert isinstance(evidence, dict)
    evidence["evidence_digest"] = "sha256:" + "3" * 64
    tampered_documents.append(tampered)

    for tampered in tampered_documents:
        with pytest.raises(checker.UpdatePlanError, match=r"mismatch|fail-closed"):
            checker.verify_prepared_update(tampered, schema, baseline, candidate)

    different_baseline = copy.deepcopy(baseline)
    different_baseline["profile_id"] = "different-profile"
    with pytest.raises(checker.UpdatePlanError, match=r"pinned update|mismatch"):
        checker.verify_prepared_update(document, schema, different_baseline, candidate)


def test_persist_load_and_replay_are_atomic_and_idempotent(tmp_path: Path) -> None:
    prepared, schema, baseline, candidate = _prepared()
    output = tmp_path / "prepared-update.json"
    assert checker.persist_prepared_update(output, prepared) == checker.PersistenceStatus.CREATED
    before = output.stat()
    before_bytes = output.read_bytes()
    assert stat.S_IMODE(before.st_mode) == 0o600
    assert before_bytes.endswith(b"\n")
    assert checker.load_prepared_update(output, schema, baseline, candidate) == prepared
    assert checker.persist_prepared_update(output, prepared) == checker.PersistenceStatus.UNCHANGED
    after = output.stat()
    assert output.read_bytes() == before_bytes
    assert after.st_mtime_ns == before.st_mtime_ns


def test_persist_never_overwrites_conflicts_or_unsafe_targets(tmp_path: Path) -> None:
    first, _, _, _ = _prepared(event_id="update-001")
    second, _, _, _ = _prepared(event_id="update-002")
    output = tmp_path / "prepared-update.json"
    assert checker.persist_prepared_update(output, first) == checker.PersistenceStatus.CREATED
    original = output.read_bytes()
    with pytest.raises(checker.UpdatePlanError, match="different content"):
        checker.persist_prepared_update(output, second)
    assert output.read_bytes() == original

    link = tmp_path / "receipt-link.json"
    link.symlink_to(output)
    with pytest.raises(checker.UpdatePlanError, match="symlink"):
        checker.persist_prepared_update(link, first)
    with pytest.raises(checker.UpdatePlanError, match="regular file"):
        checker.persist_prepared_update(tmp_path, first)
    with pytest.raises(checker.UpdatePlanError, match="parent"):
        checker.persist_prepared_update(tmp_path / "missing" / "receipt.json", first)


def test_load_rejects_malformed_symlink_and_oversized_receipts(tmp_path: Path) -> None:
    prepared, schema, baseline, candidate = _prepared()
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{invalid}\n", encoding="utf-8")
    with pytest.raises(checker.UpdatePlanError, match="invalid JSON"):
        checker.load_prepared_update(malformed, schema, baseline, candidate)

    target = tmp_path / "target.json"
    checker.persist_prepared_update(target, prepared)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(checker.UpdatePlanError, match="symlink"):
        checker.load_prepared_update(link, schema, baseline, candidate)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b" " * checker.MAX_PREPARED_UPDATE_BYTES + b"}")
    with pytest.raises(checker.UpdatePlanError, match="byte limit"):
        checker.load_prepared_update(oversized, schema, baseline, candidate)


def test_failed_atomic_create_cleans_temporary_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, _, _, _ = _prepared()

    def fail_link(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("simulated atomic-create failure")

    monkeypatch.setattr(checker.os, "link", fail_link)
    with pytest.raises(checker.UpdatePlanError, match="atomically persist"):
        checker.persist_prepared_update(tmp_path / "receipt.json", prepared)
    assert list(tmp_path.iterdir()) == []


def test_persist_enforces_strict_bundle_size_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared, _, _, _ = _prepared()
    monkeypatch.setattr(checker, "MAX_PREPARED_UPDATE_BYTES", 100)
    with pytest.raises(checker.UpdatePlanError, match="byte limit"):
        checker.persist_prepared_update(tmp_path / "receipt.json", prepared)
