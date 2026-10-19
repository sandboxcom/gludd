"""Native ledger transactions retain concurrent hosted observations and repairs."""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
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
