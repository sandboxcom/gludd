"""Tests for the AtomicSafeWriter change-export recorder hook.

The recorder is a FAIL-SOFT observability hook: on a successful, validated
write it is called with ``(resolved_path, prior_bytes_or_None, new_content)``.
It must NEVER be able to break or roll back the write itself.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from general_ludd.self_update.safe_writer import AtomicSafeWriter


def test_fresh_create_records_none_old_content(tmp_path: Path) -> None:
    """A brand-new file records ``None`` as the prior content."""
    calls: list[tuple[str, bytes | None, str]] = []
    writer = AtomicSafeWriter(
        workspace_root=tmp_path,
        recorder=lambda p, old, new: calls.append((p, old, new)),
    )

    result = writer.write("new.txt", "hello world")

    assert (tmp_path / "new.txt").read_text() == "hello world"
    assert len(calls) == 1
    path, old, new = calls[0]
    assert path == result
    assert old is None
    assert new == "hello world"


def test_overwrite_records_prior_bytes(tmp_path: Path) -> None:
    """Overwriting an existing file records the exact prior bytes."""
    target = tmp_path / "cfg.txt"
    target.write_bytes(b"original bytes")

    calls: list[tuple[str, bytes | None, str]] = []
    writer = AtomicSafeWriter(
        workspace_root=tmp_path,
        recorder=lambda p, old, new: calls.append((p, old, new)),
    )

    writer.write("cfg.txt", "brand new content")

    assert target.read_text() == "brand new content"
    assert len(calls) == 1
    _, old, new = calls[0]
    assert old == b"original bytes"
    assert new == "brand new content"


def test_recorder_exception_does_not_break_or_roll_back_write(
    tmp_path: Path,
) -> None:
    """A recorder that raises must NOT fail the write or roll it back."""

    def boom(_p: str, _old: bytes | None, _new: str) -> None:
        raise RuntimeError("recorder blew up")

    writer = AtomicSafeWriter(workspace_root=tmp_path, recorder=boom)

    # No exception should escape, and the file must be materialised intact.
    result = writer.write("safe.txt", "durable content")

    assert result == str((tmp_path / "safe.txt").resolve())
    assert (tmp_path / "safe.txt").read_text() == "durable content"


def test_recorder_not_called_when_validation_rejects(tmp_path: Path) -> None:
    """When a post-write validation rejects, the write rolls back and the
    recorder is NEVER called (there is no successful change to record)."""
    target = tmp_path / "guarded.txt"
    target.write_bytes(b"prior good")

    calls: list[tuple[str, bytes | None, str]] = []
    writer = AtomicSafeWriter(
        workspace_root=tmp_path,
        recorder=lambda p, old, new: calls.append((p, old, new)),
    )

    with pytest.raises(RuntimeError):
        writer.write("guarded.txt", "rejected content", validate=lambda _p: False)

    # Rolled back to prior bytes, and nothing recorded.
    assert target.read_bytes() == b"prior good"
    assert calls == []


def test_no_recorder_is_unchanged_behaviour(tmp_path: Path) -> None:
    """With no recorder (the default), write behaves exactly as before."""
    writer = AtomicSafeWriter(workspace_root=tmp_path)

    result = writer.write("plain.txt", "no hook here")

    assert result == str((tmp_path / "plain.txt").resolve())
    assert (tmp_path / "plain.txt").read_text() == "no hook here"
