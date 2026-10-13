"""Bound historical successful pointers without discarding failure or live owners."""

from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from tests.unit.test_item_progress import (
    _fixed_identity,
    _start,
    _state,
)
from tests.unit.test_item_progress import policy as policy
from tests.unit.test_item_progress import repository as repository


def _fill(policy: ModuleType, root: Path, state: str = "passed", *, passed_last: bool = False) -> dict[str, object]:
    for index in range(32):
        _start(policy, root, command=("make", f"proof-{index}"), run_id=f"proof-{index}",
               state="passed" if passed_last and index == 31 else state)
    return _state(root)


def _new_context(policy: ModuleType, monkeypatch: pytest.MonkeyPatch, field: str = "source") -> None:
    previous = policy._identity

    def identity(root: Path, command: tuple[str, ...], environment: dict[str, str],
                 quiet_seconds: float, max_seconds: float) -> dict[str, object]:
        result = cast(dict[str, object], previous(root, command, environment, quiet_seconds, max_seconds))
        return {**result, field: "c" * 64}

    monkeypatch.setattr(policy, "_identity", identity)


def _native_files(root: Path, state: dict[str, object]) -> dict[Path, bytes]:
    pointers = cast(dict[str, dict[str, str]], state["observations"])
    return {
        Path(pointer["root"]) / pointer["label"] / f"{pointer['run_id']}.json":
        (Path(pointer["root"]) / pointer["label"] / f"{pointer['run_id']}.json").read_bytes()
        for pointer in pointers.values()
    }


@pytest.mark.parametrize("changed", ["source", "environment"])
def test_full_history_retires_only_verified_success_pointers_and_keeps_receipts(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    changed: str,
) -> None:
    _fixed_identity(policy, monkeypatch)
    before = _fill(policy, repository)
    files = _native_files(repository, before)
    assert policy._MAX_COMMANDS == 32
    _new_context(policy, monkeypatch, changed)
    _start(policy, repository, command=("make", "new-proof"), run_id="new-proof", state="passed")
    after = _state(repository)
    assert len(cast(dict[str, object], after["observations"])) == 32
    assert after["reservations"] == {}
    assert after["release_ready"] is False and after["gate_evidence"] is False
    assert (repository / "TASKS.md").read_text().count("- [ ]") == 4
    assert all(path.read_bytes() == content for path, content in files.items())
    assert "ITEM_PROGRESS_RETENTION_RELEASED" in capsys.readouterr().out


@pytest.mark.parametrize("unsafe", [
    "failed", "timed_out", "interrupted", "running", "starting", "missing",
    "malformed", "wrong-command", "wrong-root", "false-success", "release-authority",
])
def test_capacity_refuses_to_retire_failed_live_unknown_or_forged_observations(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str,
) -> None:
    _fixed_identity(policy, monkeypatch)
    before = _fill(policy, repository, state="failed")
    pointer = next(iter(cast(dict[str, dict[str, str]], before["observations"]).values()))
    path = Path(pointer["root"]) / pointer["label"] / f"{pointer['run_id']}.json"
    native = json.loads(path.read_text())
    if unsafe == "missing":
        path.unlink()
    elif unsafe == "malformed":
        path.write_text("not-json")
    else:
        native["state"] = (
            unsafe if unsafe in {"failed", "timed_out", "interrupted", "running", "starting"} else "passed"
        )
        native["exit_code"] = (
            None if unsafe in {"starting", "running"}
            else 124 if unsafe == "timed_out"
            else 1 if unsafe in {"failed", "interrupted", "false-success"}
            else 0
        )
        if unsafe == "wrong-command":
            native["progress_identity"]["command"] = ["make", "forged-proof"]
        elif unsafe == "wrong-root":
            native["progress_identity"]["root"] = "/foreign-project"
        elif unsafe == "release-authority":
            native["release_ready"] = True
        path.write_text(json.dumps(native))
    before_bytes = (repository / ".gludd/item-progress.json").read_bytes()
    _new_context(policy, monkeypatch)
    with pytest.raises(ValueError, match="bounded command inventory"):
        _start(policy, repository, command=("make", "new-proof"), run_id="new-proof")
    assert (repository / ".gludd/item-progress.json").read_bytes() == before_bytes


def test_verified_success_retirement_keeps_unchanged_failure_and_owned_reservation(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    _fill(policy, repository, state="failed", passed_last=True)
    _new_context(policy, monkeypatch)
    _start(policy, repository, command=("make", "proof-0"), run_id="current-failure", state="failed")
    with policy.command_admission(repository, ("make", "new-proof"), primary_task_id="F12.1",
                                  environment={}, quiet_seconds=30, max_seconds=120):
        state = _state(repository)
        assert len(cast(dict[str, object], state["observations"])) == 31
        assert len(cast(dict[str, object], state["reservations"])) == 1
        with pytest.raises(ValueError, match="already reserved"):
            _start(policy, repository, command=("make", "new-proof"))
        with pytest.raises(ValueError, match="unchanged failed"):
            _start(policy, repository, command=("make", "proof-0"))
    assert _state(repository)["reservations"] == {}


def test_success_from_current_context_cannot_release_the_inventory_bound(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    _fill(policy, repository)
    before = (repository / ".gludd/item-progress.json").read_bytes()
    with pytest.raises(ValueError, match="bounded command inventory"):
        _start(policy, repository, command=("make", "new-proof"))
    assert (repository / ".gludd/item-progress.json").read_bytes() == before


def test_a_reserved_success_pointer_is_not_retired(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    _fill(policy, repository, state="failed", passed_last=True)
    with policy.command_admission(repository, ("make", "proof-31"), primary_task_id="F12.1",
                                  environment={}, quiet_seconds=30, max_seconds=120):
        before = (repository / ".gludd/item-progress.json").read_bytes()
        _new_context(policy, monkeypatch)
        with pytest.raises(ValueError, match="bounded command inventory"):
            _start(policy, repository, command=("make", "new-proof"))
        assert (repository / ".gludd/item-progress.json").read_bytes() == before


def test_retirement_admission_write_failure_preserves_history_and_receipts(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _fixed_identity(policy, monkeypatch)
    before = _fill(policy, repository)
    files = _native_files(repository, before)
    progress_bytes = (repository / ".gludd/item-progress.json").read_bytes()
    _new_context(policy, monkeypatch)

    def fail_write(_path: Path, _value: object) -> None:
        raise OSError("fixture write refused")

    monkeypatch.setattr(policy, "_atomic_write_json", fail_write)
    with pytest.raises(OSError, match="write refused"):
        _start(policy, repository, command=("make", "new-proof"))
    assert (repository / ".gludd/item-progress.json").read_bytes() == progress_bytes
    assert all(path.read_bytes() == content for path, content in files.items())
    assert "ITEM_PROGRESS_RETENTION_RELEASED" not in capsys.readouterr().out
