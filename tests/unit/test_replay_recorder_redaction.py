"""Security and compatibility tests for the legacy replay recorder capture path."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from general_ludd.filestore.store import FileStore
from general_ludd.replay.recorder import CaptureMetadata, RunRecorder
from general_ludd.security.redaction import REDACTED_VALUE, RedactionLimits


def _store(tmp_path: Path) -> FileStore:
    return FileStore(root_path=str(tmp_path / "replays"))


def _persisted_bytes(root: Path) -> bytes:
    return b"\n".join(path.read_bytes() for path in root.rglob("*") if path.is_file())


def test_valid_legacy_event_replay_and_list_contracts_are_unchanged(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorder = RunRecorder(store=store)
    event = {"type": "prompt", "content": "hello", "sequence": 3}

    assert recorder.record("legacy-safe", event) is None

    assert recorder.replay("legacy-safe") == [event]
    assert recorder.list_runs() == ["legacy-safe"]
    metadata = recorder.capture_metadata("legacy-safe")
    assert metadata == [
        CaptureMetadata(
            sequence=0,
            state="complete",
            redaction_count=0,
            redaction_kinds=(),
            truncation_count=0,
            truncation_kinds=(),
            stored_bytes=len(store.read_text("runs/legacy-safe/events/0.json").encode()),
        )
    ]


def test_secrets_urls_headers_and_hidden_reasoning_never_reach_disk(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorder = RunRecorder(store=store)

    recorder.record(
        "secret-run",
        {
            "headers": {"Authorization": "Bearer header-secret", "X-ID": "safe-id"},
            "endpoint": "https://alice:password@example.test/v1?token=url-secret&q=safe",
            "reasoning_content": "private-chain-of-thought",
            "safe": "visible",
        },
    )

    persisted = _persisted_bytes(Path(store.root_path))
    for secret in (
        b"header-secret",
        b"alice",
        b"password",
        b"url-secret",
        b"private-chain-of-thought",
    ):
        assert secret not in persisted
    replayed = recorder.replay("secret-run")[0]
    assert replayed["headers"]["Authorization"] == REDACTED_VALUE
    assert replayed["reasoning_content"] == REDACTED_VALUE
    metadata = recorder.capture_metadata("secret-run")[0]
    assert metadata.state == "redacted"
    assert set(metadata.redaction_kinds) == {
        "credential_text",
        "credential_url",
        "hidden_reasoning",
        "secret_key",
    }


def test_shape_bounds_are_explicit_in_capture_metadata(tmp_path: Path) -> None:
    recorder = RunRecorder(
        store=_store(tmp_path),
        redaction_limits=RedactionLimits(
            max_depth=1,
            max_items=3,
            max_string_chars=4,
            max_total_bytes=512,
        ),
    )

    recorder.record(
        "bounded-run",
        {
            "message": "abcdefgh",
            "nested": {"deep": {"value": 1}, "extra": 2},
            "tail": 3,
        },
    )

    metadata = recorder.capture_metadata("bounded-run")[0]
    assert metadata.state == "truncated"
    assert set(metadata.truncation_kinds) == {"depth", "items", "string"}
    assert metadata.truncation_count >= 3
    assert recorder.replay("bounded-run")[0]["message"] == "abcd"


def test_total_byte_limit_uses_empty_legacy_event_and_explicit_metadata(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorder = RunRecorder(
        store=store,
        redaction_limits=RedactionLimits(max_string_chars=1_000, max_total_bytes=64),
    )

    recorder.record("byte-run", {"payload": "x" * 200})

    assert recorder.replay("byte-run") == [{}]
    metadata = recorder.capture_metadata("byte-run")[0]
    assert metadata.state == "truncated"
    assert metadata.truncation_kinds == ("total_bytes",)
    assert metadata.stored_bytes == 2
    assert len(store.read_text("runs/byte-run/events/0.json").encode()) <= 64


def test_unsupported_and_cyclic_values_have_typed_content_free_metadata(tmp_path: Path) -> None:
    @dataclass
    class Dangerous:
        secret: str

        def __repr__(self) -> str:
            return f"Dangerous({self.secret})"

    cycle: list[object] = []
    cycle.append(cycle)
    store = _store(tmp_path)
    recorder = RunRecorder(store=store)

    recorder.record(
        "typed-run",
        {"unsupported": Dangerous("repr-secret"), "cycle": cycle},
    )

    replayed = recorder.replay("typed-run")[0]
    assert replayed == {
        "cycle": ["[TRUNCATED:cycle]"],
        "unsupported": "[REDACTED:unsupported]",
    }
    metadata = recorder.capture_metadata("typed-run")[0]
    assert isinstance(metadata, CaptureMetadata)
    assert metadata.state == "redacted_truncated"
    assert metadata.redaction_kinds == ("unsupported_type",)
    assert metadata.truncation_kinds == ("cycle",)
    assert b"repr-secret" not in _persisted_bytes(Path(store.root_path))


class _FailEventPublishStore(FileStore):
    def move(self, src: str, dst: str) -> None:
        if dst.endswith("/events/0.json"):
            raise OSError("publish-failure-secret")
        super().move(src, dst)


def test_failed_atomic_publish_leaves_no_raw_or_partial_event(tmp_path: Path) -> None:
    store = _FailEventPublishStore(root_path=str(tmp_path / "failed-replays"))
    recorder = RunRecorder(store=store)

    with pytest.raises(OSError, match="publish-failure-secret"):
        recorder.record("failed-run", {"password": "disk-secret"})

    persisted = _persisted_bytes(Path(store.root_path))
    assert b"disk-secret" not in persisted
    assert b"publish-failure-secret" not in persisted
    assert recorder.replay("failed-run") == []
    assert recorder.capture_metadata("failed-run") == []
    assert not any(path.name.endswith(".tmp") for path in Path(store.root_path).rglob("*"))


def test_atomic_success_leaves_only_numeric_event_and_metadata_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    recorder = RunRecorder(store=store)

    recorder.record("atomic-run", {"ok": True})

    files = sorted(
        path.relative_to(Path(store.root_path)).as_posix()
        for path in Path(store.root_path).rglob("*")
        if path.is_file()
    )
    assert files == [
        "runs/atomic-run/capture/0.json",
        "runs/atomic-run/events/0.json",
    ]


def test_replay_ignores_interrupted_temporary_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_text("runs/legacy/events/0.json", '{"type":"safe"}')
    store.write_text("runs/legacy/events/0.json.interrupted.tmp", "not-json")
    recorder = RunRecorder(store=store)

    assert recorder.replay("legacy") == [{"type": "safe"}]


def test_replay_skips_non_object_legacy_entries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.write_text("runs/legacy/events/0.json", '"interrupted"')
    store.write_text("runs/legacy/events/1.json", '{"type":"safe"}')

    assert RunRecorder(store=store).replay("legacy") == [{"type": "safe"}]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("[]", "capture metadata root is invalid"),
        ('{"schema":"unknown"}', "capture metadata schema is invalid"),
        (
            '{"redaction":{},"schema":"gludd.legacy-capture/v1",'
            '"sequence":"zero","state":"complete"}',
            "capture metadata shape is invalid",
        ),
        (
            '{"redaction":{},"schema":"gludd.legacy-capture/v1",'
            '"sequence":0,"state":"complete"}',
            "capture redaction metadata is invalid",
        ),
    ],
)
def test_capture_metadata_rejects_invalid_content_free_sidecars(
    tmp_path: Path,
    payload: str,
    message: str,
) -> None:
    store = _store(tmp_path)
    store.write_text("runs/corrupt/capture/0.json", payload)

    with pytest.raises(ValueError, match=message):
        RunRecorder(store=store).capture_metadata("corrupt")
