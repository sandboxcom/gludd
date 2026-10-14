"""Admission tests for one canonical primary and unchanged native failures."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, cast

import pytest

if TYPE_CHECKING:
    import gate_status_attestation
    import stream_command
else:
    from scripts import gate_status_attestation, stream_command

MODULE = Path(__file__).resolve().parents[2] / "scripts" / "item_progress.py"


@pytest.fixture
def policy(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.setattr(sys, "path", [str(Path.cwd() / "scripts"), *sys.path])
    spec = importlib.util.spec_from_file_location("candidate_item_progress", MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "gludd"
    root.mkdir()
    (root / ".gitignore").write_text(".gludd/\n.gate-logs/\n", encoding="utf-8")
    (root / "TASKS.md").write_text(
        "v0.12.0 milestone is the exact task set F12.1-F12.3\n"
        "- [ ] F12.1 — primary | status: pending\n"
        "- [ ] F12.2 — supporting | status: pending\n"
        "- [ ] F12.3 — other | status: pending\n"
        "- [ ] D9.1 — unrelated backlog | status: pending\n",
        encoding="utf-8",
    )
    (root / "BUGS.md").write_text("Known defects\n", encoding="utf-8")
    (root / "SESSION.md").write_text("Current notes\n", encoding="utf-8")
    (root / "implementation.py").write_text("result = False\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "config", "user.name", "Admission fixture")
    _git(root, "config", "user.email", "admission@example.invalid")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "initial")
    return root


def _start(
    policy: ModuleType,
    root: Path,
    *,
    primary: str = "F12.1",
    task: str = "F12.1",
    supporting: dict[str, str] | None = None,
    command: tuple[str, ...] = ("make", "test-files", "TESTFILES=tests/unit/test_feature.py"),
    environment: dict[str, str] | None = None,
    run_id: str = "attempt-1",
    label: str = "focused",
    state: str = "failed",
    max_seconds: float = 120,
) -> dict[str, object] | None:
    with policy.command_admission(
        root,
        command,
        primary_task_id=primary,
        task_id=task,
        supporting_tasks=supporting or {},
        environment=environment or {"PYTHONHASHSEED": "0"},
        quiet_seconds=30,
        max_seconds=max_seconds,
    ) as admission:
        if admission is None:
            return None
        publisher = stream_command._StatusPublisher(root / ".gate-logs/observed", label, run_id)
        native = stream_command._base_status(
            label=label,
            run_id=run_id,
            started_at=stream_command._utc_now(),
            log_path=publisher.directory / f"{run_id}.log",
            trace_path=None,
        )
        native["progress_identity"] = admission.identity
        publisher.publish(native)
        admission.record(root / ".gate-logs/observed", label, run_id)
        native["state"] = state
        native["exit_code"] = 0 if state == "passed" else 124 if state == "timed_out" else 1
        if state == "running":
            native["exit_code"] = None
        publisher.publish(native)
        return cast(dict[str, object], admission.identity)


def _state(root: Path) -> dict[str, object]:
    return cast(dict[str, object], json.loads((root / ".gludd/item-progress.json").read_text(encoding="utf-8")))


def test_first_primary_is_canonical_and_state_has_no_completion_authority(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository)
    state = _state(repository)
    assert state["primary_task_id"] == "F12.1"
    assert state["producer"] == "stream_command"
    assert state["release_ready"] is False and state["gate_evidence"] is False
    assert len(cast(dict[str, object], state["observations"])) == 1
    assert not list((repository / ".gludd").glob("*.tmp"))
    assert "- [ ] F12.1" in (repository / "TASKS.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("primary", ["", "invented", "D9.1"])
def test_primary_must_be_in_canonical_active_inventory(
    policy: ModuleType, repository: Path, primary: str,
) -> None:
    with pytest.raises(ValueError, match=r"primary.*active"):
        _start(policy, repository, primary=primary, task=primary)


def test_cannot_switch_primary_while_original_is_open(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository, state="passed")
    with pytest.raises(ValueError, match=r"primary F12\.1 remains open"):
        _start(policy, repository, primary="F12.2", task="F12.2", run_id="attempt-2")


def test_primary_can_change_only_after_canonical_ledger_closure(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository)
    tasks = repository / "TASKS.md"
    tasks.write_text(tasks.read_text(encoding="utf-8").replace(
        "[ ] F12.1 — primary | status: pending", "[x] F12.1 — primary | status: complete",
    ), encoding="utf-8")
    _start(policy, repository, primary="F12.2", task="F12.2", run_id="attempt-2")
    assert _state(repository)["primary_task_id"] == "F12.2"


def test_declared_direct_support_is_allowed_but_unrelated_task_is_rejected(
    policy: ModuleType, repository: Path,
) -> None:
    supporting = {"F12.2": "Collection repair directly required by F12.1 acceptance."}
    _start(policy, repository, task="F12.2", supporting=supporting)
    with pytest.raises(ValueError, match="declared direct support"):
        _start(policy, repository, task="F12.3", supporting=supporting, run_id="attempt-2")


@pytest.mark.parametrize("supporting", [
    {"F12.2": ""},
    {"F12.1": "Cannot support itself."},
    {"D9.1": "Unrelated backlog expansion."},
    {"F12.2": "repair", "F12.3": "repair", "D9.1": "repair"},
])
def test_support_is_bounded_and_requires_an_active_id_and_direct_reason(
    policy: ModuleType, repository: Path, supporting: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="support"):
        _start(policy, repository, supporting=supporting)


@pytest.mark.parametrize("state", ["failed", "timed_out"])
def test_unchanged_native_failure_is_blocked_despite_new_label_or_run_id(
    policy: ModuleType, repository: Path, state: str,
) -> None:
    _start(policy, repository, state=state)
    with pytest.raises(ValueError, match=r"unchanged.*focused/attempt-1"):
        _start(policy, repository, label="different-label", run_id="different-run")


def test_diagnostic_does_not_erase_prior_failure_or_require_primary(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository)
    assert _start(policy, repository, primary="", task="", command=("make", "observed-status")) is None
    with pytest.raises(ValueError, match="unchanged"):
        _start(policy, repository, run_id="attempt-2")


@pytest.mark.parametrize("target", ["help", "cat-file", "search", "ps", "pipeline-status", "ci-await"])
def test_diagnostics_and_external_waits_remain_available(
    policy: ModuleType, repository: Path, target: str,
) -> None:
    assert _start(policy, repository, primary="", task="", command=("make", target)) is None
    assert not (repository / ".gludd/item-progress.json").exists()


_STATUS_WAIT = (
    "make", "status-heartbeat", "INTERVAL=300", "COUNT=2",
    "STATUS_HEARTBEAT_VALIDATE_ONLY=0", "STATUS_HEARTBEAT_STOP_ON_TERMINAL=0",
    "PIPELINE_STATUS_REPO=sandboxcom/gludd", "PIPELINE_STATUS_BRANCH=development",
    "PIPELINE_STATUS_REMOTE=sandboxcom", "PIPELINE_STATUS_SHA=" + "1" * 40,
    "PIPELINE_STATUS_FAILURE_LEDGER=.gludd/ci-failure-ledger.json",
    "PIPELINE_STATUS_VALIDATE_ONLY=0", "PIPELINE_STATUS_OUTPUT=.gludd/release-proof-status.json",
)


@pytest.mark.parametrize("command", (
    _STATUS_WAIT,
    ("/usr/bin/make", "--no-print-directory", *_STATUS_WAIT[1:]),
))
def test_bounded_status_wait_does_not_require_primary_or_create_progress(
    policy: ModuleType, repository: Path, command: tuple[str, ...],
) -> None:
    assert _start(policy, repository, primary="", task="", command=command, max_seconds=900) is None
    assert not (repository / ".gludd/item-progress.json").exists()


def test_bounded_status_wait_preserves_full_failed_inventory_and_substantive_guards(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stable_environment(*_args: object, **_kwargs: object) -> str:
        return "b" * 64

    monkeypatch.setattr(policy, "_environment_identity", stable_environment)
    for index in range(policy._MAX_COMMANDS):
        _start(policy, repository,
               command=("make", "test-files", f"TESTFILES=failed-phase-{index}.py"),
               run_id=f"failed-phase-{index}")
    progress = repository / ".gludd/item-progress.json"
    before = progress.read_bytes()
    receipts = {path: path.read_bytes() for path in (repository / ".gate-logs").rglob("*.json")}
    assert len(cast(dict[str, object], _state(repository)["observations"])) == 32

    assert _start(policy, repository, command=_STATUS_WAIT, max_seconds=900) is None

    assert progress.read_bytes() == before
    assert all(path.read_bytes() == content for path, content in receipts.items())
    assert "- [ ] F12.1" in (repository / "TASKS.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="bounded command inventory"):
        _start(policy, repository, command=("make", "test-files", "TESTFILES=new-phase.py"))
    with pytest.raises(ValueError, match="unchanged"):
        _start(policy, repository, command=("make", "test-files", "TESTFILES=failed-phase-0.py"))


@pytest.mark.parametrize("command, maximum", (
    (tuple(arg for arg in _STATUS_WAIT if not arg.startswith("COUNT=")), 900),
    (tuple("COUNT=0" if arg.startswith("COUNT=") else arg for arg in _STATUS_WAIT), 900),
    (tuple("COUNT=25" if arg.startswith("COUNT=") else arg for arg in _STATUS_WAIT), 10000),
    (tuple("INTERVAL=299" if arg.startswith("INTERVAL=") else arg for arg in _STATUS_WAIT), 900),
    (tuple("INTERVAL=٣٠٠" if arg.startswith("INTERVAL=") else arg for arg in _STATUS_WAIT), 900),
    (tuple("INTERVAL=9999999999999999999999" if arg.startswith("INTERVAL=") else arg
           for arg in _STATUS_WAIT), 900),
    ((*_STATUS_WAIT, "COUNT=2"), 900),
    ((*_STATUS_WAIT, "SHELL=/tmp/foreign-shell"), 900),
    ((*_STATUS_WAIT, "MAKEFILES=foreign.mk"), 900),
    ((*_STATUS_WAIT, "UNREGISTERED_OPTION=1"), 900),
    (("make", "-f", "foreign.mk", *_STATUS_WAIT[1:]), 900),
    ((*_STATUS_WAIT, "test-files"), 900),
    (_STATUS_WAIT, 0),
    (_STATUS_WAIT, 299),
    (_STATUS_WAIT, float("inf")),
    (_STATUS_WAIT, float("nan")),
    (_STATUS_WAIT, True),
))
def test_unbounded_or_overridden_status_wait_remains_scoped(
    policy: ModuleType, repository: Path, command: tuple[str, ...], maximum: float,
) -> None:
    with pytest.raises(ValueError, match=r"primary.*active|runtime bounds"):
        _start(policy, repository, primary="", task="", command=command, max_seconds=maximum)
    assert not (repository / ".gludd/item-progress.json").exists()


_DISK_PREVIEW = (
    "make", "disk-cleanup-preflight", "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0",
    "DISK_CLEANUP_PREFLIGHT_DRY_RUN=1", "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800",
)


@pytest.mark.parametrize("command", (
    ("make", "disk-check"),
    ("make", "--no-print-directory", "disk-check"),
    _DISK_PREVIEW,
    ("make", "--no-print-directory", *_DISK_PREVIEW[1:]),
    ("make", *_DISK_PREVIEW[1:4], "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=3600"),
))
def test_read_only_capacity_checks_do_not_require_source_change_or_primary(
    policy: ModuleType, repository: Path, command: tuple[str, ...],
) -> None:
    assert _start(policy, repository, primary="", task="", command=command) is None
    assert _start(policy, repository, primary="", task="", command=command, run_id="later") is None
    assert not (repository / ".gludd/item-progress.json").exists()


@pytest.mark.parametrize("command", (
    ("make", "disk-cleanup-preflight"),
    ("make", *_DISK_PREVIEW[1:3], "DISK_CLEANUP_PREFLIGHT_DRY_RUN=0", _DISK_PREVIEW[4]),
    ("make", "disk-cleanup-preflight", "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=1", *_DISK_PREVIEW[3:]),
    (*_DISK_PREVIEW, "DISK_CLEANUP_PREFLIGHT_DRY_RUN=0"),
    (*_DISK_PREVIEW[:4], "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1799"),
    (*_DISK_PREVIEW[:4], "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=unknown"),
    (*_DISK_PREVIEW[:4], "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=١٨٠٠"),
    (*_DISK_PREVIEW[:4], "DISK_CLEANUP_RECEIPT_GRACE_SECONDS=12345678901"),
    (*_DISK_PREVIEW[:4], "missing-assignment"),
    (*_DISK_PREVIEW, "SYSTEM_PYTHON=foreign-runner"),
    (*_DISK_PREVIEW, "SHELL=foreign-shell"),
    (*_DISK_PREVIEW, "MAKEFILES=foreign.mk"),
    (*_DISK_PREVIEW, "MAKEFLAGS=--eval=foreign"),
    (*_DISK_PREVIEW, "another-target"),
    ("make", "-f", "foreign.mk", *_DISK_PREVIEW[1:]),
    ("make", "--eval=foreign", *_DISK_PREVIEW[1:]),
    ("make", "disk-check", "SYSTEM_PYTHON=foreign-runner"),
    ("make", "disk-check", "disk-cleanup-preflight"),
))
def test_mutating_or_overridden_capacity_commands_retain_unchanged_failure_guard(
    policy: ModuleType, repository: Path, command: tuple[str, ...],
) -> None:
    assert _start(policy, repository, command=command) is not None
    with pytest.raises(ValueError, match="unchanged failed inputs"):
        _start(policy, repository, command=command, run_id="later")


def test_capacity_diagnostic_preserves_failed_release_observation_and_authority(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository)
    before = (repository / ".gludd/item-progress.json").read_bytes()
    assert _start(policy, repository, command=_DISK_PREVIEW) is None
    assert (repository / ".gludd/item-progress.json").read_bytes() == before
    with pytest.raises(ValueError, match="unchanged failed inputs"):
        _start(policy, repository, run_id="later")
    assert _state(repository)["release_ready"] is False
    assert _state(repository)["gate_evidence"] is False


def test_capacity_diagnostic_can_recheck_retained_legacy_failure_without_erasing_it(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with monkeypatch.context() as legacy:
        legacy.setattr(policy, "_read_only_capacity_diagnostic", lambda _command: False, raising=False)
        assert _start(policy, repository, command=_DISK_PREVIEW) is not None
    before = (repository / ".gludd/item-progress.json").read_bytes()
    assert _start(policy, repository, command=_DISK_PREVIEW, run_id="later") is None
    assert (repository / ".gludd/item-progress.json").read_bytes() == before
    assert _state(repository)["release_ready"] is False
    assert _state(repository)["gate_evidence"] is False


@pytest.mark.parametrize("command", ((), ("alternate-runner", "disk-check")))
def test_capacity_diagnostic_rejects_empty_or_substituted_entrypoint(
    policy: ModuleType, command: tuple[str, ...],
) -> None:
    assert policy._read_only_capacity_diagnostic(command) is False


def test_make_recipe_override_is_not_classified_as_read_only(
    policy: ModuleType, repository: Path,
) -> None:
    with pytest.raises(ValueError, match=r"primary.*active"):
        _start(policy, repository, primary="", task="", command=("make", "--eval=help:; echo mutation", "help"))


@pytest.mark.parametrize("change", ["code", "environment", "command"])
def test_actual_input_change_allows_failing_first_repair_retry(
    policy: ModuleType, repository: Path, change: str,
) -> None:
    _start(policy, repository)
    environment = {"PYTHONHASHSEED": "0"}
    command = ("make", "test-files", "TESTFILES=tests/unit/test_feature.py")
    if change == "code":
        (repository / "implementation.py").write_text("result = True\n", encoding="utf-8")
    elif change == "environment":
        environment = {"PYTHONHASHSEED": "1"}
    elif change == "command":
        command = ("make", "test-files", "TESTFILES=tests/unit/test_repair.py")
    _start(policy, repository, run_id="attempt-2", environment=environment, command=command)


def test_observer_metadata_and_notes_do_not_change_failed_inputs(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository)
    for name in ("TASKS.md", "BUGS.md", "SESSION.md"):
        path = repository / name
        path.write_text(path.read_text(encoding="utf-8") + "\nUpdated notes\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unchanged"):
        _start(policy, repository, run_id="attempt-2", environment={
            "PYTHONHASHSEED": "0", "RUN_ID": "new", "OBSERVED_LABEL": "new",
            "MAKEFLAGS": "RUN_ID=new", "GLUDD_XDIST_TRACE_RUN_ID": "new",
        })


def test_commit_only_change_does_not_admit_unchanged_failure(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository)
    _git(repository, "commit", "--quiet", "--allow-empty", "-m", "new commit identity only")
    with pytest.raises(ValueError, match="unchanged"):
        _start(policy, repository, run_id="attempt-2")


def test_existing_content_owner_keeps_gate_defaults_and_excludes_only_fixed_notes(
    repository: Path,
) -> None:
    gate_before = gate_status_attestation.repository_state_id(repository)
    progress_before = gate_status_attestation.repository_state_id(
        repository, include_head=False, exclude_bookkeeping=True,
    )
    (repository / "SESSION.md").write_text("New session notes\n", encoding="utf-8")
    _git(repository, "add", "SESSION.md")
    _git(repository, "commit", "--quiet", "-m", "notes")
    assert gate_status_attestation.repository_state_id(repository) != gate_before
    assert gate_status_attestation.repository_state_id(
        repository, include_head=False, exclude_bookkeeping=True,
    ) == progress_before
    (repository / "implementation.py").write_text("result = True\n", encoding="utf-8")
    assert gate_status_attestation.repository_state_id(
        repository, include_head=False, exclude_bookkeeping=True,
    ) != progress_before


@pytest.mark.parametrize("field,value", [
    ("release_ready", True), ("gate_evidence", True), ("producer", "manual"),
    ("root", "/tmp/another-project"), ("observations", {"bad-key": {}}),
])
def test_persisted_state_cannot_supply_manual_authority_or_foreign_inputs(
    policy: ModuleType, repository: Path, field: str, value: object,
) -> None:
    _start(policy, repository)
    state = _state(repository)
    state[field] = value
    (repository / ".gludd/item-progress.json").write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(ValueError):
        _start(policy, repository, run_id="attempt-2")


@pytest.mark.parametrize("mutation", ["missing", "symlink", "wrong-command", "bad-exit", "bad-identity"])
def test_prior_failure_requires_bounded_native_observer_inputs(
    policy: ModuleType, repository: Path, mutation: str,
) -> None:
    _start(policy, repository)
    path = repository / ".gate-logs/observed/focused/attempt-1.json"
    native = json.loads(path.read_text(encoding="utf-8"))
    if mutation == "missing":
        path.unlink()
    elif mutation == "symlink":
        destination = path.with_name("foreign.json")
        path.rename(destination)
        path.symlink_to(destination)
    else:
        if mutation == "wrong-command":
            native["progress_identity"]["command"] = ["make", "unrelated"]
        elif mutation == "bad-exit":
            native["exit_code"] = True
        else:
            native["progress_identity"]["source"] = "manual"
        path.write_text(json.dumps(native), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        _start(policy, repository, run_id="attempt-2")


def test_native_pass_allows_replay_without_claiming_completion(
    policy: ModuleType, repository: Path,
) -> None:
    _start(policy, repository, state="passed")
    _start(policy, repository, run_id="attempt-2", state="passed")
    assert _state(repository)["release_ready"] is False
    assert "[ ] F12.1" in (repository / "TASKS.md").read_text(encoding="utf-8")


def _fixed_identity(policy: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate synchronization/input policy from the separately tested content owner."""
    def identity(root: Path, command: tuple[str, ...], _environment: dict[str, str],
                 quiet_seconds: float, max_seconds: float) -> dict[str, object]:
        return {"root": str(root), "command": list(command), "source": "a" * 64,
                "environment": "b" * 64, "limits": [quiet_seconds, max_seconds]}
    monkeypatch.setattr(policy, "_identity", identity)


class _AdmissionView(Protocol):
    identity: dict[str, object]

    def record(self, observed_root: Path, label: str, run_id: str) -> None: ...


def _publish_start(admission: _AdmissionView, root: Path, run_id: str) -> None:
    publisher = stream_command._StatusPublisher(root / ".gate-logs/observed", "parallel", run_id)
    native = stream_command._base_status(
        label="parallel", run_id=run_id, started_at=stream_command._utc_now(),
        log_path=publisher.directory / f"{run_id}.log", trace_path=None,
    )
    native["progress_identity"] = admission.identity
    publisher.publish(native)
    admission.record(root / ".gate-logs/observed", "parallel", run_id)


def test_distinct_primary_and_support_commands_admit_concurrently_and_merge_records(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    arguments = {"primary_task_id": "F12.1", "environment": {}, "quiet_seconds": 30,
                 "max_seconds": 120, "supporting_tasks": {"F12.2": "Direct acceptance repair for F12.1."}}
    with policy.command_admission(repository, ("make", "primary-proof"), **arguments) as first:
        with policy.command_admission(repository, ("make", "support-proof"), task_id="F12.2", **arguments) as second:
            _publish_start(second, repository, "support-start")
        _publish_start(first, repository, "primary-start")
    assert len(cast(dict[str, object], _state(repository)["observations"])) == 2


def test_primary_reservation_precedes_publication_and_blocks_duplicate_launch(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    arguments = {"primary_task_id": "F12.1", "environment": {}, "quiet_seconds": 30, "max_seconds": 120}
    with policy.command_admission(repository, ("make", "primary-proof"), **arguments):
        assert _state(repository)["primary_task_id"] == "F12.1"
        with (
            pytest.raises(ValueError, match="already reserved"),
            policy.command_admission(repository, ("make", "primary-proof"), **arguments),
        ):
            pytest.fail("duplicate command launched")
        with pytest.raises(ValueError, match=r"primary F12\.1 remains open"):
            policy.focus_item(repository, "F12.2")


@pytest.mark.parametrize("state", ["failed", "timed_out"])
def test_supervisor_limit_change_cannot_clear_unchanged_native_failure(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    _fixed_identity(policy, monkeypatch)
    original = _start(policy, repository, state=state)
    assert original is not None
    assert original["limits"] == [30, 120]
    with pytest.raises(ValueError, match="unchanged"):
        _start(policy, repository, run_id="longer-timeout", max_seconds=240)


def test_dependency_absent_readonly_import_and_status_work_with_plain_python(
    policy: ModuleType, repository: Path,
) -> None:
    policy.focus_item(repository, "F12.1")
    code = (
        "import importlib.util, pathlib, sys; "
        f"sys.path.insert(0, {str(Path.cwd())!r}); "
        f"spec = importlib.util.spec_from_file_location('plain_policy', {str(MODULE)!r}); "
        "module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; "
        "spec.loader.exec_module(module); "
        f"assert module.progress_status(pathlib.Path({str(repository)!r}))['primary_task_id'] == 'F12.1'"
    )
    completed = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_concurrent_record_race_preserves_both_owned_pointers(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    barrier = threading.Barrier(2, timeout=5)
    def run(name: str) -> None:
        with policy.command_admission(repository, ("make", name), primary_task_id="F12.1",
                                      environment={}, quiet_seconds=30, max_seconds=120) as admission:
            barrier.wait()
            _publish_start(admission, repository, name)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gludd-item-progress-test") as pool:
        results = [pool.submit(run, name) for name in ("first", "second")]
        for result in results:
            result.result(timeout=10)
    state = _state(repository)
    assert len(cast(dict[str, object], state["observations"])) == 2
    assert state["reservations"] == {}


@pytest.mark.parametrize("field,value", [("owner_pid", True), ("owner_pid", -1), ("token", "manual")])
def test_reservation_tamper_is_rejected_before_execution(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    _fixed_identity(policy, monkeypatch)
    arguments = {"primary_task_id": "F12.1", "environment": {}, "quiet_seconds": 30, "max_seconds": 120}
    with policy.command_admission(repository, ("make", "primary-proof"), **arguments):
        original = _state(repository)
        state = json.loads(json.dumps(original))
        reservation = next(iter(state["reservations"].values()))
        reservation[field] = value
        path = repository / ".gludd/item-progress.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        try:
            with (
                pytest.raises(ValueError, match="reservation"),
                policy.command_admission(repository, ("make", "other-proof"), **arguments),
            ):
                pytest.fail("tampered reservation admitted")
        finally:
            path.write_text(json.dumps(original), encoding="utf-8")


@pytest.mark.parametrize("mutation", ["token", "foreign-primary"])
def test_record_rejects_replaced_reservation_or_foreign_focus(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    _fixed_identity(policy, monkeypatch)
    with policy.command_admission(repository, ("make", "primary-proof"), primary_task_id="F12.1",
                                  environment={}, quiet_seconds=30, max_seconds=120) as admission:
        original = _state(repository)
        state = json.loads(json.dumps(original))
        if mutation == "token":
            next(iter(state["reservations"].values()))["token"] = "c" * 32
        else:
            state["primary_task_id"] = "F12.2"
        path = repository / ".gludd/item-progress.json"
        path.write_text(json.dumps(state), encoding="utf-8")
        try:
            with pytest.raises(ValueError, match=r"reservation|primary"):
                _publish_start(admission, repository, "stale-start")
        finally:
            path.write_text(json.dumps(original), encoding="utf-8")


def test_abandoned_reservation_retries_and_cleanup_keeps_new_owner_token(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fixed_identity(policy, monkeypatch)
    arguments = {"primary_task_id": "F12.1", "environment": {}, "quiet_seconds": 30, "max_seconds": 120}
    with policy.command_admission(repository, ("make", "primary-proof"), **arguments):
        state = _state(repository)
        abandoned = next(iter(cast(dict[str, dict[str, object]], state["reservations"]).values()))
        abandoned["owner_pid"] = 987654
        monkeypatch.setattr(policy, "_pid_alive", lambda pid: pid != 987654)
        (repository / ".gludd/item-progress.json").write_text(json.dumps(state), encoding="utf-8")
        with policy.command_admission(repository, ("make", "primary-proof"), **arguments) as replacement:
            reservations = cast(dict[str, dict[str, object]], _state(repository)["reservations"])
            new_reservation = next(iter(reservations.values()))
            assert new_reservation["token"] != abandoned["token"]
            _publish_start(replacement, repository, "replacement-start")
    assert _state(repository)["reservations"] == {}


def test_stage_role_and_supervisor_env_metadata_do_not_clear_native_failure(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(policy, "repository_state_id", lambda *_args, **_kwargs: "a" * 64)
    _start(policy, repository)
    with pytest.raises(ValueError, match="unchanged"):
        _start(policy, repository, run_id="new-role", environment={
            "PYTHONHASHSEED": "0", "WORK_ITEM_ID": "F12.2", "WORK_ITEM_ACTION": "support",
            "WORK_ITEM_REASON": "new declaration", "WORK_ITEM_VALIDATE_ONLY": "0",
            "MAX_SECS": "240", "STALL_SECS": "60", "OBSERVED_MAX_SECS": "240",
        })


def test_real_watched_cli_records_failure_and_rejects_unchanged_retry(
    policy: ModuleType, repository: Path,
) -> None:
    policy.focus_item(repository, "F12.1")
    script = Path.cwd() / "scripts/stream_command.py"
    child = (
        "from pathlib import Path; p=Path('.gate-logs/launches'); "
        "p.write_text(p.read_text()+'x' if p.exists() else 'x'); raise SystemExit(1)"
    )
    base = [
        sys.executable, str(script), "--item-progress", "--label", "real",
        "--root", str(repository / ".gate-logs/observed"), "--retain-runs", "1",
        "--max-secs", "10", "--",
        sys.executable, "-c", child,
    ]
    first = subprocess.run(base, cwd=repository, text=True, capture_output=True, check=False, timeout=20)
    assert first.returncode == 1, first.stdout + first.stderr
    state = _state(repository)
    assert len(cast(dict[str, object], state["observations"])) == 1
    assert state["reservations"] == {}
    second = subprocess.run(base, cwd=repository, text=True, capture_output=True, check=False, timeout=20)
    assert second.returncode == 2, second.stdout + second.stderr
    assert "unchanged failed inputs" in second.stderr
    assert (repository / ".gate-logs/launches").read_text() == "x"
    assert state["release_ready"] is False


def test_real_watched_cli_without_focus_preserves_unattended_owner_compatibility(repository: Path) -> None:
    script = Path.cwd() / "scripts/stream_command.py"
    result = subprocess.run(
        [sys.executable, str(script), "--item-progress", "--label", "unattended",
         "--root", str(repository / ".gate-logs/observed"), "--max-secs", "10",
         "--", sys.executable, "-c", "print('native unattended check')"],
        cwd=repository, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (repository / ".gludd/item-progress.json").exists()


@pytest.mark.parametrize("override", ("UV=alternate-runner", "SHELL=/tmp/foreign-shell", "MAKEFILES=foreign.mk"))
def test_diagnostic_recipe_overrides_do_not_bypass_scoped_admission(
    policy: ModuleType, repository: Path, override: str,
) -> None:
    with pytest.raises(ValueError, match=r"primary.*active"):
        _start(policy, repository, primary="", task="", command=("make", "help", override))


@pytest.mark.parametrize("target", ["help", "status-heartbeat"])
def test_exempt_native_observer_retention_keeps_full_failed_history(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    _fixed_identity(policy, monkeypatch)
    (repository / "Makefile").write_text(
        "help status-heartbeat:\n\t@printf 'native diagnostic complete\\n'\n", encoding="utf-8",
    )
    label = "retention"
    for index in range(policy._MAX_COMMANDS):
        _start(policy, repository, command=("make", f"failed-proof-{index}"),
               label=label, run_id=f"failed-{index}")
        (repository / f".gate-logs/observed/{label}/failed-{index}.log").write_text("failed evidence\n")
    progress = repository / ".gludd/item-progress.json"
    before = progress.read_bytes()
    retained = {path: path.read_bytes() for path in (repository / ".gate-logs/observed").rglob("*")
                if path.is_file() and path.suffix in {".json", ".log"}
                and path.name.startswith("failed-")}
    assert len(retained) == 64
    script = MODULE.with_name("stream_command.py")
    result = subprocess.run(
        [sys.executable, str(script), "--item-progress", "--label", label,
         "--run-id", "diagnostic", "--root", str(repository / ".gate-logs/observed"),
         "--retain-runs", "1", "--max-secs", "10", "--", "make", target,
         "INTERVAL=300", "COUNT=1", "STATUS_HEARTBEAT_VALIDATE_ONLY=0",
         "STATUS_HEARTBEAT_STOP_ON_TERMINAL=0"],
        cwd=repository, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(path.exists() and path.read_bytes() == content for path, content in retained.items())
    assert progress.read_bytes() == before
    assert (repository / "TASKS.md").read_text().count("- [ ]") == 4
    with pytest.raises(ValueError, match="unchanged"):
        _start(policy, repository, command=("make", "failed-proof-0"))


@pytest.mark.parametrize("target", ["help", "status-heartbeat"])
@pytest.mark.parametrize("artifact", ["receipt", "orphan-log", "orphan-trace", "reference-only"])
def test_exempt_observer_refuses_reusing_native_failure_identity(
    policy: ModuleType, repository: Path, target: str, artifact: str,
) -> None:
    (repository / "Makefile").write_text(
        "help status-heartbeat:\n\t@printf 'executed' > child-executed\n", encoding="utf-8",
    )
    _start(policy, repository, command=("make", "failed-proof"), label="retention", run_id="failed")
    directory = repository / ".gate-logs/observed/retention"
    (directory / "failed.log").write_bytes(b"original failed output\n")
    (directory / "failed.pytest.jsonl").write_bytes(b"original failed trace\n")
    if artifact != "receipt":
        (directory / "failed.json").unlink()
    if artifact in {"orphan-trace", "reference-only"}:
        (directory / "failed.log").unlink()
    if artifact == "reference-only":
        (directory / "failed.pytest.jsonl").unlink()
    before = {path: path.read_bytes() for path in directory.iterdir() if path.is_file()}
    progress = repository / ".gludd/item-progress.json"
    before_progress = progress.read_bytes()
    result = subprocess.run(
        [sys.executable, str(MODULE.with_name("stream_command.py")), "--item-progress",
         "--label", "retention", "--run-id", "failed", "--root",
         str(repository / ".gate-logs/observed"), "--retain-runs", "1", "--max-secs", "10",
         "--", "make", target, "INTERVAL=300", "COUNT=1"],
        cwd=repository, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 125, result.stdout + result.stderr
    assert not (repository / "child-executed").exists()
    assert {path: path.read_bytes() for path in directory.iterdir() if path.is_file()} == before
    assert progress.read_bytes() == before_progress
    assert (repository / "TASKS.md").read_text().count("- [ ]") == 4
    expected_error = ValueError if artifact == "receipt" else FileNotFoundError
    expected_message = "unchanged" if artifact == "receipt" else r"failed\.json"
    with pytest.raises(expected_error, match=expected_message):
        _start(policy, repository, command=("make", "failed-proof"))


@pytest.mark.parametrize("loss", ["missing", "malformed"])
def test_exempt_retention_refuses_lost_native_progress_without_deleting_evidence(
    policy: ModuleType, repository: Path, loss: str,
) -> None:
    _start(policy, repository, label="retention", run_id="failed")
    protected = repository / ".gate-logs/observed/retention/failed.json"
    before = protected.read_bytes()
    mutation = ("rm .gludd/item-progress.json" if loss == "missing"
                else "printf '{}' > .gludd/item-progress.json")
    (repository / "Makefile").write_text(f"help:\n\t@{mutation}\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(MODULE.with_name("stream_command.py")), "--item-progress",
         "--label", "retention", "--run-id", "diagnostic", "--root",
         str(repository / ".gate-logs/observed"), "--retain-runs", "1", "--max-secs", "10",
         "--", "make", "help"],
        cwd=repository, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 125, result.stdout + result.stderr
    assert protected.exists() and protected.read_bytes() == before
    terminal = json.loads((protected.parent / "diagnostic.json").read_text())
    assert terminal["state"] == "failed" and terminal["exit_code"] == 125
    assert terminal["termination_reason"] == "observer-retention-failed"


def test_real_retention_preserves_referenced_failed_input_evidence(
    policy: ModuleType, repository: Path,
) -> None:
    policy.focus_item(repository, "F12.1")
    script = Path.cwd() / "scripts/stream_command.py"
    for index in range(3):
        result = subprocess.run(
            [sys.executable, str(script), "--item-progress", "--label", "retention",
             "--run-id", f"attempt-{index}", "--root", str(repository / ".gate-logs/observed"),
             "--retain-runs", "1", "--max-secs", "10", "--", sys.executable, "-c",
             f"print('native-{index}'); raise SystemExit({int(index == 0)})"],
            cwd=repository, text=True, capture_output=True, check=False, timeout=20,
        )
        assert result.returncode == int(index == 0), result.stdout + result.stderr
    retained = repository / ".gate-logs/observed/retention/attempt-0.json"
    assert retained.exists(), "referenced unsuccessful evidence must survive ordinary retention"
    assert json.loads(retained.read_text())["state"] == "failed"


@pytest.mark.parametrize(
    "arguments, expected",
    (
        (["status"], 0),
        (["focus", "--task", "F12.1"], 0),
        (["focus"], 2),
        (["support", "--task", "F12.2"], 2),
        (["support", "--task", "F12.2", "--reason", "direct acceptance"], 2),
        (["status", "--validate-only", "1"], 0),
        (["focus", "--task", "F12.1", "--validate-only", "1"], 0),
    ),
)
def test_native_control_cli_is_bounded_and_does_not_invent_readiness(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], arguments: list[str], expected: int,
) -> None:
    monkeypatch.chdir(repository)
    assert policy.main(arguments) == expected
    output = capsys.readouterr()
    if expected == 0:
        record = json.loads(output.out)
        assert record["release_ready"] is False
        assert record["gate_evidence"] is False


def test_native_support_cli_updates_only_its_owned_declaration(
    policy: ModuleType, repository: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(repository)
    policy.focus_item(repository, "F12.1")
    assert policy.main(["support", "--task", "F12.2", "--reason", "Direct acceptance dependency"]) == 0
    assert _state(repository)["supporting_tasks"] == {"F12.2": "Direct acceptance dependency"}
    assert policy.main(["status"]) == 0
    assert _state(repository)["observations"] == {}


@pytest.mark.parametrize("target", ("state-symlink", "oversized-state", "nonobject-state"))
def test_administrative_input_boundary_fails_before_any_execution(
    policy: ModuleType, repository: Path, target: str,
) -> None:
    directory = repository / ".gludd"
    directory.mkdir()
    path = directory / "item-progress.json"
    if target == "state-symlink":
        source = directory / "foreign.json"
        source.write_text("{}")
        path.symlink_to(source)
    elif target == "oversized-state":
        path.write_text(" " * (128 * 1024 + 1))
    else:
        path.write_text("[]")
    with pytest.raises(ValueError):
        policy.progress_status(repository)


def test_native_control_entrypoint_plan_does_not_create_state(repository: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(MODULE), "focus", "--task", "F12.1", "--validate-only", "1"],
        cwd=repository, text=True, capture_output=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["kind"] == "work_item_plan"
    assert not (repository / ".gludd/item-progress.json").exists()
