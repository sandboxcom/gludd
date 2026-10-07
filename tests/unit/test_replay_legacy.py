"""Compatibility contracts for read-only legacy-v0 replay access."""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.filestore.store import FileStore


def _store(tmp_path: Path) -> FileStore:
    return FileStore(root_path=str(tmp_path / "legacy-replays"))


def test_legacy_reader_preserves_numeric_order_and_never_rewrites_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from general_ludd.replay.legacy import LegacyReplayReader

    store = _store(tmp_path)
    store.write_text("runs/run-safe/events/10.json", '{"type":"end","step":10}')
    store.write_text("runs/run-safe/events/2.json", '{"type":"tool","step":2}')
    store.write_text("runs/run-safe/events/1.json", '{"type":"start","step":1}')
    before = store.tree("runs/run-safe")

    def forbid_write(_path: str, _content: str) -> None:
        raise AssertionError("legacy reads must never rewrite replay storage")

    monkeypatch.setattr(store, "write_text", forbid_write)
    replay = LegacyReplayReader(store).read("run-safe")

    assert replay.schema_version == "legacy-v0"
    assert replay.integrity == "unverified"
    assert [event.sequence for event in replay.events] == [1, 2, 10]
    assert [event.payload["step"] for event in replay.events] == [1, 2, 10]
    assert store.tree("runs/run-safe") == before


def test_legacy_reader_returns_a_typed_empty_view_for_missing_run(tmp_path: Path) -> None:
    from general_ludd.replay.legacy import read_legacy_run

    replay = read_legacy_run(_store(tmp_path), "missing-run")

    assert replay.run_id == "missing-run"
    assert replay.events == ()
    assert replay.model_dump(mode="json") == {
        "schema": "legacy-v0",
        "integrity": "unverified",
        "run_id": "missing-run",
        "events": [],
    }


@pytest.mark.parametrize(
    "run_id",
    ["../escape", "run/escape", "run\\escape", "r\u0430n", "run\u202efile", "AUX"],
)
def test_legacy_reader_validates_run_id_before_constructing_a_path(
    tmp_path: Path,
    run_id: str,
) -> None:
    from pydantic import ValidationError

    from general_ludd.replay.legacy import read_legacy_run

    with pytest.raises(ValidationError, match="run_id"):
        read_legacy_run(_store(tmp_path), run_id)


@pytest.mark.parametrize(
    ("filename", "payload", "message"),
    [
        ("event.json", "{}", "filename"),
        ("-1.json", "{}", "filename"),
        ("0.json", "[]", "object"),
        ("0.json", '{"metric":NaN}', "finite"),
        ("0.json", "{", "JSON"),
    ],
)
def test_legacy_reader_rejects_ambiguous_or_invalid_event_files(
    tmp_path: Path,
    filename: str,
    payload: str,
    message: str,
) -> None:
    from general_ludd.replay.legacy import LegacyReplayError, read_legacy_run

    store = _store(tmp_path)
    store.write_text(f"runs/run-safe/events/{filename}", payload)

    with pytest.raises(LegacyReplayError, match=message):
        read_legacy_run(store, "run-safe")


def test_legacy_reader_rejects_duplicate_numeric_sequences(tmp_path: Path) -> None:
    from general_ludd.replay.legacy import LegacyReplayError, read_legacy_run

    store = _store(tmp_path)
    store.write_text("runs/run-safe/events/1.json", '{"type":"first"}')
    store.write_text("runs/run-safe/events/01.json", '{"type":"duplicate"}')

    with pytest.raises(LegacyReplayError, match="duplicate"):
        read_legacy_run(store, "run-safe")


def test_legacy_reader_skips_directories_but_rejects_a_file_as_events_root(
    tmp_path: Path,
) -> None:
    from general_ludd.replay.legacy import LegacyReplayError, read_legacy_run

    store = _store(tmp_path)
    store.makedirs("runs/run-safe/events/ignored-directory")
    store.write_text("runs/run-safe/events/0.json", '{"type":"start"}')
    replay = read_legacy_run(store, "run-safe")
    assert [event.sequence for event in replay.events] == [0]

    file_store = _store(tmp_path / "file-root")
    file_store.write_text("runs/run-file/events", "not-a-directory")
    with pytest.raises(LegacyReplayError, match="directory"):
        read_legacy_run(file_store, "run-file")


def test_public_replay_package_exports_versioned_and_legacy_contracts() -> None:
    import general_ludd.replay as replay

    expected = {
        "BUNDLE_SCHEMA_V1",
        "EVENT_SCHEMA_V1",
        "BundleManifestV1",
        "EventEnvelopeV1",
        "LegacyReplayReader",
        "LegacyRun",
        "RunRecorder",
        "canonical_replay_json",
        "parse_bundle_manifest",
        "parse_event_envelope",
        "read_legacy_run",
    }

    assert expected <= set(replay.__all__)
