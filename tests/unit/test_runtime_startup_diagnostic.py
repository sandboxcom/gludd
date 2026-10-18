"""Bounded, read-only startup reports and native diagnostic routing."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import item_progress as progress_owner
    import stream_command as owner
else:
    from scripts import item_progress as progress_owner
    from scripts import stream_command as owner

ROOT = Path(__file__).resolve().parents[2]


def _segments() -> str:
    return "".join(
        "import time: self [us] | cumulative | imported package\n"
        "import time:        11 |         11 |   nested\n"
        "import time:        23 |         34 | parent\n"
        "============================= test session starts ==============================\n"
        "platform darwin -- Python 3.12.15, pytest-9.1.1\n"
        f"========================== {count} tests collected in {seconds}s ==========================\n"
        for count, seconds in ((1897, "11.22"), (1, "0.50"), (11, "3.72"))
    )


def _run(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    repository = tmp_path / "checkout"
    root = repository / ".gate-logs/observed"
    directory = root / "profile"
    directory.mkdir(parents=True)
    log = repository / ".gate-logs/imports.log"
    log.write_text(_segments(), encoding="utf-8")
    status = directory / "run-1.json"
    status.write_text(json.dumps({
        "schema_version": 1, "kind": "observed_command", "label": "profile",
        "run_id": "run-1", "state": "passed", "exit_code": 0,
        "elapsed_seconds": 21.694, "log_path": str(log), "trace_path": None,
        "bytes_written": log.stat().st_size, "lines_written": len(_segments().splitlines()),
    }) + "\n", encoding="utf-8")
    return repository, root, status, log


def test_report_separates_visible_self_cumulative_and_unobserved_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root, status, log = _run(tmp_path)
    before = (status.read_bytes(), log.read_bytes())
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("report spawned"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("report ran command"))
    report = owner.runtime_startup_report(
        repository, root, "profile", "run-1", source_sha="f" * 40,
    )
    assert "21.694" in report
    assert report.count("Visible self: 34 microseconds") == 3
    assert "parent: self 23, cumulative 34 microseconds" in report
    assert "1897 collected / 11.22 seconds" in report
    assert "1 collected / 0.50 seconds" in report
    assert "11 collected / 3.72 seconds" in report
    assert "capture-limited" in report
    assert "first-header latency: unknown" in report
    assert "trace: absent" in report and "parent console: absent" in report
    assert "source binding: declared" in report and "command argv: absent" in report
    assert "never admission or release evidence" in report
    assert before == (status.read_bytes(), log.read_bytes())


@pytest.mark.parametrize("state,code", [("failed", 1), ("timed_out", 124), ("interrupted", 143)])
def test_report_retains_terminal_failure(tmp_path: Path, state: str, code: int) -> None:
    repository, root, status, _ = _run(tmp_path)
    value = json.loads(status.read_text())
    value.update(state=state, exit_code=code)
    status.write_text(json.dumps(value))
    report = owner.runtime_startup_report(repository, root, "profile", "run-1")
    assert f"state: {state}; rc: {code}" in report
    assert "never admission or release evidence" in report


@pytest.mark.parametrize("mutation", [
    lambda text: "\n".join(text.splitlines()[:-1]),
    lambda text: text.replace("        11 |         11", "         x |         11", 1),
    lambda text: text.replace("        11 |         11", "        12 |         11", 1),
    lambda text: text + text,
    lambda text: text.replace("platform darwin", "platform linux", 1),
    lambda text: text.replace("test session starts", "lost session", 1),
])
def test_malformed_or_interleaved_import_segments_are_explicit(
    tmp_path: Path, mutation: Any,
) -> None:
    repository, root, status, log = _run(tmp_path)
    log.write_text(mutation(_segments()))
    value = json.loads(status.read_text())
    value["bytes_written"] = log.stat().st_size
    value["lines_written"] = len(log.read_text().splitlines())
    status.write_text(json.dumps(value))
    report = owner.runtime_startup_report(repository, root, "profile", "run-1")
    assert "Import analysis incomplete:" in report
    assert "Visible self:" not in report


@pytest.mark.parametrize("run_id", ["", "current", "../run-1", "missing"])
def test_exact_run_is_mandatory(tmp_path: Path, run_id: str) -> None:
    repository, root, _, _ = _run(tmp_path)
    with pytest.raises((ValueError, OSError)):
        owner.runtime_startup_report(repository, root, "profile", run_id)


@pytest.mark.parametrize("mutation", ["identity", "foreign", "symlink", "root", "size", "running"])
def test_report_refuses_unowned_or_incoherent_inputs(tmp_path: Path, mutation: str) -> None:
    repository, root, status, log = _run(tmp_path)
    value = json.loads(status.read_text())
    if mutation == "identity":
        value["run_id"] = "different"
    elif mutation == "foreign":
        foreign = tmp_path / "foreign.log"
        foreign.write_text(_segments())
        value["log_path"] = str(foreign)
    elif mutation == "symlink":
        target = log.with_suffix(".real")
        log.rename(target)
        log.symlink_to(target)
    elif mutation == "root":
        root = root.parent
    elif mutation == "size":
        value["bytes_written"] += 1
    else:
        value["state"] = "running"
    status.write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        owner.runtime_startup_report(repository, root, "profile", "run-1")


def test_archive_plan_is_readonly_and_replay_uses_only_verified_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root, status, log = _run(tmp_path)
    console = tmp_path / "console.txt"
    console.write_bytes(b"external parent console\n")
    destination = tmp_path / "archive"
    expected = owner.runtime_startup_report(
        repository, root, "profile", "run-1", source_sha="f" * 40, console_paths=[console],
    )
    plan = owner.runtime_startup_archive(
        repository, root, "profile", "run-1", destination=destination,
        source_sha="f" * 40, console_paths=[console],
    )
    assert plan["validate_only"] is True
    assert not destination.exists()
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("archive launched"))
    result = owner.runtime_startup_archive(
        repository, root, "profile", "run-1", destination=destination,
        source_sha="f" * 40, console_paths=[console], validate_only=False,
    )
    assert result["validate_only"] is False
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["diagnostic_only"] is True
    originals = {"status": status.read_bytes(), "log": log.read_bytes(), "console-1": console.read_bytes()}
    for entry in manifest["files"]:
        data = (destination / entry["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry["sha256"]
        if entry["role"] in originals:
            assert data == originals[entry["role"]]
    status.unlink()
    log.unlink()
    console.unlink()
    replay = owner.runtime_startup_report(repository, root, "profile", "run-1", archive=destination)
    assert replay == expected
    (destination / "child.log").write_bytes(b"changed")
    with pytest.raises(ValueError, match=r"hash|changed"):
        owner.runtime_startup_report(repository, root, "profile", "run-1", archive=destination)


def test_archive_never_overwrites_or_publishes_changed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root, _, log = _run(tmp_path)
    destination = tmp_path / "archive"
    destination.mkdir()
    with pytest.raises((ValueError, OSError)):
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1", destination=destination, validate_only=False,
        )
    destination.rmdir()
    original = owner._startup_fresh

    def changed(files: Any) -> None:
        log.write_bytes(b"tampered after snapshot")
        original(files)

    monkeypatch.setattr(owner, "_startup_fresh", changed)
    with pytest.raises(ValueError, match="changed"):
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1", destination=destination, validate_only=False,
        )
    assert not (destination / "manifest.json").exists()


def test_native_startup_plan_has_fixed_three_child_owner_without_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "missing-checkout"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("plan ran"))
    monkeypatch.setattr(owner, "stream_command", lambda *a, **k: pytest.fail("plan spawned"))
    plan = owner.runtime_startup_diagnostic(
        repository, repository / ".gate-logs/observed", "plan", "run-1",
        source_sha="f" * 40, environment=None, python="3.12.15", profile_set="ci",
        log_path=repository / ".gate-logs/plan.log",
    )
    assert plan["validate_only"] is True
    assert plan["command"] == ["make", "--no-print-directory", "_pytest-runtime-compatibility-admission"]
    assert plan["quiet_seconds"] == 60 and plan["max_seconds"] == 90
    assert plan["environment"]["PYTEST_ADDOPTS"] == "--collect-only -rs"
    assert not repository.exists()


def _make_fixture(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    repository, _, _, _ = _run(tmp_path)
    shim = repository / "uv_fixture.py"
    shim.write_text(
        "import os,sys\n"
        "assert sys.argv[1:3] == ['run','python'], sys.argv\n"
        "os.execv(sys.executable,[sys.executable,*sys.argv[3:]])\n"
    )
    (repository / "Makefile").write_text(
        f"UV := {sys.executable} {shim}\n"
        "OBSERVED_ROOT ?= .gate-logs/observed\n"
        "OBSERVED_HEARTBEAT_SECS ?= 10\nOBSERVED_RETAIN_RUNS ?= 20\n"
        f"include {ROOT / 'make/10-observability-and-tests.mk'}\n"
        "sync acquire-pinned-uv _diagnostic_tripwire:\n\t@exit 99\n"
    )
    environment = os.environ.copy()
    for name in ("MAKEFLAGS", "MAKEOVERRIDES", "MFLAGS", "GNUMAKEFLAGS", "MAKELEVEL"):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    return repository, environment


def test_real_make_startup_surfaces_are_registered_readonly_and_literal(
    tmp_path: Path,
) -> None:
    repository, environment = _make_fixture(tmp_path)
    destination = tmp_path / "archive"
    base = [
        "OBSERVED_LABEL=profile", "RUN_ID=run-1",
        "RUNTIME_DIAGNOSTIC_SOURCE_SHA=" + "f" * 40,
    ]
    for target, arguments in (
        ("runtime-startup-diagnostic", []),
        ("runtime-startup-report", []),
        ("runtime-startup-archive", [f"RUNTIME_DIAGNOSTIC_ARCHIVE={destination}"]),
    ):
        result = subprocess.run(
            ["make", "--no-print-directory", target, *base, *arguments],
            cwd=repository, env=environment, capture_output=True, text=True,
            timeout=5, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not destination.exists()
        assert "acquire" not in result.stdout
    contract = json.loads((ROOT / "config/make_target_contract.json").read_text())
    for target in ("runtime-startup-diagnostic", "runtime-startup-report", "runtime-startup-archive"):
        assert any(row["name"] == target for row in contract["targets"])
    tripwire = tmp_path / "literal-tripwire"
    for name in ("RUNTIME_DIAGNOSTIC_SOURCE_SHA", "RUNTIME_DIAGNOSTIC_VALIDATE_ONLY",
                 "OBSERVED_ROOT", "OBSERVED_LABEL", "RUN_ID", "LOG",
                 "RUNTIME_DIAGNOSTIC_ARCHIVE", "RUNTIME_DIAGNOSTIC_REPORT"):
        result = subprocess.run(
            ["make", "--no-print-directory", "runtime-startup-diagnostic", *base,
             f"{name}=$(shell touch {tripwire})"],
            cwd=repository, env=environment, capture_output=True, text=True, timeout=5, check=False,
        )
        assert not tripwire.exists(), result.stdout + result.stderr


def test_real_make_report_archive_and_replay_preserve_producer_bytes(tmp_path: Path) -> None:
    repository, environment = _make_fixture(tmp_path)
    status = repository / ".gate-logs/observed/profile/run-1.json"
    log = repository / ".gate-logs/imports.log"
    before = (status.read_bytes(), log.read_bytes())
    destination = tmp_path / "archive"
    report_path = tmp_path / "report.md"
    base = ["OBSERVED_LABEL=profile", "RUN_ID=run-1"]
    for target, arguments in (
        ("runtime-startup-report", [f"RUNTIME_DIAGNOSTIC_REPORT={report_path}"]),
        ("runtime-startup-archive", [f"RUNTIME_DIAGNOSTIC_ARCHIVE={destination}",
                                     "RUNTIME_DIAGNOSTIC_VALIDATE_ONLY=0"]),
    ):
        result = subprocess.run(
            ["make", "--no-print-directory", target, *base, *arguments],
            cwd=repository, env=environment, capture_output=True, text=True, timeout=5, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert before == (status.read_bytes(), log.read_bytes())
    status.unlink()
    log.unlink()
    result = subprocess.run(
        ["make", "--no-print-directory", "runtime-startup-report", *base,
         f"RUNTIME_DIAGNOSTIC_ARCHIVE={destination}"],
        cwd=repository, env=environment, capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == report_path.read_text()


def _apply_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    repository = tmp_path / "checkout"
    repository.mkdir()
    events: list[object] = []
    monkeypatch.setattr(owner, "_startup_source", lambda root: ("f" * 40, "tree"))
    monkeypatch.setattr(owner, "_startup_capacity", lambda root, env: events.append("capacity"))
    monkeypatch.setattr(owner, "_startup_runtime", lambda *args: {
        "state": "verified", "runtime": {"prefix": "/fixture/registered/python-3.12"},
        "input_sha256": {"uv.lock": "a" * 64},
    })
    monkeypatch.setattr(progress_owner, "progress_status", lambda root: None)

    def observe(*args: Any, **kwargs: Any) -> int:
        events.append((args, kwargs))
        return 0

    monkeypatch.setattr(owner, "stream_command", observe)
    return {
        "repository": repository, "root": repository / ".gate-logs/observed",
        "label": "profile", "run_id": "new", "source_sha": "f" * 40,
        "environment": None, "python": "3.12.15", "profile_set": "ci",
        "log_path": repository / ".gate-logs/new.log", "validate_only": False,
        "_events": events,
    }


def test_applied_startup_uses_existing_observer_once_with_fixed_child_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _apply_fixture(tmp_path, monkeypatch)
    events = values.pop("_events")
    monkeypatch.setenv("MAKEFLAGS", " -- GATE_TEST_PROFILE=native")
    monkeypatch.setenv("PYTEST_ADDOPTS", "-k omitted")
    result = owner.runtime_startup_diagnostic(**values)
    assert result["exit_code"] == 0
    assert events[0] == "capacity" and len(events) == 2
    args, options = events[1]
    assert args[0] == ("make", "--no-print-directory", "_pytest-runtime-compatibility-admission")
    assert options["max_seconds"] == 90 and options["quiet_seconds"] == 60
    assert options["environment"]["PYTEST_ADDOPTS"] == "--collect-only -rs"
    assert options["environment"]["PYTHONPROFILEIMPORTTIME"] == "1"
    assert options["environment"]["GATE_TEST_PROFILE"] == "release"
    assert options["environment"]["GATE_BATCH_WORKERS"] == "1"
    assert options["environment"]["UV_PROJECT_ENVIRONMENT"] == "/fixture/registered/python-3.12"
    assert "MAKEFLAGS" not in options["environment"]
    assert options["startup_metadata"]["source_before"] == "f" * 40
    assert options["startup_metadata"]["runtime_inspection"]["input_sha256"] == {"uv.lock": "a" * 64}


@pytest.mark.parametrize("failure", ["source", "occupied", "runtime", "prefix", "changed", "path"])
def test_applied_startup_refuses_prerequisites_before_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    values = _apply_fixture(tmp_path, monkeypatch)
    events = values.pop("_events")
    if failure == "source":
        monkeypatch.setattr(owner, "_startup_source", lambda root: ("a" * 40, "tree"))
    elif failure == "occupied":
        values["log_path"].parent.mkdir()
        values["log_path"].write_bytes(b"prior")
    elif failure == "runtime":
        monkeypatch.setattr(owner, "_startup_runtime", lambda *args: {"state": "planned"})
    elif failure == "prefix":
        monkeypatch.setattr(owner, "_startup_runtime", lambda *args: {"state": "verified"})
    elif failure == "changed":
        states = iter([("f" * 40, "tree"), ("f" * 40, "different")])
        monkeypatch.setattr(owner, "_startup_source", lambda root: next(states))
    else:
        values["log_path"] = values["repository"] / ".gate-logs/../outside.log"
    with pytest.raises(ValueError):
        owner.runtime_startup_diagnostic(**values)
    assert not any(isinstance(event, tuple) for event in events)


def test_native_capacity_uses_only_existing_bounded_make_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []
    def native(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        output = json.dumps({"resource_observability": {
            "worker_count": 0, "leased_worker_count": 0, "collection_lease": {"state": "available"},
        }}) if args[2] == "active-work-status" else "Verdict      : OK\n"
        return subprocess.CompletedProcess(args, 0, output, "")

    monkeypatch.setattr(subprocess, "run", native)
    owner._startup_capacity(tmp_path, {"UV_NO_SYNC": "1"})
    assert [call[0][2] for call in calls] == ["ps", "active-work-status", "check-system-load", "disk-guard"]
    assert all(call[1]["check"] is True and call[1]["timeout"] == 120 for call in calls)
    assert "DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0" in calls[-1][0]
    assert "DISK_CLEANUP_PREFLIGHT_DRY_RUN=0" in calls[-1][0]


@pytest.mark.parametrize("mutation", ["role", "path", "identity", "missing", "symlink"])
def test_archive_replay_refuses_metadata_and_path_tampering(
    tmp_path: Path, mutation: str,
) -> None:
    repository, root, _, _ = _run(tmp_path)
    destination = tmp_path / "archive"
    owner.runtime_startup_archive(
        repository, root, "profile", "run-1", destination=destination, validate_only=False,
    )
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if mutation == "role":
        manifest["files"].append(manifest["files"][0])
    elif mutation == "path":
        manifest["files"][0]["path"] = "../outside"
    elif mutation == "identity":
        manifest["run_id"] = "other"
    elif mutation == "missing":
        manifest["files"] = [row for row in manifest["files"] if row["role"] != "log"]
    else:
        original = destination / "child.log"
        original.unlink()
        original.symlink_to(tmp_path / "missing")
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, OSError)):
        owner.runtime_startup_report(repository, root, "profile", "run-1", archive=destination)


def test_native_observer_records_startup_metadata_only_when_requested(tmp_path: Path) -> None:
    root = tmp_path / "observed"
    metadata = {"source_before": "f" * 40, "command": ["fixture"], "diagnostic_only": True}
    assert owner.stream_command(
        [sys.executable, "-c", "print('fixture-only')"], tmp_path / "child.log",
        observed_root=root, label="fixture", run_id="once", startup_metadata=metadata,
    ) == 0
    value = json.loads((root / "fixture/once.json").read_text())
    assert value["startup_diagnostic"] == metadata


@pytest.mark.parametrize("output", [
    '{"resource_observability":{"worker_count":1,"leased_worker_count":0,"collection_lease":{"state":"available"}}}',
    '{"resource_observability":{"worker_count":0,"leased_worker_count":0,"collection_lease":{"state":"busy"}}}',
    '{}',
])
def test_native_startup_refuses_busy_or_unknown_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str,
) -> None:
    def native(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, output if args[2] == "active-work-status" else "", "")
    monkeypatch.setattr(subprocess, "run", native)
    with pytest.raises(ValueError, match="capacity"):
        owner._startup_capacity(tmp_path, {})


def test_native_startup_runtime_reuses_existing_inspector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if TYPE_CHECKING:
        import dependency_profiles
    else:
        from scripts import dependency_profiles
    token = object()
    monkeypatch.setattr(dependency_profiles, "load_catalog", lambda root: token)
    calls: list[Any] = []

    def inspect(catalog: Any, **kwargs: Any) -> dict[str, object]:
        calls.append((catalog, kwargs))
        return {"state": "verified"}

    monkeypatch.setattr(dependency_profiles, "inspect_runtime", inspect)
    assert owner._startup_runtime(tmp_path, None, "3.12.15", "ci") == {"state": "verified"}
    assert calls == [(token, {"profile_set": "ci", "environment": None, "python": "3.12.15"})]


def test_archive_keeps_partial_copy_unpublished_when_source_changes_after_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, root, _, log = _run(tmp_path)
    destination = tmp_path / "archive"
    original = owner._startup_fresh
    checks = 0

    def fresh(files: Any) -> None:
        nonlocal checks
        checks += 1
        if checks == 3:
            assert (destination / "child.log").exists()
            log.write_bytes(b"changed after copies")
        original(files)

    monkeypatch.setattr(owner, "_startup_fresh", fresh)
    with pytest.raises(ValueError, match="changed"):
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1", destination=destination, validate_only=False,
        )
    assert not (destination / "manifest.json").exists()


def test_trace_is_copied_verbatim_and_oversized_input_refuses(tmp_path: Path) -> None:
    repository, root, status, log = _run(tmp_path)
    trace = repository / ".gate-logs/trace.jsonl"
    trace.write_bytes(b'{"kind":"fixture"}\n')
    value = json.loads(status.read_text())
    value["trace_path"] = str(trace)
    status.write_text(json.dumps(value))
    destination = tmp_path / "archive"
    owner.runtime_startup_archive(
        repository, root, "profile", "run-1", destination=destination, validate_only=False,
    )
    assert (destination / "trace.raw").read_bytes() == trace.read_bytes()
    assert "trace: present" in (destination / "report.md").read_text()
    with log.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="bounded"):
        owner.runtime_startup_report(repository, root, "profile", "run-1")


@pytest.mark.parametrize("value", ["current", "../escape"])
def test_report_output_identity_refuses_before_any_read(
    tmp_path: Path, value: str,
) -> None:
    with pytest.raises(ValueError):
        owner.runtime_startup_report(tmp_path, tmp_path, "profile", value)


@pytest.mark.parametrize("action,key,value", [
    ("run", "VALIDATE_ONLY", "invalid"),
    ("run", "CONSOLE", "{}"),
    ("archive", "ARCHIVE", ""),
    ("run", "SOURCE_SHA", "not-a-sha"),
])
def test_startup_cli_refuses_invalid_literal_inputs_without_work(
    monkeypatch: pytest.MonkeyPatch, action: str, key: str, value: str,
) -> None:
    monkeypatch.setenv("GLUDD_RUNTIME_DIAGNOSTIC_LABEL", "profile")
    monkeypatch.setenv("GLUDD_RUNTIME_DIAGNOSTIC_RUN_ID", "literal-only")
    monkeypatch.setenv("GLUDD_RUNTIME_DIAGNOSTIC_SOURCE_SHA", "f" * 40)
    monkeypatch.setenv("GLUDD_RUNTIME_DIAGNOSTIC_" + key, value)
    monkeypatch.setattr(owner, "stream_command", lambda *a, **k: pytest.fail("invalid input launched"))
    assert owner.main(["--runtime-startup", action]) == 2


@pytest.mark.parametrize("label", ["CRITICAL", "ERROR reading load/CPU"])
def test_native_startup_refuses_load_failure_without_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str,
) -> None:
    def native(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        output = json.dumps({"resource_observability": {
            "worker_count": 0, "leased_worker_count": 0, "collection_lease": {"state": "available"},
        }}) if args[2] == "active-work-status" else f"Verdict      : {label}\n"
        return subprocess.CompletedProcess(args, 0, output, "")
    monkeypatch.setattr(subprocess, "run", native)
    with pytest.raises(ValueError, match="load"):
        owner._startup_capacity(tmp_path, {})
