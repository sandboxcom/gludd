"""Boundary tests for replay telemetry integration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from prometheus_client import CollectorRegistry

from general_ludd.filestore.store import FileStore
from general_ludd.observability.metrics_exporter import MetricsExporter
from general_ludd.replay.recorder import RunRecorder
from general_ludd.replay.service import ReplayCatalogEntry, ReplayService
from general_ludd.replay.store import RunBundleStore
from general_ludd.replay.telemetry import ReplayTelemetry
from general_ludd.security.redaction import RedactionLimits

_KEY_ID = "telemetry-test-key"
_KEY = b"telemetry-test-key-material-32-bytes"


def _exporter() -> MetricsExporter:
    return MetricsExporter(registry=CollectorRegistry(auto_describe=False))


def _value(
    exporter: MetricsExporter,
    name: str,
    labels: dict[str, str],
) -> float:
    matches = [
        float(sample["value"])
        for family in exporter.get_json()["metrics"].values()
        for sample in family
        if sample["name"] == name and sample["labels"] == labels
    ]
    return sum(matches)


def _event(index: int = 0) -> dict[str, object]:
    return {
        "event_id": f"event-{index}",
        "occurred_at": "2026-10-05T12:00:00Z",
        "recorded_at": "2026-10-05T12:00:00.010Z",
        "type": "tool.responded",
        "project_id": "private-project-id",
        "correlation": {
            "todo_id": "private-todo-id",
            "task_id": "private-task-id",
            "trace_id": f"private-trace-{index}",
        },
        "payload": {"secret": "private-payload", "exit_code": 0},
        "redaction": {"count": 1, "kinds": ["secret_key"]},
    }


def _manifest(run_id: str, *, project_id: str = "private-project-id") -> dict[str, object]:
    created = datetime(2026, 10, 5, 12, tzinfo=UTC)
    return {
        "schema": "gludd.run-bundle/v1",
        "run_id": run_id,
        "parent_run_id": None,
        "operation": "record",
        "created_at": created.isoformat().replace("+00:00", "Z"),
        "finalized_at": (created + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "status": "completed",
        "project_id": project_id,
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
            "request_parameters": {},
            "provider_revision": None,
        },
        "event_count": 0,
        "events_sha256": "sha256:" + "0" * 64,
        "attachments": [],
        "completeness": {
            "expected_stages": ["run.started", "run.completed"],
            "observed_stages": ["run.started", "run.completed"],
            "recorder_errors": [],
            "missing_ranges": [],
        },
        "retention": {"expires_at": None, "pinned": False, "hold_reason": None},
        "integrity": "unsigned",
        "signing_key_id": None,
    }


def _store(root: Path, telemetry: ReplayTelemetry) -> RunBundleStore:
    return RunBundleStore(
        root,
        verification_keys={_KEY_ID: _KEY},
        active_key_id=_KEY_ID,
        telemetry=telemetry,
    )


def test_store_metrics_follow_durable_retry_finalize_and_verify_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)
    store = _store(tmp_path, telemetry)
    original_write = store._atomic_write
    failures = 0

    def fail_once(path: Path, payload: bytes) -> None:
        nonlocal failures
        failures += 1
        if failures == 1:
            raise OSError("private-storage-detail")
        original_write(path, payload)

    monkeypatch.setattr(store, "_atomic_write", fail_once)
    with pytest.raises(OSError, match="private-storage-detail"):
        store.append_event("private-run-id", _event())
    envelope = store.append_event("private-run-id", _event())
    manifest = store.finalize("private-run-id", _manifest("private-run-id"))
    verdict = store.verify("private-run-id")

    assert envelope.sequence == 0
    assert manifest.event_count == 1
    assert verdict.valid and verdict.complete
    assert _value(
        exporter,
        "gludd_replay_events_recorded_total",
        {"schema": "v1", "event_type": "tool.responded"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_record_failures_total",
        {"reason": "storage"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_operations_total",
        {"operation": "record", "outcome": "success"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_operations_total",
        {"operation": "record", "outcome": "failure"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_bundles_finalized_total",
        {"schema": "v1", "status": "completed"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_verify_total",
        {"schema": "v1", "outcome": "valid", "reason": "ok"},
    ) == 1
    rendered = exporter.render_prometheus()
    for private in (
        "private-run-id",
        "private-project-id",
        "private-todo-id",
        "private-task-id",
        "private-payload",
        "private-storage-detail",
    ):
        assert private not in rendered


def test_incomplete_and_corrupt_bundles_never_emit_successful_verification(
    tmp_path: Path,
) -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)
    store = _store(tmp_path, telemetry)

    incomplete = store.verify("incomplete-run")
    store.append_event("corrupt-run", _event())
    store.finalize("corrupt-run", _manifest("corrupt-run"))
    event_path = store.bundle_path("corrupt-run") / "events" / "000000000000.json"
    event_path.write_text("{}", encoding="utf-8")
    corrupt = store.verify("corrupt-run")

    assert not incomplete.valid and not incomplete.complete
    assert not corrupt.valid and not corrupt.complete
    assert _value(
        exporter,
        "gludd_replay_verify_total",
        {"schema": "v1", "outcome": "valid", "reason": "ok"},
    ) == 0
    invalid_total = sum(
        float(sample["value"])
        for family in exporter.get_json()["metrics"].values()
        for sample in family
        if sample["name"] == "gludd_replay_verify_total"
        and sample["labels"]["outcome"] == "invalid"
    )
    assert invalid_total == 2


class _Catalog:
    def __init__(self, entry: ReplayCatalogEntry) -> None:
        self._entry = entry

    def page(
        self, *, project_id: str | None, offset: int, limit: int
    ) -> tuple[ReplayCatalogEntry, ...]:
        rows = (self._entry,) if project_id == self._entry.project_id else ()
        return rows[offset : offset + limit]

    def get(self, run_id: str) -> ReplayCatalogEntry | None:
        return self._entry if run_id == self._entry.run_id else None


def test_service_operation_metrics_wait_for_complete_export_consumption(
    tmp_path: Path,
) -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)
    store = _store(tmp_path, telemetry)
    store.append_event("service-run", _event())
    store.finalize("service-run", _manifest("service-run"))
    entry = ReplayCatalogEntry(
        run_id="service-run",
        project_id="private-project-id",
        created_at=datetime(2026, 10, 5, 12, tzinfo=UTC),
    )
    service = ReplayService(
        store,
        catalog=_Catalog(entry),
        authorize=lambda capability, project_id: True,
        cursor_key=b"service-telemetry-cursor-key-material",
        telemetry=telemetry,
    )

    assert len(service.list_runs(project_id="private-project-id").items) == 1
    assert service.verify("service-run", project_id="private-project-id").valid
    stream = service.stream_export("service-run", project_id="private-project-id")
    assert _value(
        exporter,
        "gludd_replay_operations_total",
        {"operation": "export", "outcome": "success"},
    ) == 0
    assert hashlib.sha256(b"".join(stream)).hexdigest()
    assert _value(
        exporter,
        "gludd_replay_operations_total",
        {"operation": "export", "outcome": "success"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_operations_total",
        {"operation": "list", "outcome": "success"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_operations_total",
        {"operation": "verify", "outcome": "success"},
    ) == 1


class _FailEventPublishStore(FileStore):
    def move(self, src: str, dst: str) -> None:
        if dst.endswith("/events/0.json"):
            raise OSError("private-publish-failure")
        super().move(src, dst)


def test_legacy_metrics_emit_only_after_safe_publish_and_successful_read(
    tmp_path: Path,
) -> None:
    exporter = _exporter()
    telemetry = ReplayTelemetry(exporter)
    failed = RunRecorder(
        store=_FailEventPublishStore(root_path=str(tmp_path / "failed")),
        telemetry=telemetry,
    )
    with pytest.raises(OSError, match="private-publish-failure"):
        failed.record("private-failed-run", {"password": "private-secret"})

    recorder = RunRecorder(
        store=FileStore(root_path=str(tmp_path / "ok")),
        redaction_limits=RedactionLimits(max_string_chars=4),
        telemetry=telemetry,
    )
    recorder.record(
        "private-legacy-run",
        {
            "type": "run.started",
            "password": "private-secret",
            "message": "private-long-payload",
        },
    )
    assert recorder.replay("private-legacy-run")

    assert _value(
        exporter,
        "gludd_replay_events_recorded_total",
        {"schema": "legacy-v0", "event_type": "run.started"},
    ) == 1
    assert _value(exporter, "gludd_replay_legacy_reads_total", {}) == 1
    assert _value(
        exporter,
        "gludd_replay_record_failures_total",
        {"reason": "storage"},
    ) == 1
    assert _value(
        exporter,
        "gludd_replay_truncations_total",
        {"content_type": "event"},
    ) > 0
    rendered = exporter.render_prometheus()
    assert "private-legacy-run" not in rendered
    assert "private-secret" not in rendered
    assert "private-long-payload" not in rendered
    assert "private-publish-failure" not in rendered


class _FailingBackend:
    def __getattr__(self, name: str) -> Any:
        del name

        def fail(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("exporter unavailable")

        return fail


class _ExplosiveGetDict(dict[str, Any]):
    def get(self, key: str, default: object = None) -> Any:
        del key, default
        raise RuntimeError("telemetry must not inspect source mappings")


def test_telemetry_never_adds_source_mapping_reads_or_masks_storage_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry = ReplayTelemetry(_FailingBackend())
    store = _store(tmp_path / "v1", telemetry)
    event = _ExplosiveGetDict(_event())

    def fail_write(path: Path, payload: bytes) -> None:
        del path, payload
        raise OSError("original-storage-error")

    monkeypatch.setattr(store, "_atomic_write", fail_write)
    with pytest.raises(OSError, match="original-storage-error"):
        store.append_event("mapping-run", event)

    recorder = RunRecorder(
        store=FileStore(root_path=str(tmp_path / "legacy")),
        telemetry=telemetry,
    )
    recorder.record(
        "mapping-legacy-run",
        _ExplosiveGetDict({"type": "run.started", "ok": True}),
    )


def test_failing_exporter_never_changes_record_verify_or_read_behavior(
    tmp_path: Path,
) -> None:
    telemetry = ReplayTelemetry(_FailingBackend())
    store = _store(tmp_path / "v1", telemetry)
    event = store.append_event("backend-run", _event())
    manifest = store.finalize("backend-run", _manifest("backend-run"))
    verdict = store.verify("backend-run")
    recorder = RunRecorder(
        store=FileStore(root_path=str(tmp_path / "legacy")),
        telemetry=telemetry,
    )
    recorder.record("legacy-backend-run", {"type": "run.started", "ok": True})

    assert event.sequence == 0
    assert manifest.event_count == 1
    assert verdict.valid and verdict.complete
    assert recorder.replay("legacy-backend-run") == [
        {"ok": True, "type": "run.started"}
    ]
