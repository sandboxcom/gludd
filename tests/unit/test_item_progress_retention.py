"""Bound historical successful pointers without discarding failure or live owners."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, cast

import pytest

from tests.unit.test_item_progress import (
    _fixed_identity,
    _git,
    _start,
    _state,
)

if TYPE_CHECKING:
    import stream_command
else:
    from scripts import stream_command

pytest_plugins = ["tests.unit.test_item_progress"]


def _fill(policy: ModuleType, root: Path, state: str = "passed", *, passed_last: bool = False) -> dict[str, object]:
    for index in range(32):
        _start(
            policy,
            root,
            command=("make", f"proof-{index}"),
            run_id=f"proof-{index}",
            state="passed" if passed_last and index == 31 else state,
        )
    return _state(root)


def _new_context(policy: ModuleType, monkeypatch: pytest.MonkeyPatch, field: str = "source") -> None:
    previous = policy._identity

    def identity(
        root: Path, command: tuple[str, ...], environment: dict[str, str], quiet_seconds: float, max_seconds: float
    ) -> dict[str, object]:
        result = cast(dict[str, object], previous(root, command, environment, quiet_seconds, max_seconds))
        return {**result, field: "c" * 64}

    monkeypatch.setattr(policy, "_identity", identity)


def _native_files(root: Path, state: dict[str, object]) -> dict[Path, bytes]:
    pointers = cast(dict[str, dict[str, str]], state["observations"])
    return {
        Path(pointer["root"]) / pointer["label"] / f"{pointer['run_id']}.json": (
            Path(pointer["root"]) / pointer["label"] / f"{pointer['run_id']}.json"
        ).read_bytes()
        for pointer in pointers.values()
    }


@pytest.mark.parametrize("changed", ["source", "environment"])
def test_full_history_retires_only_verified_success_pointers_and_keeps_receipts(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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


@pytest.mark.parametrize(
    "unsafe",
    [
        "failed",
        "timed_out",
        "interrupted",
        "running",
        "starting",
        "missing",
        "malformed",
        "wrong-command",
        "wrong-root",
        "false-success",
        "release-authority",
    ],
)
def test_capacity_refuses_to_retire_failed_live_unknown_or_forged_observations(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
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
            None
            if unsafe in {"starting", "running"}
            else 124
            if unsafe == "timed_out"
            else 1
            if unsafe in {"failed", "interrupted", "false-success"}
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
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    _fill(policy, repository, state="failed", passed_last=True)
    _new_context(policy, monkeypatch)
    _start(policy, repository, command=("make", "proof-0"), run_id="current-failure", state="failed")
    with policy.command_admission(
        repository, ("make", "new-proof"), primary_task_id="F12.1", environment={}, quiet_seconds=30, max_seconds=120
    ):
        state = _state(repository)
        assert len(cast(dict[str, object], state["observations"])) == 31
        assert len(cast(dict[str, object], state["reservations"])) == 1
        with pytest.raises(ValueError, match="already reserved"):
            _start(policy, repository, command=("make", "new-proof"))
        with pytest.raises(ValueError, match="unchanged failed"):
            _start(policy, repository, command=("make", "proof-0"))
    assert _state(repository)["reservations"] == {}


def test_success_from_current_context_cannot_release_the_inventory_bound(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    _fill(policy, repository)
    before = (repository / ".gludd/item-progress.json").read_bytes()
    with pytest.raises(ValueError, match="bounded command inventory"):
        _start(policy, repository, command=("make", "new-proof"))
    assert (repository / ".gludd/item-progress.json").read_bytes() == before


def test_a_reserved_success_pointer_is_not_retired(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    _fill(policy, repository, state="failed", passed_last=True)
    with policy.command_admission(
        repository, ("make", "proof-31"), primary_task_id="F12.1", environment={}, quiet_seconds=30, max_seconds=120
    ):
        before = (repository / ".gludd/item-progress.json").read_bytes()
        _new_context(policy, monkeypatch)
        with pytest.raises(ValueError, match="bounded command inventory"):
            _start(policy, repository, command=("make", "new-proof"))
        assert (repository / ".gludd/item-progress.json").read_bytes() == before


def test_retirement_admission_write_failure_preserves_history_and_receipts(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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


def _historical_failures(
    policy: ModuleType,
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    *,
    count: int = 32,
) -> dict[str, object]:
    """Keep real source hashing while isolating runtime and ended-owner checks."""

    def environment_identity(environment: Mapping[str, str], cwd: Path | str | None = None) -> str:
        return str(policy._command_key(dict(environment)))

    monkeypatch.setattr(policy, "_environment_identity", environment_identity)
    for index in range(count):
        _start(policy, root, command=("make", f"proof-{index}"), run_id=f"proof-{index}", state=state)
    before = _state(root)
    native_owner = os.getpid()
    original_alive = policy._pid_alive
    monkeypatch.setattr(policy, "_pid_alive", lambda owner: owner != native_owner and original_alive(owner))
    return before


@pytest.mark.parametrize("state", ["failed", "timed_out"])
def test_actual_source_change_releases_one_historical_failed_slot(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    state: str,
) -> None:
    before = _historical_failures(policy, repository, monkeypatch, state)
    files = _native_files(repository, before)
    (repository / "implementation.py").write_text("result = True\n", encoding="utf-8")
    _start(policy, repository, command=("make", "new-acquisition"), run_id="acquisition", state="passed")
    after = _state(repository)
    old_keys = set(cast(dict[str, object], before["observations"]))
    new_keys = set(cast(dict[str, object], after["observations"]))
    assert len(old_keys - new_keys) == 1 and len(new_keys - old_keys) == 1
    assert len(new_keys) == policy._MAX_COMMANDS == 32
    assert after["reservations"] == {}
    assert after["release_ready"] is False and after["gate_evidence"] is False
    assert (repository / "TASKS.md").read_text().count("- [ ]") == 4
    assert all(path.read_bytes() == content for path, content in files.items())
    assert "reason=superseded-native-failure-source" in capsys.readouterr().out


@pytest.mark.parametrize("overlap", [False, True])
@pytest.mark.parametrize("state", ["failed", "timed_out"])
def test_full_bound_dead_incoming_reservation_replacement_does_not_retire_history(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    overlap: bool,
    state: str,
) -> None:
    count = 32 if overlap else 31
    before = _historical_failures(policy, repository, monkeypatch, state, count=count)
    observations = cast(dict[str, dict[str, str]], before["observations"])
    files = _native_files(repository, before)
    for receipt in list(files):
        native = json.loads(files[receipt])
        log, trace = Path(native["log_path"]), receipt.with_suffix(".pytest.jsonl")
        native["trace_path"] = str(trace)
        receipt.write_text(json.dumps(native), encoding="utf-8")
        for artifact in (log, trace):
            artifact.write_bytes(b"retained native failure evidence\n")
            files[artifact] = artifact.read_bytes()
        files[receipt] = receipt.read_bytes()
    command = ("make", "proof-0" if overlap else "incoming-acquisition")
    implementation = repository / "implementation.py"
    if overlap:
        implementation.write_text("result = True\n", encoding="utf-8")
    code = (
        "import os, sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "from scripts.item_progress import command_admission\n"
        f"owner = command_admission(Path({str(repository)!r}), {command!r}, "
        "primary_task_id='F12.1', environment={'PYTHONHASHSEED': '0'}, "
        "quiet_seconds=30, max_seconds=120)\n"
        "owner.__enter__()\nos._exit(0)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    abandoned = _state(repository)
    incoming_key = str(policy._command_key(list(command)))
    reservations = cast(dict[str, dict[str, object]], abandoned["reservations"])
    previous = reservations[incoming_key]
    assert not policy._pid_alive(previous["owner_pid"])
    assert abandoned["observations"] == observations
    assert len(set(observations) | set(reservations)) == policy._MAX_COMMANDS == 32
    implementation.write_text("result = 'source-repair'\n", encoding="utf-8")
    capsys.readouterr()
    with policy.command_admission(
        repository,
        command,
        primary_task_id="F12.1",
        environment={"PYTHONHASHSEED": "0"},
        quiet_seconds=30,
        max_seconds=120,
    ):
        during = _state(repository)
        current_observations = cast(dict[str, object], during["observations"])
        current_reservations = cast(dict[str, dict[str, object]], during["reservations"])
        assert current_observations == observations and len(current_observations) == count
        assert len(set(current_observations) | set(current_reservations)) == 32
        assert current_reservations[incoming_key]["token"] != previous["token"]
        assert current_reservations[incoming_key]["owner_pid"] == os.getpid()
        assert during["release_ready"] is False and during["gate_evidence"] is False
        assert all(path.read_bytes() == content for path, content in files.items())
    assert _state(repository)["observations"] == observations
    assert _state(repository)["reservations"] == {}
    assert all(path.read_bytes() == content for path, content in files.items())
    assert "ITEM_PROGRESS_RETENTION_RELEASED" not in capsys.readouterr().out


@pytest.mark.parametrize("route", ["scoped", "exempt"])
@pytest.mark.parametrize("state", ["failed", "timed_out"])
def test_retired_failure_survives_real_retention_and_refuses_run_identity_reuse(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: str,
    state: str,
) -> None:
    before = _historical_failures(policy, repository, monkeypatch, state)
    pointers = cast(dict[str, dict[str, str]], before["observations"])
    artifacts: dict[Path, bytes] = {}
    for pointer in pointers.values():
        directory = Path(pointer["root"]) / pointer["label"]
        receipt = directory / f"{pointer['run_id']}.json"
        native = json.loads(receipt.read_text())
        trace = directory / f"{pointer['run_id']}.pytest.jsonl"
        native["trace_path"] = str(trace)
        receipt.write_text(json.dumps(native), encoding="utf-8")
        Path(native["log_path"]).write_bytes(b"original failed output\n")
        trace.write_bytes(b"original failed trace\n")
        for path in (receipt, Path(native["log_path"]), trace):
            artifacts[path] = path.read_bytes()
    (repository / "implementation.py").write_text("result = True\n", encoding="utf-8")
    _start(policy, repository, command=("make", "new-acquisition"), run_id="acquisition", state="passed")
    retired = set(pointers) - set(cast(dict[str, object], _state(repository)["observations"]))
    assert len(retired) == 1
    pointer = pointers[retired.pop()]
    directory = Path(pointer["root"]) / pointer["label"]
    command = [sys.executable, "-c", "print('retention probe')"]
    environment = dict(os.environ)
    if route == "scoped":
        with policy.command_admission(
            repository,
            command,
            primary_task_id="F12.1",
            environment=environment,
            quiet_seconds=0,
            max_seconds=10,
        ) as admission:
            result = stream_command.stream_command(
                command,
                directory / "probe.log",
                observed_root=directory.parent,
                label=pointer["label"],
                run_id="probe",
                retain_runs=1,
                max_seconds=10,
                progress_admission=admission,
                environment=environment,
            )
    else:
        result = stream_command.stream_command(
            command,
            directory / "probe.log",
            observed_root=directory.parent,
            label=pointer["label"],
            run_id="probe",
            retain_runs=1,
            max_seconds=10,
            progress_root=repository,
            environment=environment,
        )
    assert result == 0
    assert all(path.exists() and path.read_bytes() == content for path, content in artifacts.items())
    marker = repository / "reuse-executed"
    result = stream_command.stream_command(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
        directory / "reuse-attempt.log",
        observed_root=directory.parent,
        label=pointer["label"],
        run_id=pointer["run_id"],
        retain_runs=1,
        max_seconds=10,
        progress_root=repository,
        environment=environment,
    )
    assert result == 125 and not marker.exists()
    assert all(path.read_bytes() == content for path, content in artifacts.items())


@pytest.mark.parametrize("change", ["none", "environment", "bookkeeping", "empty-commit"])
def test_failed_slots_require_actual_source_change(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    _historical_failures(policy, repository, monkeypatch, "failed")
    environment = {"PYTHONHASHSEED": "0"}
    if change == "environment":
        environment["PYTHONHASHSEED"] = "1"
    elif change == "bookkeeping":
        for name in ("TASKS.md", "BUGS.md", "SESSION.md"):
            path = repository / name
            path.write_text(path.read_text() + "\nUpdated notes\n", encoding="utf-8")
    elif change == "empty-commit":
        _git(repository, "commit", "--quiet", "--allow-empty", "-m", "unchanged source")
    before = (repository / ".gludd/item-progress.json").read_bytes()
    with pytest.raises(ValueError, match="bounded command inventory"):
        _start(policy, repository, command=("make", "new-acquisition"), environment=environment)
    assert (repository / ".gludd/item-progress.json").read_bytes() == before


def test_retiring_old_source_failure_preserves_current_source_refusal_and_reservation(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _historical_failures(policy, repository, monkeypatch, "failed")
    (repository / "implementation.py").write_text("result = True\n", encoding="utf-8")
    _start(policy, repository, command=("make", "proof-0"), run_id="current-failure")
    with policy.command_admission(
        repository,
        ("make", "new-acquisition"),
        primary_task_id="F12.1",
        environment={"PYTHONHASHSEED": "0"},
        quiet_seconds=30,
        max_seconds=120,
    ):
        during = _state(repository)
        assert len(cast(dict[str, object], during["observations"])) == 31
        assert len(cast(dict[str, object], during["reservations"])) == 1
        with pytest.raises(ValueError, match="unchanged failed"):
            _start(policy, repository, command=("make", "proof-0"), run_id="retry", max_seconds=240)
    assert _state(repository)["reservations"] == {}


def test_failed_slot_retirement_write_failure_keeps_every_native_record(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    before = _historical_failures(policy, repository, monkeypatch, "failed")
    files = _native_files(repository, before)
    progress = repository / ".gludd/item-progress.json"
    original = progress.read_bytes()
    (repository / "implementation.py").write_text("result = True\n", encoding="utf-8")

    def fail_write(_path: Path, _value: object) -> None:
        raise OSError("fixture native admission write refused")

    monkeypatch.setattr(policy, "_atomic_write_json", fail_write)
    with pytest.raises(OSError, match="native admission write refused"):
        _start(policy, repository, command=("make", "new-acquisition"))
    assert progress.read_bytes() == original
    assert all(path.read_bytes() == content for path, content in files.items())
    assert "ITEM_PROGRESS_RETENTION_RELEASED" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "unsafe",
    [
        "current-source",
        "environment-only",
        "live-owner",
        "unknown-owner",
        "boolean-owner",
        "zero-owner",
        "reserved",
        "wrong-command",
        "wrong-root",
        "invalid-source",
        "missing",
        "malformed",
        "symlink",
        "invalid-exit",
        "interrupted",
        "running",
        "starting",
    ],
)
def test_one_source_obsolete_failure_cannot_bypass_native_safety_boundaries(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    _fixed_identity(policy, monkeypatch)
    before = _fill(policy, repository, state="failed")
    observations = cast(dict[str, dict[str, str]], before["observations"])
    key, pointer = next(iter(observations.items()))
    path = Path(pointer["root"]) / pointer["label"] / f"{pointer['run_id']}.json"
    native = json.loads(path.read_text())
    native["progress_identity"]["source"] = "c" * 64
    monkeypatch.setattr(policy, "_pid_alive", lambda _owner: unsafe == "live-owner")
    if unsafe in {"current-source", "environment-only"}:
        native["progress_identity"]["source"] = "a" * 64
        if unsafe == "environment-only":
            native["progress_identity"]["environment"] = "c" * 64
    elif unsafe in {"unknown-owner", "boolean-owner", "zero-owner"}:
        native["owner_pid"] = {"unknown-owner": None, "boolean-owner": True, "zero-owner": 0}[unsafe]
    elif unsafe == "reserved":
        before["reservations"] = {key: {"owner_pid": os.getpid(), "token": "f" * 32}}
        (repository / ".gludd/item-progress.json").write_text(json.dumps(before), encoding="utf-8")
    elif unsafe == "wrong-command":
        native["progress_identity"]["command"] = ["make", "forged-command"]
    elif unsafe == "wrong-root":
        native["progress_identity"]["root"] = "/foreign-project"
    elif unsafe == "invalid-source":
        native["progress_identity"]["source"] = "manual"
    elif unsafe == "invalid-exit":
        native["exit_code"] = True
    elif unsafe in {"interrupted", "running", "starting"}:
        native["state"] = unsafe
        native["exit_code"] = 130 if unsafe == "interrupted" else None
    path.write_text(json.dumps(native), encoding="utf-8")
    if unsafe == "missing":
        path.unlink()
    elif unsafe == "malformed":
        path.write_text("not-json", encoding="utf-8")
    elif unsafe == "symlink":
        original = path.with_suffix(".retained")
        path.rename(original)
        path.symlink_to(original)
    progress = repository / ".gludd/item-progress.json"
    progress_before = progress.read_bytes()
    with pytest.raises(ValueError, match="bounded command inventory"):
        _start(policy, repository, command=("make", "new-acquisition"))
    assert progress.read_bytes() == progress_before


def test_failed_slot_retirement_keeps_latest_context_not_all_historical_source_deduplication(
    policy: ModuleType,
    repository: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _historical_failures(policy, repository, monkeypatch, "failed")
    pointers = cast(dict[str, dict[str, str]], before["observations"])
    original_files = _native_files(repository, before)
    implementation = repository / "implementation.py"
    implementation.write_text("result = True\n", encoding="utf-8")
    _start(policy, repository, command=("make", "new-acquisition"), run_id="source-b", state="passed")
    retired = set(pointers) - set(cast(dict[str, object], _state(repository)["observations"]))
    assert len(retired) == 1
    pointer = pointers[retired.pop()]
    previous = json.loads((Path(pointer["root"]) / pointer["label"] / f"{pointer['run_id']}.json").read_text())
    command = tuple(previous["progress_identity"]["command"])
    implementation.write_text("result = False\n", encoding="utf-8")
    _start(policy, repository, command=command, run_id="source-a-return")
    assert all(path.read_bytes() == content for path, content in original_files.items())
    with pytest.raises(ValueError, match="unchanged failed"):
        _start(policy, repository, command=command, run_id="source-a-repeat")
