"""Concurrent native retention is idempotent without hiding genuine I/O failures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import stream_command
else:
    from scripts import stream_command


@pytest.mark.parametrize("operation", ["read", "unlink"])
@pytest.mark.parametrize("missing", [True, False], ids=["concurrent-removal", "permission-failure"])
def test_native_retention_distinguishes_concurrent_removal_from_io_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, missing: bool,
) -> None:
    directory = tmp_path / "observed" / "retention"
    directory.mkdir(parents=True)
    records = {}
    for index, state in enumerate(("passed", "passed", "running"), start=1):
        run_id = f"owned-{index}"
        record = directory / f"{run_id}.json"
        record.write_text(json.dumps({
            "schema_version": 1, "kind": "observed_command", "label": directory.name,
            "run_id": run_id, "state": state,
            "started_at": f"2026-10-13T00:00:0{index}+00:00",
        }), encoding="utf-8")
        records[index] = record
    current = directory / "current.json"
    current.write_text(records[2].read_text(encoding="utf-8"), encoding="utf-8")
    protected = {path: path.read_bytes() for path in (records[2], records[3], current)}
    original_read = Path.read_text
    original_unlink = Path.unlink

    def raced_read(path: Path, encoding: str | None = None, errors: str | None = None) -> str:
        if path == records[1]:
            if not missing:
                raise PermissionError("native retention volume unavailable")
            original_unlink(path, missing_ok=True)
        return original_read(path, encoding=encoding, errors=errors)

    def raced_unlink(path: Path, missing_ok: bool = False) -> None:
        if path == records[1]:
            if not missing:
                raise PermissionError("native retention volume unavailable")
            original_unlink(path, missing_ok=True)
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "read_text" if operation == "read" else "unlink",
                        raced_read if operation == "read" else raced_unlink)
    if missing:
        stream_command._prune_terminal_runs(directory, retain_runs=1)
        assert not records[1].exists()
    else:
        with pytest.raises(PermissionError, match="native retention volume unavailable"):
            stream_command._prune_terminal_runs(directory, retain_runs=1)
    for path, payload in protected.items():
        assert path.read_bytes() == payload
