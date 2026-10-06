"""Strict contracts for versioned replay bundle and event schemas."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError


def _manifest_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "gludd.run-bundle/v1",
        "run_id": "run-2026.10.05_01",
        "parent_run_id": None,
        "operation": "record",
        "created_at": "2026-10-05T12:00:00Z",
        "finalized_at": "2026-10-05T12:00:03Z",
        "status": "completed",
        "project_id": "project-alpha",
        "source": {
            "repository_url_sha256": "sha256:" + "1" * 64,
            "commit_sha": "2" * 40,
            "tree_sha": "3" * 40,
            "branch": "feat/replay",
            "dirty": False,
        },
        "runtime": {
            "gludd_version": "0.1.1",
            "python_version": "3.14.0",
            "os": "darwin",
            "architecture": "arm64",
            "config_sha256": "sha256:" + "4" * 64,
            "feature_flags": {"replay_v1": True},
        },
        "model": {
            "provider": "openai",
            "profile": "default",
            "model": "gpt-6",
            "request_parameters": {"temperature": 0.25},
            "provider_revision": None,
        },
        "event_count": 1,
        "events_sha256": "sha256:" + "5" * 64,
        "attachments": [],
        "completeness": {
            "expected_stages": ["run.started", "run.completed"],
            "observed_stages": ["run.started", "run.completed"],
            "recorder_errors": [],
            "missing_ranges": [],
        },
        "retention": {
            "expires_at": "2026-11-04T12:00:00Z",
            "pinned": False,
            "hold_reason": None,
        },
        "integrity": "unsigned",
        "signing_key_id": None,
    }
    payload.update(overrides)
    return payload


def _event_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": "gludd.run-event/v1",
        "sequence": 7,
        "event_id": "0199b152-e800-7000-8000-000000000001",
        "occurred_at": "2026-10-05T12:00:00Z",
        "recorded_at": "2026-10-05T12:00:00.010Z",
        "type": "tool.responded",
        "project_id": "project-alpha",
        "correlation": {
            "todo_id": "todo-7",
            "task_id": "task-9",
            "trace_id": "trace-11",
        },
        "payload": {"exit_code": 0, "duration_seconds": 0.125},
        "redaction": {"count": 1, "kinds": ["credential"]},
        "digest": "sha256:" + "6" * 64,
    }
    payload.update(overrides)
    return payload


def test_v1_models_parse_strict_payloads_and_emit_canonical_json() -> None:
    from general_ludd.replay.schema import (
        BUNDLE_SCHEMA_V1,
        EVENT_SCHEMA_V1,
        canonical_replay_json,
        parse_bundle_manifest,
        parse_event_envelope,
    )

    manifest = parse_bundle_manifest(json.dumps(_manifest_payload()))
    event = parse_event_envelope(json.dumps(_event_payload()))

    assert manifest.schema_version == BUNDLE_SCHEMA_V1
    assert manifest.run_id == "run-2026.10.05_01"
    assert manifest.created_at.utcoffset() is not None
    assert event.schema_version == EVENT_SCHEMA_V1
    assert event.sequence == 7
    assert event.payload["duration_seconds"] == 0.125
    first = canonical_replay_json(manifest)
    second = canonical_replay_json(
        parse_bundle_manifest(json.dumps(_manifest_payload(), sort_keys=True))
    )
    assert first == second
    assert first.encode("utf-8").decode("utf-8") == first
    assert " " not in first


@pytest.mark.parametrize(
    "run_id",
    [
        "../escape",
        "run/escape",
        "run\\escape",
        "r\u0430n",  # Cyrillic small a is a confusable.
        "run\u202efile",
        "CON",
        "nul.txt",
        "",
        "r" * 129,
    ],
)
def test_v1_manifest_rejects_unsafe_run_ids(run_id: str) -> None:
    from general_ludd.replay.schema import parse_bundle_manifest

    with pytest.raises(ValidationError, match="run_id"):
        parse_bundle_manifest(json.dumps(_manifest_payload(run_id=run_id)))


@pytest.mark.parametrize(
    ("parser_name", "payload_factory", "schema"),
    [
        ("bundle", _manifest_payload, "gludd.run-bundle/v2"),
        ("event", _event_payload, "gludd.run-event/v99"),
    ],
)
def test_parsers_fail_closed_on_unknown_schema_major(
    parser_name: str,
    payload_factory: Callable[..., dict[str, object]],
    schema: str,
) -> None:
    from general_ludd.replay.schema import (
        UnsupportedReplaySchemaError,
        parse_bundle_manifest,
        parse_event_envelope,
    )

    parser = parse_bundle_manifest if parser_name == "bundle" else parse_event_envelope
    with pytest.raises(UnsupportedReplaySchemaError, match=schema):
        parser(json.dumps(payload_factory(schema=schema)))


def test_v1_models_reject_extras_coercion_and_unknown_event_types() -> None:
    from general_ludd.replay.schema import parse_bundle_manifest, parse_event_envelope

    with pytest.raises(ValidationError, match="unexpected"):
        parse_bundle_manifest(json.dumps(_manifest_payload(unexpected=True)))
    with pytest.raises(ValidationError, match="sequence"):
        parse_event_envelope(json.dumps(_event_payload(sequence=True)))
    with pytest.raises(ValidationError, match="type"):
        parse_event_envelope(json.dumps(_event_payload(type="shell.executed")))
    nested_extra = _event_payload()
    nested_extra["redaction"] = {"count": 0, "kinds": [], "raw_secret": "no"}
    with pytest.raises(ValidationError, match="raw_secret"):
        parse_event_envelope(json.dumps(nested_extra))


@pytest.mark.parametrize("constant", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected_before_canonicalization(constant: float) -> None:
    from general_ludd.replay.schema import canonical_replay_json, parse_event_envelope

    wire = json.dumps(_event_payload(payload={"measurement": constant}))
    with pytest.raises(ValueError, match="finite"):
        parse_event_envelope(wire)
    with pytest.raises(ValueError, match="finite"):
        canonical_replay_json({"measurement": constant})


def test_manifest_lifecycle_and_attachment_invariants_fail_closed() -> None:
    from general_ludd.replay.schema import parse_bundle_manifest

    with pytest.raises(ValidationError, match="finalized_at"):
        parse_bundle_manifest(json.dumps(_manifest_payload(finalized_at=None)))
    attachment: dict[str, Any] = {
        "digest": "sha256:" + "a" * 64,
        "original_bytes": 4,
        "stored_bytes": 5,
        "media_type": "text/plain",
        "encoding": "utf-8",
        "redaction_count": 0,
        "truncated": False,
    }
    with pytest.raises(ValidationError, match="stored_bytes"):
        parse_bundle_manifest(json.dumps(_manifest_payload(attachments=[attachment])))


def test_signed_simulation_manifest_covers_partial_and_attachment_metadata() -> None:
    from general_ludd.replay.schema import parse_bundle_manifest, parse_event_envelope

    attachment = {
        "digest": "sha256:" + "a" * 64,
        "original_bytes": 12,
        "stored_bytes": 8,
        "media_type": "text/plain",
        "encoding": "utf-8",
        "redaction_count": 1,
        "truncated": True,
    }
    completeness = {
        "expected_stages": ["run.started", "run.completed"],
        "observed_stages": ["run.started"],
        "recorder_errors": ["worker-exited"],
        "missing_ranges": [{"start": 2, "end": 4}],
    }
    manifest = parse_bundle_manifest(
        _manifest_payload(
            operation="simulate",
            parent_run_id="parent-run",
            status="incomplete",
            finalized_at=None,
            project_id=None,
            attachments=[attachment],
            completeness=completeness,
            retention={"expires_at": None, "pinned": True, "hold_reason": "audit"},
            integrity="signed",
            signing_key_id="replay_integrity_v1",
        )
    )
    event = parse_event_envelope(
        _event_payload(payload={"nested": [None, True, 3, 0.5, "ok"]})
    )

    assert manifest.parent_run_id == "parent-run"
    assert manifest.attachments[0].truncated is True
    assert manifest.completeness.missing_ranges[0].end == 4
    assert event.payload["nested"] == [None, True, 3, 0.5, "ok"]


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"finalized_at": "2026-10-05T11:59:59Z"}, "precede"),
        ({"parent_run_id": "unexpected-parent"}, "must not declare"),
        ({"operation": "simulate"}, "require parent_run_id"),
        ({"integrity": "signed"}, "require signing_key_id"),
        ({"signing_key_id": "unexpected-key"}, "must not declare"),
    ],
)
def test_manifest_cross_field_invariants_reject_ambiguous_evidence(
    overrides: dict[str, object],
    message: str,
) -> None:
    from general_ludd.replay.schema import parse_bundle_manifest

    with pytest.raises(ValidationError, match=message):
        parse_bundle_manifest(_manifest_payload(**overrides))


def test_schema_rejects_duplicate_keys_ranges_redactions_and_non_json_values() -> None:
    from general_ludd.replay.schema import (
        ReplaySchemaError,
        canonical_replay_json,
        parse_bundle_manifest,
        parse_event_envelope,
    )

    duplicate = '{"schema":"gludd.run-event/v1","schema":"gludd.run-event/v1"}'
    with pytest.raises(ReplaySchemaError, match="duplicate"):
        parse_event_envelope(duplicate)
    with pytest.raises(ReplaySchemaError, match="object"):
        parse_event_envelope("[]")
    with pytest.raises(ReplaySchemaError, match="invalid"):
        parse_event_envelope("{")
    with pytest.raises(TypeError, match="object key"):
        canonical_replay_json({1: "not-text"})
    with pytest.raises(TypeError, match="tuple"):
        canonical_replay_json(("not", "json"))

    reverse_range = _manifest_payload()
    reverse_range["completeness"] = {
        "expected_stages": [],
        "observed_stages": [],
        "recorder_errors": [],
        "missing_ranges": [{"start": 4, "end": 2}],
    }
    with pytest.raises(ValidationError, match="precede"):
        parse_bundle_manifest(reverse_range)

    duplicate_attachments = _manifest_payload()
    attachment = {
        "digest": "sha256:" + "b" * 64,
        "original_bytes": 1,
        "stored_bytes": 1,
        "media_type": "text/plain",
        "encoding": None,
        "redaction_count": 0,
        "truncated": False,
    }
    duplicate_attachments["attachments"] = [attachment, attachment]
    with pytest.raises(ValidationError, match="unique"):
        parse_bundle_manifest(duplicate_attachments)

    duplicate_redactions = _event_payload(
        redaction={"count": 2, "kinds": ["credential", "credential"]}
    )
    with pytest.raises(ValidationError, match="unique"):
        parse_event_envelope(duplicate_redactions)
    missing_redaction_kind = _event_payload(redaction={"count": 1, "kinds": []})
    with pytest.raises(ValidationError, match="exactly"):
        parse_event_envelope(missing_redaction_kind)


def test_schema_rejects_naive_timestamps_and_traversal_tokens() -> None:
    from general_ludd.replay.schema import parse_bundle_manifest, parse_event_envelope

    with pytest.raises(ValidationError, match="timezone-aware"):
        parse_bundle_manifest(_manifest_payload(created_at="2026-10-05T12:00:00"))
    with pytest.raises(ValidationError, match="timezone-aware"):
        parse_event_envelope(_event_payload(recorded_at="2026-10-05T12:00:01"))
    for run_id in ("run..escape", "run-safe."):
        with pytest.raises(ValidationError, match="run_id"):
            parse_bundle_manifest(_manifest_payload(run_id=run_id))
