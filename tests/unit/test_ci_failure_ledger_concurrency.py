"""Native ledger transactions retain concurrent hosted observations and repairs."""

from __future__ import annotations

import importlib.util
import json
import selectors
import signal
import stat
import subprocess
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import ModuleType
from typing import Any

import pytest
from filelock import FileLock, Timeout

_ADJACENT_SCRIPT = Path(__file__).resolve().with_name("ci_failure_ledger.py")
SCRIPT = (
    _ADJACENT_SCRIPT
    if _ADJACENT_SCRIPT.is_file()
    else Path(__file__).resolve().parents[2] / "scripts" / "ci_failure_ledger.py"
)
SHA_FAILED = "a" * 40
SHA_REPAIR = "b" * 40
SHA_OTHER = "c" * 40


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_failure_ledger_concurrent", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(run_id: int, *jobs: str, sha: str = SHA_FAILED) -> dict[str, Any]:
    return {
        "databaseId": run_id,
        "headSha": sha,
        "headBranch": "development",
        "status": "completed",
        "conclusion": "failure",
        "workflowName": "Build and Release",
        "url": f"https://github.invalid/actions/runs/{run_id}",
        "jobs": [
            {"name": job, "status": "completed", "conclusion": "failure", "steps": []}
            for job in jobs
        ],
    }


def _seed(module: ModuleType, path: Path) -> dict[str, str]:
    ledger = module.new_ledger()
    module.observe_payload(ledger, _payload(101, "primary", "sibling"), observed_at="first")
    module.write_ledger(path, ledger)
    return {family["job"]: key for key, family in ledger["families"].items()}


def _repair(path: Path, *family_ids: str, all_open: bool = False) -> list[str]:
    return [
        "repair", "--ledger", str(path), "--sha", SHA_REPAIR,
        "--evidence-target", "test-count",
        *[part for family_id in family_ids for part in ("--family", family_id)],
        *(["--all-open"] if all_open else []),
    ]


def _checkout_query(
    command: list[str], root: Path, sha: str = SHA_REPAIR,
) -> subprocess.CompletedProcess[str] | None:
    if command != ["make", "--no-print-directory", "worktree-state"]:
        return None
    return subprocess.CompletedProcess(command, 0, json.dumps({
        "path": str(root), "head": sha, "is_clean": True, "dirty_count": 0,
    }), "")


def _observe(path: Path, run_id: int) -> list[str]:
    return ["observe", "--ledger", str(path), "--run", str(run_id)]


def _index(run_id: int) -> list[dict[str, Any]]:
    return [{
        "databaseId": run_id, "headSha": SHA_FAILED, "headBranch": "development",
        "event": "push", "workflowName": "Build and Release", "status": "completed",
        "createdAt": "2026-10-13T12:00:00Z",
    }]


def test_repair_retains_concurrent_run_and_other_family_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed(module, path)
    started, resume = Event(), Event()
    calls = 0

    def evidence(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        checkout = _checkout_query(command, module.ROOT)
        if checkout is not None:
            return checkout
        calls += 1
        if calls == 1:
            started.set()
            assert resume.wait(timeout=5), "concurrent native writes must not await evidence"
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", evidence)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: _payload(202, "new"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        primary = executor.submit(module.main, _repair(path, family_ids["primary"]))
        try:
            assert started.wait(timeout=5)
            assert module.main(_observe(path, 202)) == 0
            assert module.main(_repair(path, family_ids["sibling"])) == 0
            concurrent = module.read_ledger(path)
        finally:
            resume.set()
        assert primary.result(timeout=5) == 0

    persisted = module.read_ledger(path)
    assert persisted["runs"] == concurrent["runs"], "a repair must never erase concurrent runs"
    assert persisted["families"][family_ids["sibling"]] == concurrent["families"][family_ids["sibling"]]
    assert persisted["families"][family_ids["primary"]]["status"] == "repaired"
    new_family = next(f for f in concurrent["families"].values() if f["job"] == "new")
    assert persisted["families"][new_family["family_id"]] == new_family


@pytest.mark.parametrize("command_name", ["observe", "observe-sha"])
def test_observer_retains_writer_that_commits_during_github_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command_name: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    _seed(module, path)
    started, resume = Event(), Event()

    def fetch(run_id: int, _repo: str) -> dict[str, Any]:
        if run_id == 303:
            started.set()
            assert resume.wait(timeout=5), "GitHub fetch must not hold the transaction lock"
        return _payload(run_id, f"new-{run_id}")

    monkeypatch.setattr(module, "fetch_run", fetch)
    monkeypatch.setitem(module.observe_exact_sha.__kwdefaults__, "fetcher", fetch)
    monkeypatch.setattr(module, "fetch_run_index", lambda _sha, _repo: _index(303))
    command = (
        _observe(path, 303) if command_name == "observe"
        else ["observe-sha", "--ledger", str(path), "--sha", SHA_FAILED]
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        observer = executor.submit(module.main, command)
        try:
            assert started.wait(timeout=5)
            assert module.main(_observe(path, 202)) == 0
            concurrent = module.read_ledger(path)
        finally:
            resume.set()
        assert observer.result(timeout=5) == 0

    persisted = module.read_ledger(path)
    assert set(persisted["runs"]) == {"101", "202", "303"}
    assert all(persisted["runs"][key] == run for key, run in concurrent["runs"].items())
    assert all(
        persisted["families"][key] == family for key, family in concurrent["families"].items()
    )


def test_all_open_selection_does_not_repair_a_failure_arriving_during_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed(module, path)
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: _payload(202, "new"))

    def evidence(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(command, module.ROOT)
        if checkout is not None:
            return checkout
        assert module.main(_observe(path, 202)) == 0
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", evidence)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)

    assert module.main(_repair(path, all_open=True)) == 0

    persisted = module.read_ledger(path)
    assert set(persisted["runs"]) == {"101", "202"}
    assert all(persisted["families"][key]["status"] == "repaired" for key in family_ids.values())
    arriving = next(f for f in persisted["families"].values() if f["job"] == "new")
    assert arriving["status"] == "open"
    assert "repair" not in arriving


@pytest.mark.parametrize("concurrent_change", ["failure", "success", "repair"])
def test_repair_rejects_changed_selected_family_without_overwriting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, concurrent_change: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed(module, path)
    calls = 0
    concurrent: dict[str, Any] = {}
    secondary_root = tmp_path / "secondary-checkout"
    payload = _payload(202, "primary", sha=SHA_OTHER)
    if concurrent_change == "success":
        payload["conclusion"] = "success"
        payload["jobs"][0]["conclusion"] = "success"
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: payload)

    def evidence(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls, concurrent
        checkout = _checkout_query(
            command, _kwargs["cwd"], SHA_OTHER if _kwargs["cwd"] == secondary_root else SHA_REPAIR,
        )
        if checkout is not None:
            return checkout
        calls += 1
        if calls == 1:
            if concurrent_change == "repair":
                secondary = _repair(path, family_ids["primary"])
                secondary[secondary.index("--sha") + 1] = SHA_OTHER
                secondary_module = _load()
                monkeypatch.setattr(secondary_module, "ROOT", secondary_root)
                monkeypatch.setattr(secondary_module, "_git_is_ancestor", lambda _old, _new: True)
                assert secondary_module.main(secondary) == 0
            else:
                assert module.main(_observe(path, 202)) == 0
            concurrent = module.read_ledger(path)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", evidence)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)

    assert module.main(_repair(path, family_ids["primary"])) == 2
    assert module.read_ledger(path) == concurrent
    assert "changed while repair evidence ran" in capsys.readouterr().err


@pytest.mark.parametrize("command_name", ["observe", "observe-sha", "repair"])
def test_every_native_writer_honors_the_same_private_bounded_transaction_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, command_name: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed(module, path)
    before = path.read_bytes()
    lock_path = path.with_name(f".{path.name}.lock")
    monkeypatch.setattr(module, "LOCK_TIMEOUT_SECONDS", 0, raising=False)
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: _payload(202, "new"))
    monkeypatch.setattr(module, "fetch_run_index", lambda _sha, _repo: _index(202))
    monkeypatch.setitem(module.observe_exact_sha.__kwdefaults__, "fetcher", module.fetch_run)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", lambda command, **_kwargs:
                        _checkout_query(command, module.ROOT) or subprocess.CompletedProcess(command, 0, "", ""))
    commands = {
        "observe": _observe(path, 202),
        "observe-sha": ["observe-sha", "--ledger", str(path), "--sha", SHA_FAILED],
        "repair": _repair(path, family_ids["primary"]),
    }

    with FileLock(str(lock_path), timeout=0, mode=0o600):
        assert module.main(commands[command_name]) == 2
        assert path.read_bytes() == before
        assert "ledger transaction lock" in capsys.readouterr().err

    lock_identity = lock_path.stat().st_ino
    assert module.main(commands[command_name]) == 0
    assert lock_path.stat().st_ino == lock_identity, "lock sidecars must not be unlinked after release"
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    persisted = json.loads(path.read_text(encoding="utf-8"))
    if command_name == "repair":
        assert persisted["families"][family_ids["primary"]]["status"] == "repaired"
    else:
        assert set(persisted["runs"]) == {"101", "202"}


def test_native_writer_rejects_a_symlinked_lock_without_changing_either_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    _seed(module, path)
    before = path.read_bytes()
    target = tmp_path / "unrelated.txt"
    target.write_text("must be preserved", encoding="utf-8")
    path.with_name(f".{path.name}.lock").symlink_to(target)
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: _payload(202, "new"))

    assert module.main(_observe(path, 202)) == 2
    assert "lock must not be a symbolic link" in capsys.readouterr().err
    assert path.read_bytes() == before
    assert target.read_text(encoding="utf-8") == "must be preserved"


@pytest.mark.parametrize("error_kind", ["lookup", "io", "sha", "immutable"])
def test_exact_sha_preparation_retains_valid_workflows_and_reports_each_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, error_kind: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    _seed(module, path)
    if error_kind == "immutable":
        ledger = module.read_ledger(path)
        module.observe_payload(ledger, _payload(202, "original"), observed_at="before")
        module.write_ledger(path, ledger)
    before = module.read_ledger(path)
    index = [dict(_index(run_id)[0], workflowName=workflow) for run_id, workflow in ((202, "A"), (303, "B"))]
    fetched: list[int] = []

    def fetch(run_id: int, _repo: str) -> dict[str, Any]:
        fetched.append(run_id)
        if run_id == 202:
            if error_kind == "lookup":
                raise module.LedgerError("hosted detail unavailable")
            if error_kind == "io":
                raise OSError("hosted detail unavailable")
            return _payload(run_id, "changed", sha=SHA_OTHER if error_kind == "sha" else SHA_FAILED)
        return _payload(run_id, "valid")

    monkeypatch.setattr(module, "fetch_run", fetch)
    monkeypatch.setattr(module, "fetch_run_index", lambda _sha, _repo: index)
    monkeypatch.setitem(module.observe_exact_sha.__kwdefaults__, "fetcher", fetch)

    assert module.main(["observe-sha", "--ledger", str(path), "--sha", SHA_FAILED]) == 2

    persisted = module.read_ledger(path)
    assert fetched == [202, 303]
    assert "303" in persisted["runs"]
    assert all(persisted["runs"][key] == run for key, run in before["runs"].items())
    assert all(persisted["families"][key] == family for key, family in before["families"].items())
    errors = capsys.readouterr().err
    assert "ERRORS count=1" in errors
    assert "run 202:" in errors
    expected = {
        "lookup": "hosted detail unavailable", "io": "hosted detail unavailable",
        "sha": "detail SHA differs", "immutable": "immutable terminal run",
    }
    assert expected[error_kind] in errors


@pytest.mark.parametrize("writer_input_case", ("missing", "existing"))
def test_fresh_writer_requests_public_filelock_before_mutation_or_yield(
    tmp_path: Path, writer_input_case: str,
) -> None:
    """A read-only import boundary must never become an unlocked writer fallback."""
    path = tmp_path / "private" / "ledger.json"
    if writer_input_case == "existing":
        _seed(_load(), path)
    before = path.read_bytes() if path.exists() else None
    parent_existed = path.parent.exists()
    program = f"""
import builtins
import importlib.util
import sys
from pathlib import Path
assert not any(name == "filelock" or name.startswith("filelock.") for name in sys.modules)
attempts = []
native_import = builtins.__import__
def refuse_lock(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "filelock" or name.startswith("filelock."):
        attempts.append(name)
        raise ModuleNotFoundError("writer dependency blocked by test", name=name)
    return native_import(name, globals, locals, fromlist, level)
builtins.__import__ = refuse_lock
spec = importlib.util.spec_from_file_location("ledger_writer_import_probe", {str(SCRIPT)!r})
assert spec is not None and spec.loader is not None
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
print("LEDGER_READER_IMPORTED_WITHOUT_LOCK")
ledger_path = Path({str(path)!r})
yielded = False
try:
    with owner._locked_ledger(ledger_path):
        yielded = True
except ModuleNotFoundError as error:
    assert error.name == "filelock", error
else:
    raise AssertionError("writer bypassed its required public FileLock import")
assert not yielded
assert attempts == ["filelock"], attempts
assert (ledger_path.read_bytes() if ledger_path.exists() else None) == {before!r}
assert ledger_path.parent.exists() is {parent_existed!r}
assert not ledger_path.with_name("." + ledger_path.name + ".lock").exists()
assert not any(name == "filelock" or name.startswith("filelock.") for name in sys.modules)
print("LEDGER_WRITER_IMPORT_REFUSAL_PASS")
"""
    result = subprocess.run(
        [sys.executable, "-W", "error", "-c", program],
        cwd=tmp_path, check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "LEDGER_READER_IMPORTED_WITHOUT_LOCK" in result.stdout
    assert "LEDGER_WRITER_IMPORT_REFUSAL_PASS" in result.stdout
    assert (path.read_bytes() if path.exists() else None) == before
    assert path.parent.exists() is parent_existed
    assert not path.with_name(f".{path.name}.lock").exists()


def test_locked_ledger_keeps_real_timeout_cause_and_reacquires_after_independent_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Independent public locks contend; the live timeout and private sidecar stay authoritative."""
    module = _load()
    path = tmp_path / "private" / "ledger.json"
    _seed(module, path)
    before = path.read_bytes()
    lock_path = path.with_name(f".{path.name}.lock")
    timeout_before = module.LOCK_TIMEOUT_SECONDS
    holder = FileLock(str(lock_path), timeout=0, mode=0o600)
    with monkeypatch.context() as scoped:
        scoped.setattr(module, "LOCK_TIMEOUT_SECONDS", 0)
        with holder:
            lock_inode = lock_path.stat().st_ino
            with (
                pytest.raises(module.LedgerError, match="ledger transaction lock") as captured,
                module._locked_ledger(path),
            ):
                pytest.fail("an independently held writer lock must never yield")
            assert isinstance(captured.value.__cause__, Timeout)
            assert path.read_bytes() == before
            assert lock_path.stat().st_ino == lock_inode
        with module._locked_ledger(path) as ledger:
            assert ledger == json.loads(before)
        assert lock_path.stat().st_ino == lock_inode
        assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert path.read_bytes() == before
    assert timeout_before == module.LOCK_TIMEOUT_SECONDS


def _seed_repaired(module: ModuleType, path: Path) -> dict[str, str]:
    family_ids = _seed(module, path)
    ledger = module.read_ledger(path)
    module.record_repair(
        ledger, list(family_ids.values()), repair_sha=SHA_REPAIR,
        evidence="make original-evidence", recorded_at="previous-success",
        is_ancestor=lambda _old, _new: True,
    )
    module.write_ledger(path, ledger)
    return family_ids


def _push(module: ModuleType, path: Path) -> int:
    return int(module.main(["guard-push", "--ledger", str(path), "--head", SHA_REPAIR]))


@pytest.mark.parametrize("failed_attempt_case", (
    "exit-1", "exit-124", "signal", "io", "timeout", "interrupt", "post-check",
))
def test_admitted_repair_invalidates_prior_authority_before_evidence_and_keeps_it_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_attempt_case: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    before = module.read_ledger(path)
    observed: dict[str, Any] = {}
    checkout_calls = 0

    def evidence(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal checkout_calls
        checkout = _checkout_query(command, module.ROOT)
        if checkout is not None:
            checkout_calls += 1
            if failed_attempt_case == "post-check" and checkout_calls == 2:
                return subprocess.CompletedProcess(command, 1, "", "post-check failed")
            return checkout
        observed["during"] = module.read_ledger(path)
        observed["guard"] = _push(module, path)
        if failed_attempt_case == "io":
            raise OSError("evidence could not start")
        if failed_attempt_case == "timeout":
            raise subprocess.TimeoutExpired(command, 90)
        if failed_attempt_case == "interrupt":
            raise KeyboardInterrupt
        exit_code = {"exit-1": 1, "exit-124": 124, "signal": -15}.get(failed_attempt_case, 0)
        return subprocess.CompletedProcess(command, exit_code, "", "")

    monkeypatch.setattr(module.subprocess, "run", evidence)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    assert _push(module, path) == 0
    command = _repair(path, family_ids["primary"])
    if failed_attempt_case == "timeout":
        with pytest.raises(subprocess.TimeoutExpired):
            module.main(command)
    elif failed_attempt_case == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            module.main(command)
    else:
        assert module.main(command) == 2
    failed = module.read_ledger(path)
    assert observed["guard"] == 1
    assert failed == observed["during"], "unsuccessful evidence cannot publish or restore authority"
    primary = failed["families"][family_ids["primary"]]
    assert primary["status"] == "open" and "repair" not in primary
    attempt = primary["repair_attempts"][-1]
    assert attempt["previous_repair"] == before["families"][family_ids["primary"]]["repair"]
    assert attempt["sha"] == SHA_REPAIR and attempt["evidence"] == "make test-count"
    assert attempt["started_at"] and len(attempt["id"]) == 32
    assert failed["runs"] == before["runs"]
    assert failed["families"][family_ids["sibling"]] == before["families"][family_ids["sibling"]]
    assert _push(module, path) == 1

    monkeypatch.setattr(module.subprocess, "run", lambda argv, **_kwargs:
                        _checkout_query(argv, module.ROOT) or subprocess.CompletedProcess(argv, 0, "", ""))
    assert module.main(command) == 0
    restored = module.read_ledger(path)
    assert _push(module, path) == 0
    repaired = restored["families"][family_ids["primary"]]
    assert repaired["status"] == "repaired" and repaired["repair"]["sha"] == SHA_REPAIR
    assert repaired["repair"]["evidence"] == "make test-count"
    assert repaired["repair_attempts"][:-1] == primary["repair_attempts"]
    assert "previous_repair" not in repaired["repair_attempts"][-1]
    assert len({item["id"] for item in repaired["repair_attempts"]}) == 2


@pytest.mark.parametrize("admission_failure_case", ("changed", "history", "publication"))
def test_multi_family_admission_refuses_without_partial_invalidation_or_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, admission_failure_case: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    evidence_calls: list[list[str]] = []
    expected: dict[str, Any] = {}
    native_lock = module._locked_ledger

    @contextmanager
    def intervening_lock(ledger_path: Path) -> Iterator[dict[str, Any]]:
        current = module.read_ledger(ledger_path)
        if admission_failure_case == "changed":
            module.observe_payload(current, _payload(202, "sibling", sha=SHA_OTHER), observed_at="concurrent")
        elif admission_failure_case == "history":
            current["families"][family_ids["sibling"]]["repair_attempts"] = {"invalid": "history"}
        module.write_ledger(ledger_path, current)
        expected.update(current)
        with native_lock(ledger_path) as locked:
            yield locked

    def evidence(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        evidence_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", evidence)
    if admission_failure_case == "publication":
        expected.update(module.read_ledger(path))
        monkeypatch.setattr(module.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("replace refused")))
    else:
        monkeypatch.setattr(module, "_locked_ledger", intervening_lock)
    assert module.main(_repair(path, *family_ids.values())) == 2
    assert evidence_calls == []
    assert module.read_ledger(path) == expected
    assert module.read_ledger(path)["families"][family_ids["primary"]]["status"] == "repaired"


def test_validate_only_prior_repair_is_byte_preserving_and_launches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    before = path.read_bytes()
    calls: list[list[str]] = []
    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main([*_repair(path, family_ids["primary"]), "--validate-only"]) == 0
    assert path.read_bytes() == before and calls == []
    assert not path.with_name(f".{path.name}.lock").exists()


def test_identical_overlapping_attempt_cannot_restore_authority_after_newer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    calls = 0
    nested: list[int] = []
    concurrent: dict[str, Any] = {}

    def evidence(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        calls += 1
        if calls == 1:
            nested.append(module.main(_repair(path, family_ids["primary"])))
            concurrent.update(module.read_ledger(path))
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 124, "", "")

    monkeypatch.setattr(module, "_now", lambda: "same-timestamp")
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", evidence)
    assert module.main(_repair(path, family_ids["primary"])) == 2
    assert nested == [2] and calls == 2
    assert module.read_ledger(path) == concurrent
    assert _push(module, path) == 1
    attempts = concurrent["families"][family_ids["primary"]]["repair_attempts"]
    assert len(attempts) == 2 and attempts[0]["id"] != attempts[1]["id"]


def test_multi_family_final_fence_keeps_every_admitted_family_invalid_on_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    concurrent: dict[str, Any] = {}

    def evidence(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        with module._locked_ledger(path) as ledger:
            module.observe_payload(ledger, _payload(202, "sibling", sha=SHA_OTHER), observed_at="concurrent")
            module.write_ledger(path, ledger)
            concurrent.update(ledger)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", evidence)
    assert module.main(_repair(path, *family_ids.values())) == 2
    assert module.read_ledger(path) == concurrent
    assert all(family["status"] == "open" and "repair" not in family for family in concurrent["families"].values())
    assert _push(module, path) == 1


def _fresh_repair_program(path: Path, command: list[str], *, wait: bool = False) -> str:
    fixture = """
native_run = owner.subprocess.run
def evidence(argv, **kwargs):
    if argv == ["make", "--no-print-directory", "worktree-state"]:
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "path": str(owner.ROOT), "head": SHA, "is_clean": True, "dirty_count": 0,
        }), "")
    print("NATIVE_REPAIR_EVIDENCE_WAIT", flush=True)
    sys.stdin.buffer.read(1)
    return subprocess.CompletedProcess(argv, 9, "", "")
owner.subprocess.run = evidence
""" if wait else ""
    return f"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("fresh_repair_owner", {str(SCRIPT)!r})
assert spec is not None and spec.loader is not None
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)
owner.ROOT = Path({str(path.parent)!r})
owner._git_is_ancestor = lambda old, new: True
SHA = {SHA_REPAIR!r}
{fixture}
raise SystemExit(owner.main({command!r}))
"""


@pytest.mark.parametrize("interruption_signal", (signal.SIGTERM, signal.SIGINT))
def test_fresh_cli_interruption_keeps_durable_invalidation_and_releases_native_lock(
    tmp_path: Path, interruption_signal: signal.Signals,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    before = module.read_ledger(path)
    process = subprocess.Popen(
        [sys.executable, "-W", "error", "-c", _fresh_repair_program(
            path, _repair(path, family_ids["primary"]), wait=True,
        )],
        cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
    )
    lines: list[bytes] = []
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as ready:
            ready.register(process.stdout, selectors.EVENT_READ)
            for _index in range(2):
                assert ready.select(timeout=10), "fresh CLI did not reach its bounded evidence seam"
                lines.append(process.stdout.readline())
        during = module.read_ledger(path)
        blocked = subprocess.run(
            [sys.executable, "-W", "error", "-c", _fresh_repair_program(
                path, ["guard-push", "--ledger", str(path), "--head", SHA_REPAIR],
            )], cwd=tmp_path, check=False, capture_output=True, text=True, timeout=15,
        )
    finally:
        if process.poll() is None:
            process.send_signal(interruption_signal)
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=15)
            raise
    assert any(b"NATIVE_REPAIR_EVIDENCE_WAIT" in line for line in lines), [*lines, stdout, stderr]
    assert process.returncode != 0
    assert blocked.returncode == 1, blocked.stdout + blocked.stderr
    assert "CI_FAILURE_PUSH_GUARD_BLOCKED" in blocked.stderr
    assert module.read_ledger(path) == during
    primary = during["families"][family_ids["primary"]]
    assert primary["status"] == "open" and "repair" not in primary
    assert primary["repair_attempts"][-1]["previous_repair"] == before["families"][family_ids["primary"]]["repair"]
    with module._locked_ledger(path) as reloaded:
        assert reloaded == during
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_fresh_cli_native_make_failure_blocks_push_until_actual_success(tmp_path: Path) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    before = module.read_ledger(path)
    state = json.dumps({"path": str(tmp_path), "head": SHA_REPAIR, "is_clean": True, "dirty_count": 0})
    makefile = tmp_path / "Makefile"
    recipe = f".PHONY: worktree-state test-count\nworktree-state:\n\t@printf '%s\\n' '{state}'\ntest-count:\n\t@exit "
    makefile.write_text(recipe + "5\n", encoding="utf-8")
    command = _repair(path, family_ids["primary"])

    def invoke(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-W", "error", "-c", _fresh_repair_program(path, argv)],
            cwd=tmp_path, check=False, capture_output=True, text=True, timeout=15,
        )

    failed = invoke(command)
    blocked = invoke(["guard-push", "--ledger", str(path), "--head", SHA_REPAIR])
    invalid = module.read_ledger(path)
    assert failed.returncode == 2 and "repair evidence failed with exit 2" in failed.stderr
    assert "Error 5" in failed.stderr
    assert blocked.returncode == 1 and "PUSH_GUARD_BLOCKED" in blocked.stderr
    family = invalid["families"][family_ids["primary"]]
    assert family["status"] == "open" and "repair" not in family
    assert family["repair_attempts"][-1]["previous_repair"] == before["families"][family_ids["primary"]]["repair"]
    makefile.write_text(recipe + "0\n", encoding="utf-8")
    restored = invoke(command)
    allowed = invoke(["guard-push", "--ledger", str(path), "--head", SHA_REPAIR])
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert allowed.returncode == 0 and "PUSH_GUARD_PASS" in allowed.stdout
    current = module.read_ledger(path)["families"][family_ids["primary"]]
    assert current["repair"]["sha"] == SHA_REPAIR and current["repair"]["evidence"] == "make test-count"
    assert current["repair_attempts"][:-1] == family["repair_attempts"]


@pytest.mark.parametrize("final_publication_case", ("replace", "bound"))
def test_final_publication_failure_cannot_restore_the_superseded_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, final_publication_case: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    observed: dict[str, Any] = {}
    native_replace = module.os.replace
    replacements = 0
    timestamps = 0

    def replace(source: Path, destination: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("final replace failed")
        native_replace(source, destination)

    def now() -> str:
        nonlocal timestamps
        timestamps += 1
        return "admitted" if timestamps == 1 else "x" * module.MAX_LEDGER_BYTES

    def evidence(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        observed.update(module.read_ledger(path))
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", evidence)
    if final_publication_case == "replace":
        monkeypatch.setattr(module.os, "replace", replace)
    else:
        monkeypatch.setattr(module, "_now", now)
    assert module.main(_repair(path, family_ids["primary"])) == 2
    assert module.read_ledger(path) == observed
    primary = observed["families"][family_ids["primary"]]
    assert primary["status"] == "open" and "repair" not in primary
    assert primary["repair_attempts"][-1]["previous_repair"]["recorded_at"] == "previous-success"
    assert _push(module, path) == 1
    _assert_no_temporary_ledger_files(path)


@pytest.mark.parametrize("before_child_fault", ("write", "interrupt"))
def test_durable_admission_survives_failure_before_the_evidence_child_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, before_child_fault: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    calls: list[list[str]] = []

    def visible(message: str, **_kwargs: Any) -> None:
        if message.startswith("CI_FAILURE_REPAIR_EVIDENCE"):
            if before_child_fault == "interrupt":
                raise KeyboardInterrupt
            raise BrokenPipeError("evidence announcement flush failed")

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module, "print", visible, raising=False)
    monkeypatch.setattr(module.subprocess, "run", run)
    if before_child_fault == "interrupt":
        with pytest.raises(KeyboardInterrupt):
            module.main(_repair(path, family_ids["primary"]))
    else:
        assert module.main(_repair(path, family_ids["primary"])) == 2
    assert calls == []
    primary = module.read_ledger(path)["families"][family_ids["primary"]]
    assert primary["status"] == "open" and "repair" not in primary
    assert primary["repair_attempts"][-1]["previous_repair"]["recorded_at"] == "previous-success"
    assert _push(module, path) == 1


@pytest.mark.parametrize("malformed_attempt_history", (None, {"not": "a list"}))
def test_malformed_attempt_history_refuses_all_selected_families_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed_attempt_history: object,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    ledger = module.read_ledger(path)
    ledger["families"][family_ids["sibling"]]["repair_attempts"] = malformed_attempt_history
    module.write_ledger(path, ledger)
    original = path.read_bytes()
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.main(_repair(path, *family_ids.values())) == 2
    assert calls == [] and path.read_bytes() == original


def test_observation_and_non_success_reconciliation_cannot_resurrect_prior_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", lambda argv, **_kwargs:
                        _checkout_query(argv, module.ROOT) or subprocess.CompletedProcess(argv, 124, "", ""))
    assert module.main(_repair(path, family_ids["primary"])) == 2
    admitted = module.read_ledger(path)
    payload = _payload(202, "primary", sha=SHA_OTHER)
    payload["jobs"][0]["conclusion"] = "skipped"
    monkeypatch.setattr(module, "fetch_run", lambda _run, _repo: payload)
    assert module.main(_observe(path, 202)) == 0
    observed = module.read_ledger(path)
    primary = observed["families"][family_ids["primary"]]
    assert primary["status"] == "open" and "repair" not in primary
    assert primary["repair_attempts"] == admitted["families"][family_ids["primary"]]["repair_attempts"]
    assert _push(module, path) == 1

    with module._locked_ledger(path) as projected:
        family = projected["families"][family_ids["primary"]]
        family.update(
            status="resolved", resolved_by_run=202, resolved_by_sha=SHA_OTHER,
            resolved_at=projected["runs"]["202"]["observed_at"], resolved_by_attempt=1,
        )
        module.write_ledger(path, projected)
    assert module.main(_observe(path, 202)) == 0
    reconciled = module.read_ledger(path)
    assert reconciled == observed, "only the false resolution projection may be removed"
    assert _push(module, path) == 1


@pytest.mark.parametrize("independent_update", ("failure", "success", "repair"))
def test_failed_attempt_retains_independent_selected_family_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, independent_update: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    concurrent: dict[str, Any] = {}

    def evidence(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        with module._locked_ledger(path) as current:
            if independent_update == "repair":
                module.record_repair(
                    current, [family_ids["primary"]], repair_sha=SHA_OTHER, evidence="make independent",
                    recorded_at="independent", is_ancestor=lambda _old, _new: True,
                )
            else:
                payload = _payload(202, "primary", sha=SHA_OTHER)
                if independent_update == "success":
                    payload["conclusion"] = "success"
                    payload["jobs"][0]["conclusion"] = "success"
                module.observe_payload(current, payload, observed_at="independent")
            module.write_ledger(path, current)
            concurrent.update(current)
        return subprocess.CompletedProcess(argv, 124, "", "")

    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", evidence)
    assert module.main(_repair(path, family_ids["primary"])) == 2
    assert module.read_ledger(path) == concurrent
    assert _push(module, path) == (1 if independent_update == "failure" else 0)
    primary = concurrent["families"][family_ids["primary"]]
    assert primary["repair_attempts"][-1]["previous_repair"]["recorded_at"] == "previous-success"


@pytest.mark.parametrize("admission_publication_fault", ("before-replace", "after-replace"))
def test_admission_publication_fault_launches_no_child_and_preserves_safe_persisted_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, admission_publication_fault: str,
) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    family_ids = _seed_repaired(module, path)
    original = path.read_bytes()
    calls: list[list[str]] = []
    native_chmod = module.os.chmod

    def replace(_source: Path, _destination: Path) -> None:
        raise OSError("admission replace refused")

    def chmod(target: Path, mode: int) -> None:
        if Path(target) == path:
            raise OSError("late chmod failed after publication")
        native_chmod(target, mode)

    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        checkout = _checkout_query(argv, module.ROOT)
        if checkout is not None:
            return checkout
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(module, "_git_is_ancestor", lambda _old, _new: True)
    monkeypatch.setattr(module.subprocess, "run", run)
    if admission_publication_fault == "before-replace":
        monkeypatch.setattr(module.os, "replace", replace)
    else:
        monkeypatch.setattr(module.os, "chmod", chmod)
    assert module.main(_repair(path, *family_ids.values())) == 2
    assert calls == []
    if admission_publication_fault == "before-replace":
        assert path.read_bytes() == original
        assert _push(module, path) == 0
    else:
        persisted = module.read_ledger(path)
        assert persisted["runs"] == json.loads(original)["runs"]
        assert all(
            family["status"] == "open" and "repair" not in family
            and family["repair_attempts"][-1]["previous_repair"]["recorded_at"] == "previous-success"
            for family in persisted["families"].values()
        )
        assert len({family["repair_attempts"][-1]["id"] for family in persisted["families"].values()}) == 1
        assert _push(module, path) == 1
    _assert_no_temporary_ledger_files(path)


def _assert_no_temporary_ledger_files(path: Path) -> None:
    assert set(path.parent.glob(f".{path.name}.*")) == {path.with_name(f".{path.name}.lock")}


def test_temporary_ledger_cleanup_assertion_rejects_real_unclaimed_prefix_file(tmp_path: Path) -> None:
    module = _load()
    path = tmp_path / "ledger.json"
    _seed_repaired(module, path)
    with module._locked_ledger(path):
        pass
    _assert_no_temporary_ledger_files(path)
    leaked = path.with_name(f".{path.name}.native-leak")
    leaked.write_bytes(b"retained negative witness")
    with pytest.raises(AssertionError):
        _assert_no_temporary_ledger_files(path)
    assert leaked.read_bytes() == b"retained negative witness"
