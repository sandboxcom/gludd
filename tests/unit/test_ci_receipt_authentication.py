"""Authenticated, still-shadow-only receipt envelope contracts."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import cast

import pytest
from coverage import CoverageData
from scripts import run_ci_shards_serial as serial_runner
from scripts.ci_batch_receipts import (
    BatchReceiptRequest,
    FailureReceiptRequest,
    ShadowBatchReceiptWriter,
    ShadowFailureReceiptWriter,
    canonical_json_sha256,
    file_sha256,
    normalize_junit_failure_metadata,
)
from scripts.ci_batch_replay_audit import ReplayAuditRequest, ShadowReplayAuditor
from scripts.ci_gate_progress import ProgressBatch, ShadowGateProgress
from scripts.ci_receipt_auth import (
    MAX_AUTH_REVOKED_SIGNERS,
    MAX_AUTH_TRUSTED_SIGNERS,
    MAX_AUTH_VALIDITY_SECONDS,
    ReceiptSigner,
    ReceiptTrustPolicy,
    authenticate_receipt,
    authenticate_receipt_envelope,
    create_receipt_envelope,
    load_gate_receipt_auth_context,
    parse_revoked_signers,
)

_SHA = "a" * 40
_KEY = bytes(range(32))
_OTHER_KEY = bytes(reversed(range(32)))
_SIGNER = ReceiptSigner(key=_KEY, issued_at=1_000, validity_seconds=300)
_OTHER_SIGNER = ReceiptSigner(
    key=_OTHER_KEY,
    issued_at=1_000,
    validity_seconds=300,
)


def _policy(
    *,
    signer: ReceiptSigner = _SIGNER,
    now: int = 1_100,
    revoked: frozenset[str] = frozenset(),
) -> ReceiptTrustPolicy:
    return ReceiptTrustPolicy(
        trusted_keys={signer.signer_id: signer.key},
        verification_epoch=now,
        revoked_signers=revoked,
    )


def _identity() -> dict[str, object]:
    files = ["tests/unit/test_auth_example.py"]
    files_digest = canonical_json_sha256(files)
    return {
        "schema_version": 1,
        "source": {
            "candidate_sha": _SHA,
            "expected_sha": _SHA,
            "branch": "feature",
            "clean": True,
            "exact_sha": True,
            "queries_ok": True,
            "repository_state_id": "state-1",
            "test_files": files,
            "test_files_sha256": files_digest,
        },
        "plan": {
            "shard": "unit-1a1",
            "batch_index": 1,
            "max_files_per_batch": 16,
            "shard_plan_sha256": "1" * 64,
            "complete_plan_sha256": "2" * 64,
            "collection_manifest_kind": "canonical-test-file-plan-v1",
            "collection_manifest_sha256": files_digest,
            "execution_policy_sha256": "3" * 64,
        },
        "runner": {
            "implementation_sha256": "4" * 64,
            "attestation_sha256": "5" * 64,
            "pytest_args": ["-W", "error"],
            "heartbeat_seconds": 30.0,
            "no_progress_seconds": 600.0,
            "worker_count": 1,
            "distribution": "none",
            "cleanup_policy_version": 1,
        },
        "toolchain": {
            "python": "cpython-3.11.14",
            "executable_sha256": "6" * 64,
            "uv_version": "0.8.0",
            "lockfile_sha256": "7" * 64,
            "dependency_profile_sha256": "8" * 64,
            "installed_distributions_sha256": "9" * 64,
        },
        "plugins": {"inventory_sha256": "b" * 64},
        "coverage": {
            "config_sha256": "c" * 64,
            "branch": True,
            "source": ["src/general_ludd"],
            "omit": [],
            "paths": {},
            "concurrency": ["greenlet", "thread"],
            "data_schema": "coverage.py-7",
        },
        "platform": {
            "system": "Darwin",
            "release": "25.0",
            "machine": "arm64",
            "python_abi": "cpython-311",
            "resource_namespace_schema": 1,
        },
        "environment": {
            "allowlist": {"CI": "true"},
            "undeclared_names_sha256": "d" * 64,
            "restricted_inputs_present": False,
            "values_outside_allowlist_stored": False,
        },
        "external_inputs": [],
    }


def _write_coverage(path: Path) -> None:
    data = CoverageData(basename=str(path))
    data.add_arcs({"src/general_ludd/example.py": {(1, 2), (2, -1)}})
    data.write()
    data.close()


def _outcomes() -> dict[str, object]:
    node = canonical_json_sha256("node-1")
    return {
        "schema_version": 1,
        "counts": {
            "errors": 0,
            "failures": 0,
            "passed": 1,
            "skipped": 0,
            "tests": 1,
        },
        "node_id_sha256": canonical_json_sha256([node]),
        "terminal_outcome_sha256": canonical_json_sha256(
            [{"node_sha256": node, "outcome": "passed"}]
        ),
    }


def _publish_pass(
    tmp_path: Path,
    *,
    signer: ReceiptSigner | None,
) -> Path:
    coverage = tmp_path / "coverage.data"
    _write_coverage(coverage)
    identity = _identity()
    result = ShadowBatchReceiptWriter(
        tmp_path / "receipts",
        signer=signer,
    ).publish(
        BatchReceiptRequest(
            action_identity=identity,
            observed_action_identity=identity,
            coverage_path=coverage,
            outcome_manifest=_outcomes(),
            originating_run_id="run-1",
            started_at="2026-10-07T00:00:00Z",
            completed_at="2026-10-07T00:00:01Z",
            returncode=0,
            cleanup_returncode=0,
        )
    )
    assert result.published is True
    assert result.path is not None
    return result.path


def _audit_request(tmp_path: Path) -> ReplayAuditRequest:
    coverage = tmp_path / "current.coverage"
    _write_coverage(coverage)
    identity = _identity()
    return ReplayAuditRequest(
        action_identity=identity,
        observed_action_identity=identity,
        coverage_path=coverage,
        outcome_manifest=_outcomes(),
        returncode=0,
        cleanup_returncode=0,
    )


def _batch() -> ProgressBatch:
    identity = _identity()
    source = cast(dict[str, object], identity["source"])
    plan = cast(dict[str, object], identity["plan"])
    return ProgressBatch(
        shard=str(plan["shard"]),
        batch_index=cast(int, plan["batch_index"]),
        test_files_sha256=str(source["test_files_sha256"]),
        collection_manifest_sha256=str(plan["collection_manifest_sha256"]),
    )


def test_envelope_reuses_bounded_hmac_attestation_without_leaking_inputs() -> None:
    envelope: dict[str, object] | None = create_receipt_envelope(
        receipt_kind="pass",
        action_digest="a" * 64,
        content_sha256="b" * 64,
        signer=_SIGNER,
    )

    result = authenticate_receipt_envelope(
        envelope,
        expected_kind="pass",
        expected_action_digest="a" * 64,
        expected_content_sha256="b" * 64,
        trust_policy=_policy(),
    )

    assert result.status == "verified"
    assert result.verified is True
    serialized = json.dumps(envelope, sort_keys=True)
    assert _KEY.hex() not in serialized
    assert "/private/secret-key" not in serialized
    assert "test payload" not in serialized
    assert len(serialized.encode("ascii")) < 4096


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("unsigned", "unsigned-legacy"),
        ("unknown", "unknown-signer"),
        ("tampered", "tampered"),
        ("revoked", "revoked"),
        ("expired", "expired"),
        ("malformed", "malformed"),
    ],
)
def test_authentication_classifies_every_nonverified_state(
    state: str,
    expected: str,
) -> None:
    signer = _OTHER_SIGNER if state == "unknown" else _SIGNER
    envelope: dict[str, object] | None = create_receipt_envelope(
        receipt_kind="pass",
        action_digest="a" * 64,
        content_sha256="b" * 64,
        signer=signer,
    )
    policy = _policy()
    if state == "unsigned":
        envelope = None
    elif state == "tampered":
        assert envelope is not None
        envelope = {**envelope, "signature": "0" * 64}
    elif state == "revoked":
        policy = _policy(revoked=frozenset({_SIGNER.signer_id}))
    elif state == "expired":
        policy = _policy(now=1_301)
    elif state == "malformed":
        envelope = {}

    result = authenticate_receipt_envelope(
        envelope,
        expected_kind="pass",
        expected_action_digest="a" * 64,
        expected_content_sha256="b" * 64,
        trust_policy=policy,
    )

    assert result.status == expected
    assert result.verified is False


def test_authentication_policy_and_validity_are_hard_bounded() -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        ReceiptSigner(key=b"short", issued_at=1, validity_seconds=1)
    with pytest.raises(ValueError, match="validity"):
        ReceiptSigner(key=_KEY, issued_at=1, validity_seconds=0)
    with pytest.raises(ValueError, match="validity"):
        ReceiptSigner(
            key=_KEY,
            issued_at=1,
            validity_seconds=MAX_AUTH_VALIDITY_SECONDS + 1,
        )
    keys = {
        ReceiptSigner(bytes([index]) * 32, 1, 1).signer_id: bytes([index]) * 32
        for index in range(MAX_AUTH_TRUSTED_SIGNERS + 1)
    }
    with pytest.raises(ValueError, match="trusted signer"):
        ReceiptTrustPolicy(trusted_keys=keys, verification_epoch=1)
    with pytest.raises(ValueError, match="issued_at"):
        ReceiptSigner(key=_KEY, issued_at=-1, validity_seconds=1)
    with pytest.raises(ValueError, match="verification_epoch"):
        ReceiptTrustPolicy(trusted_keys={}, verification_epoch=-1)
    with pytest.raises(ValueError, match="revoked signer count"):
        ReceiptTrustPolicy(
            trusted_keys={},
            verification_epoch=1,
            revoked_signers=frozenset(
                f"{index:064x}" for index in range(MAX_AUTH_REVOKED_SIGNERS + 1)
            ),
        )
    with pytest.raises(ValueError, match="revoked signer identity"):
        ReceiptTrustPolicy(
            trusted_keys={},
            verification_epoch=1,
            revoked_signers=frozenset({"invalid"}),
        )
    with pytest.raises(ValueError, match="trusted signer identity"):
        ReceiptTrustPolicy(
            trusted_keys={_SIGNER.signer_id: _OTHER_KEY},
            verification_epoch=1,
        )


def test_authentication_rejects_misbinding_missing_trust_and_bad_digests() -> None:
    envelope = create_receipt_envelope(
        receipt_kind="pass",
        action_digest="a" * 64,
        content_sha256="b" * 64,
        signer=_SIGNER,
    )

    misbound = authenticate_receipt_envelope(
        envelope,
        expected_kind="pass",
        expected_action_digest="c" * 64,
        expected_content_sha256="b" * 64,
        trust_policy=_policy(),
    )
    missing_trust = authenticate_receipt_envelope(
        envelope,
        expected_kind="pass",
        expected_action_digest="a" * 64,
        expected_content_sha256="b" * 64,
        trust_policy=None,
    )

    assert misbound.status == "tampered"
    assert missing_trust.status == "unknown-signer"
    with pytest.raises(ValueError, match="digest"):
        create_receipt_envelope(
            receipt_kind="pass",
            action_digest="invalid",
            content_sha256="b" * 64,
            signer=_SIGNER,
        )


def test_gate_key_lifecycle_is_private_bounded_and_path_silent(tmp_path: Path) -> None:
    key_path = tmp_path / "private" / "receipt.key"

    context = load_gate_receipt_auth_context(key_path, issued_at=1_000)

    assert key_path.stat().st_mode & 0o777 == 0o600
    assert context.signer.signer_id in context.trust_policy.trusted_keys
    rendered = repr(context)
    assert context.signer.key.hex() not in rendered
    assert str(key_path) not in rendered

    key_path.chmod(0o644)
    with pytest.raises(ValueError) as raised:
        load_gate_receipt_auth_context(key_path, issued_at=1_000)
    assert str(key_path) not in str(raised.value)
    assert context.signer.key.hex() not in str(raised.value)

    key_path.chmod(0o600)
    with pytest.raises(ValueError, match="active receipt signer is revoked"):
        load_gate_receipt_auth_context(
            key_path,
            issued_at=1_000,
            revoked_signers=frozenset({context.signer.signer_id}),
        )

    directory_path = tmp_path / "not-a-key"
    directory_path.mkdir()
    with pytest.raises(ValueError) as unavailable:
        load_gate_receipt_auth_context(directory_path, issued_at=1_000)
    assert str(directory_path) not in str(unavailable.value)


def test_gate_key_loader_rejects_symlink_without_disclosing_target(tmp_path: Path) -> None:
    target = tmp_path / "target.key"
    target.write_bytes(_KEY)
    target.chmod(0o600)
    alias = tmp_path / "alias.key"
    alias.symlink_to(target)

    with pytest.raises(ValueError) as raised:
        load_gate_receipt_auth_context(alias, issued_at=1_000)

    assert str(target) not in str(raised.value)
    assert _KEY.hex() not in str(raised.value)


def test_receipt_envelope_reader_rejects_unsafe_and_ambiguous_json(
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "receipt"
    receipt.mkdir()
    envelope_path = receipt / "attestation.json"
    envelope_path.write_text(
        '{"schema_version":1,"schema_version":1}\n',
        encoding="ascii",
    )
    envelope_path.chmod(0o600)
    def classify() -> str:
        return authenticate_receipt(
            receipt,
            expected_kind="pass",
            expected_action_digest="a" * 64,
            expected_content_sha256="b" * 64,
            trust_policy=_policy(),
        ).status

    assert classify() == "malformed"

    envelope_path.write_text("[]\n", encoding="ascii")
    assert classify() == "malformed"

    envelope_path.chmod(0o644)
    assert classify() == "malformed"

    envelope_path.unlink()
    target = tmp_path / "target-envelope"
    target.write_text("{}\n", encoding="ascii")
    target.chmod(0o600)
    envelope_path.symlink_to(target)
    assert classify() == "malformed"


def test_revocation_policy_is_exact_unique_and_hard_bounded() -> None:
    signer_ids = [f"{index:064x}" for index in range(MAX_AUTH_REVOKED_SIGNERS)]
    assert parse_revoked_signers(",".join(signer_ids)) == frozenset(signer_ids)

    with pytest.raises(ValueError, match="malformed or unbounded"):
        parse_revoked_signers("bad")
    with pytest.raises(ValueError, match="malformed or unbounded"):
        parse_revoked_signers(f"{signer_ids[0]},{signer_ids[0]}")
    with pytest.raises(ValueError, match="malformed or unbounded"):
        parse_revoked_signers(",".join([*signer_ids, "f" * 64]))


def test_signed_writer_envelope_authenticates_exact_content(tmp_path: Path) -> None:
    receipt = _publish_pass(tmp_path, signer=_SIGNER)

    result = authenticate_receipt(
        receipt,
        expected_kind="pass",
        expected_action_digest=canonical_json_sha256(_identity()),
        expected_content_sha256=file_sha256(receipt / "manifest.json"),
        trust_policy=_policy(),
    )

    assert result.status == "verified"
    assert (receipt / "attestation.json").stat().st_mode & 0o777 == 0o600
    serialized = (receipt / "attestation.json").read_text(encoding="ascii")
    assert _KEY.hex() not in serialized
    assert str(tmp_path) not in serialized
    assert "test_auth_example.py" not in serialized


@pytest.mark.parametrize(
    ("state", "reason", "authentication", "eligible"),
    [
        ("verified", "exact-safe-candidate", "verified", True),
        ("unsigned", "receipt-auth-unsigned-legacy", "unsigned-legacy", False),
        ("unknown", "receipt-auth-unknown-signer", "unknown-signer", False),
        ("tampered", "receipt-auth-tampered", "tampered", False),
        ("revoked", "receipt-auth-revoked", "revoked", False),
        ("expired", "receipt-auth-expired", "expired", False),
        ("malformed", "receipt-auth-malformed", "malformed", False),
    ],
)
def test_auditor_and_progress_distinguish_authentication_without_skips(
    tmp_path: Path,
    state: str,
    reason: str,
    authentication: str,
    eligible: bool,
) -> None:
    signer = None if state == "unsigned" else _OTHER_SIGNER if state == "unknown" else _SIGNER
    receipt = _publish_pass(tmp_path, signer=signer)
    policy = _policy()
    attestation = receipt / "attestation.json"
    if state == "tampered":
        envelope = json.loads(attestation.read_text(encoding="ascii"))
        envelope["signature"] = "0" * 64
        attestation.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="ascii")
    elif state == "revoked":
        policy = _policy(revoked=frozenset({_SIGNER.signer_id}))
    elif state == "expired":
        policy = _policy(now=1_301)
    elif state == "malformed":
        attestation.write_text("{}\n", encoding="ascii")

    result = ShadowReplayAuditor(
        tmp_path / "receipts",
        candidate_sha=_SHA,
        trust_policy=policy,
    ).audit(_audit_request(tmp_path))
    tracker = ShadowGateProgress(
        candidate_sha=_SHA,
        batches=[_batch()],
        completed_receipts=[receipt],
        trust_policy=policy,
    )
    summary = tracker.summary()

    assert result.eligible is eligible
    assert result.reason == reason
    assert result.authentication == authentication
    assert result.skip_authorized is False
    assert summary["timing"]["authentication_status"] == authentication
    if eligible:
        assert summary["timing"]["evidence_status"] == "valid"
        assert summary["timing"]["samples_retained"] == 1
    else:
        assert summary["timing"]["evidence_status"] == f"rejected-auth-{authentication}"
        assert summary["timing"]["samples_retained"] == 0
    assert summary["skips"] == 0
    rendered = tracker.render(summary).lower()
    assert _KEY.hex() not in rendered
    assert str(tmp_path).lower() not in rendered


def test_verified_failure_receipt_remains_non_reusable(tmp_path: Path) -> None:
    junit = tmp_path / "failure.xml"
    junit.write_text(
        "<testsuite><testcase classname='suite' name='failed'>"
        "<failure>password=do-not-store</failure></testcase></testsuite>",
        encoding="utf-8",
    )
    identity = _identity()
    publication = ShadowFailureReceiptWriter(
        tmp_path / "receipts",
        signer=_SIGNER,
    ).publish(
        FailureReceiptRequest(
            action_identity=identity,
            observed_action_identity=identity,
            failure_node_metadata=normalize_junit_failure_metadata(junit),
            originating_run_id="run-1",
            elapsed_seconds=1.25,
            returncode=1,
            cleanup_returncode=0,
        )
    )
    assert publication.published is True

    result = ShadowReplayAuditor(
        tmp_path / "receipts",
        candidate_sha=_SHA,
        trust_policy=_policy(),
    ).audit(_audit_request(tmp_path))

    assert result.eligible is False
    assert result.reason == "prior-failure-non-reusable"
    assert result.authentication == "verified"
    assert result.skip_authorized is False


def test_cli_authentication_rollback_keeps_shadow_execution_and_zero_skips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    identity = {
        "head_sha": _SHA,
        "expected_sha": _SHA,
        "branch": "feature",
        "clean": True,
        "exact_sha": True,
        "queries_ok": True,
    }
    received: list[bool] = []

    def create_session(**kwargs: object) -> object:
        enabled = kwargs["receipt_authentication_enabled"]
        assert isinstance(enabled, bool)
        received.append(enabled)
        return object()

    monkeypatch.setattr(serial_runner, "_repository_identity", lambda **_kwargs: identity)
    monkeypatch.setattr(serial_runner, "_attestation_pairing", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(serial_runner, "_create_shadow_receipt_session", create_session)
    monkeypatch.setattr(serial_runner, "run", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr(
        serial_runner,
        "_write_terminal_attestation",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        serial_runner,
        "_resource_paths",
        lambda: serial_runner.ResourcePaths(
            root=tmp_path,
            coverage_shards=tmp_path / "coverage-fragments",
            coverage_json=tmp_path / "coverage.json",
            coverage_audit=tmp_path / "coverage-audit.json",
            attestation=tmp_path / "attestation.json",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_ci_shards_serial.py",
            "--shards=unit-1a1",
            "--skip-isolated",
            "--skip-aggregate",
            "--no-shadow-receipt-authentication",
        ],
    )

    assert serial_runner.main() == 0
    assert received == [False]
    output = capsys.readouterr().out
    assert (
        "BATCH-RECEIPT-AUTH-SHADOW status=disabled reason=operator-off "
        "future_eligible=0 skips=0"
    ) in output
