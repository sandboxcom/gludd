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


@pytest.mark.parametrize(
    ("row_text", "expected"),
    [
        ("import time: 0 | 0 | builtin", (0, 0, "builtin")),
        ("import time:        23 |         34 |   package.module", (23, 34, "package.module")),
        ("import time:\t11\t|\t34\t|\tpackage.module  ", (11, 34, "package.module")),
    ],
)
def test_canonical_importtime_row_preserves_numeric_columns(
    row_text: str, expected: tuple[int, int, str],
) -> None:
    """Decode native self-first microseconds independently of stream framing."""
    assert owner.parse_importtime_row(row_text) == expected


@pytest.mark.parametrize("row_text", [
    "",
    "import time: self [us] | cumulative | imported package",
    "23 34 package.module",
    '{"name": "package.module", "self": 23, "cumulative": 34}',
    "import time: -1 | 34 | package.module",
    "import time: 1 | -34 | package.module",
    "import time: 23.0 | 34 | package.module",
    "import time: 23 | 22 | package.module",
    "import time: cached | cached | package.module",
    "import time: 23 | 34 |",
    "import time: 23 | 34 |   ",
    "import time: 23 | missing | package.module",
])
def test_canonical_importtime_row_refuses_invalid_measurement(row_text: str) -> None:
    """Missing, cached, invented and malformed cells never become numeric evidence."""
    with pytest.raises(ValueError, match="malformed importtime"):
        owner.parse_importtime_row(row_text)


def test_startup_segments_use_canonical_importtime_row_decoder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the original framing owner and delegate each admitted row exactly once."""
    decode = owner.parse_importtime_row
    calls: list[str] = []

    def record(line: str) -> tuple[int, int, str]:
        calls.append(line)
        return decode(line)

    monkeypatch.setattr(owner, "parse_importtime_row", record)
    segments = owner._startup_imports(_segments().encode())
    assert len(segments) == 3
    assert calls == [
        line for line in _segments().splitlines()
        if line.startswith("import time:") and not line.startswith("import time: self")
    ]
    assert [segment["rows"] for segment in segments] == [
        [(11, 11, "nested"), (23, 34, "parent")],
    ] * 3


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
        "bytes_written": log.stat().st_size, "lines_written": log.read_bytes().count(b"\n"),
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
    value["lines_written"] = log.read_bytes().count(b"\n")
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
        f"LOG={repository / '.gate-logs/imports.log'}",
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


_READER_FIXTURE = r"""
import json, os, sys
from pathlib import Path
from scripts import stream_command as owner

request = json.loads(sys.argv[1])
repository = Path(request["repository"])
root = repository / ".gate-logs/observed"
archive = Path(request["archive"]) if request.get("archive") else None
status = (archive / "status/profile/run-1.json" if archive else root / "profile/run-1.json")
replacement = request.get("replacement")
if replacement == "open-fifo":
    native_open = os.open
    swapped = False
    def descriptor_open(name, flags, *args, **kwargs):
        global swapped
        if name == status.name and kwargs.get("dir_fd") is not None and not swapped:
            swapped = True
            status.unlink()
            os.mkfifo(status)
        return native_open(name, flags, *args, **kwargs)
    os.open = descriptor_open
elif replacement:
    native_read = owner._startup_read
    replaced = False
    def read(role, path, boundary):
        global replaced
        item = native_read(role, path, boundary)
        if role == "status" and not replaced:
            replaced = True
            path.unlink()
            if replacement == "fifo":
                os.mkfifo(path)
            else:
                with path.open("wb") as handle:
                    handle.truncate(owner._STARTUP_LIMIT + 1)
        return item
    owner._startup_read = read
    if replacement == "oversized":
        native_text = Path.read_text
        def text(path, *args, **kwargs):
            if path == status:
                raise AssertionError("unbounded status reopen after snapshot")
            return native_text(path, *args, **kwargs)
        Path.read_text = text

def forbidden(*args, **kwargs):
    raise AssertionError("reader launched a process")
owner.subprocess.run = forbidden
owner.subprocess.Popen = forbidden
try:
    if request.get("apply_archive"):
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1",
            destination=Path(request["destination"]), validate_only=False,
        )
    else:
        owner.runtime_startup_report(
            repository, root, "profile", "run-1", archive=archive,
            console_paths=[Path(path) for path in request.get("console", [])],
        )
except (OSError, ValueError):
    print("refused without launch", flush=True)
else:
    raise AssertionError("unsafe input accepted")
"""


def _bounded_reader_refusal(request: dict[str, Any]) -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
    process = subprocess.Popen(
        [sys.executable, "-B", "-c", _READER_FIXTURE, json.dumps(request)],
        cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=2)
    assert process.poll() is not None
    assert not timed_out, "bounded reader fixture blocked on an owned no-writer FIFO"
    assert process.returncode == 0, stdout + stderr
    assert stdout.strip() == "refused without launch", stderr


@pytest.mark.parametrize("role", [
    "status", "log", "trace", "console", "manifest", "archive-status", "archive-log",
    "archive-report", "status-archive",
])
def test_real_fifo_read_roles_refuse_without_launch_or_completed_output(
    tmp_path: Path, role: str,
) -> None:
    repository, root, status, log = _run(tmp_path)
    request: dict[str, Any] = {"repository": str(repository)}
    destination = tmp_path / "new-output"
    request["destination"] = str(destination)
    target = status if role in {"status", "status-archive"} else log
    if role == "trace":
        target = repository / ".gate-logs/trace.jsonl"
        value = json.loads(status.read_text())
        value["trace_path"] = str(target)
        status.write_text(json.dumps(value))
    elif role == "console":
        target = tmp_path / "console"
        request["console"] = [str(target)]
    elif role in {"manifest", "archive-status", "archive-log", "archive-report"}:
        archive = tmp_path / "existing-archive"
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1", destination=archive, validate_only=False,
        )
        request["archive"] = str(archive)
        relative = {
            "manifest": "manifest.json", "archive-status": "status/profile/run-1.json",
            "archive-log": "child.log", "archive-report": "report.md",
        }[role]
        target = archive / relative
    if role == "status-archive":
        request["apply_archive"] = True
    preserved = {path: path.read_bytes() for path in (status, log) if path != target}
    if target.exists():
        target.unlink()
    os.mkfifo(target)
    _bounded_reader_refusal(request)
    assert not destination.exists()
    assert all(path.read_bytes() == data for path, data in preserved.items())


@pytest.mark.parametrize("archived", [False, True])
@pytest.mark.parametrize("replacement", ["fifo", "oversized"])
def test_status_snapshot_replacement_never_reopens_an_unbounded_path(
    tmp_path: Path, archived: bool, replacement: str,
) -> None:
    repository, root, _, _ = _run(tmp_path)
    request: dict[str, Any] = {"repository": str(repository), "replacement": replacement}
    if archived:
        archive = tmp_path / "archive"
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1", destination=archive, validate_only=False,
        )
        request["archive"] = str(archive)
    _bounded_reader_refusal(request)


@pytest.mark.parametrize("archived", [False, True])
def test_status_identity_uses_only_the_captured_native_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archived: bool,
) -> None:
    repository, root, _, _ = _run(tmp_path)
    archive = tmp_path / "archive" if archived else None
    if archive is not None:
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1", destination=archive, validate_only=False,
        )
    monkeypatch.setattr(owner, "_load_status", lambda *a, **k: pytest.fail("status path reopened"))
    report = owner.runtime_startup_report(repository, root, "profile", "run-1", archive=archive)
    assert "1897 collected / 11.22 seconds" in report


def test_archive_status_role_requires_the_canonical_manifest_path(tmp_path: Path) -> None:
    repository, root, _, _ = _run(tmp_path)
    archive = tmp_path / "archive"
    owner.runtime_startup_archive(
        repository, root, "profile", "run-1", destination=archive, validate_only=False,
    )
    manifest = json.loads((archive / "manifest.json").read_text())
    row = next(row for row in manifest["files"] if row["role"] == "status")
    (archive / "alternate.json").write_bytes((archive / row["path"]).read_bytes())
    row["path"] = "alternate.json"
    (archive / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="canonical"):
        owner.runtime_startup_report(repository, root, "profile", "run-1", archive=archive)


@pytest.mark.parametrize("swap", ["final-link", "ancestor-link", "final-regular"])
def test_read_open_boundary_never_consumes_swapped_foreign_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, swap: str,
) -> None:
    directory = tmp_path / "owned"
    foreign = tmp_path / "foreign"
    directory.mkdir()
    foreign.mkdir()
    path = directory / "input"
    path.write_bytes(b"owned bytes")
    (foreign / "input").write_bytes(b"foreign bytes")
    native_open = os.open
    native_path_open = Path.open
    native_read = os.read
    opened: list[int] = []
    swapped = False

    def exchange() -> None:
        nonlocal swapped
        if swapped:
            return
        swapped = True
        if swap == "ancestor-link":
            directory.rename(tmp_path / "retained-owned")
            directory.symlink_to(foreign, target_is_directory=True)
        else:
            path.unlink()
            if swap == "final-link":
                path.symlink_to(foreign / "input")
            else:
                path.write_bytes(b"foreign replacement")

    def descriptor_open(name: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if name == "input":
            exchange()
        fd = native_open(name, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    def buffered_open(selected: Path, *args: Any, **kwargs: Any) -> Any:
        if selected == path and args and args[0] == "rb":
            exchange()
            pytest.fail("buffered path open consumed the swapped input")
        return native_path_open(selected, *args, **kwargs)

    def descriptor_read(fd: int, size: int) -> bytes:
        info = os.fstat(fd)
        assert (info.st_dev, info.st_ino) != (
            (foreign / "input").stat().st_dev, (foreign / "input").stat().st_ino,
        )
        return native_read(fd, size)

    monkeypatch.setattr(os, "open", descriptor_open)
    monkeypatch.setattr(os, "read", descriptor_read)
    monkeypatch.setattr(Path, "open", buffered_open)
    with pytest.raises((ValueError, OSError)):
        owner._startup_read("fixture", path, tmp_path)
    assert swapped
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("fail_read", [False, True])
def test_startup_read_closes_every_descriptor_on_success_or_read_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_read: bool,
) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"owned bytes")
    native_open = os.open
    native_read = os.read
    opened: list[int] = []

    def descriptor_open(*args: Any, **kwargs: Any) -> int:
        fd = native_open(*args, **kwargs)
        opened.append(fd)
        return fd

    def descriptor_read(*args: Any, **kwargs: Any) -> bytes:
        if fail_read:
            raise OSError("fixture descriptor read failure")
        return native_read(*args, **kwargs)

    monkeypatch.setattr(os, "open", descriptor_open)
    monkeypatch.setattr(os, "read", descriptor_read)
    if fail_read:
        with pytest.raises(OSError, match="fixture descriptor"):
            owner._startup_read("fixture", path, tmp_path)
    else:
        assert owner._startup_read("fixture", path, tmp_path).data == b"owned bytes"
    assert opened, "regular-file admission must use verified native descriptors"
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("archived", [False, True])
def test_fifo_replacement_at_descriptor_open_is_nonblocking_and_refused(
    tmp_path: Path, archived: bool,
) -> None:
    repository, root, _, _ = _run(tmp_path)
    request: dict[str, Any] = {"repository": str(repository), "replacement": "open-fifo"}
    if archived:
        archive = tmp_path / "archive"
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1", destination=archive, validate_only=False,
        )
        request["archive"] = str(archive)
    _bounded_reader_refusal(request)


def test_startup_reader_accepts_exact_16mib_limit_and_fixed_tmp_alias(tmp_path: Path) -> None:
    path = tmp_path / "exact-limit"
    with path.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024)
    result = owner._startup_read("fixture", path, tmp_path)
    assert len(result.data) == 16 * 1024 * 1024
    # Native pytest basetemp is rooted in /tmp; exercise the platform-owned alias.
    actual = path.resolve()
    alias = Path("/tmp") / actual.relative_to(Path("/tmp").resolve())
    assert owner._startup_read("fixture", alias, Path("/tmp")).data == result.data


def test_startup_reader_never_falls_back_when_safe_descriptors_are_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"owned")
    monkeypatch.setattr(os, "supports_dir_fd", set())
    monkeypatch.setattr(os, "open", lambda *a, **k: pytest.fail("unsafe fallback open"))
    with pytest.raises(ValueError, match="unsupported"):
        owner._startup_read("fixture", path, tmp_path)


def test_startup_reader_refuses_contents_changed_during_descriptor_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "input"
    path.write_bytes(b"owned")
    native_read = os.read
    changed = False

    def mutate(fd: int, size: int) -> bytes:
        nonlocal changed
        data = native_read(fd, size)
        if data and not changed:
            changed = True
            with path.open("ab") as handle:
                handle.write(b"changed")
        return data

    monkeypatch.setattr(os, "read", mutate)
    with pytest.raises(ValueError, match="changed"):
        owner._startup_read("fixture", path, tmp_path)
    assert changed


def test_startup_path_refuses_unknown_tmp_alias_and_relative_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_link = Path.is_symlink
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == Path("/tmp") or native_link(path))
    monkeypatch.setattr(os, "readlink", lambda path: "/foreign")
    with pytest.raises(ValueError, match="unknown"):
        owner._startup_file_path(Path("/tmp/input"))
    with pytest.raises(ValueError, match="absolute confined"):
        owner._startup_file_path(Path("../input"))


def _native_timeout_archive_inputs(
    tmp_path: Path, payload: bytes,
) -> tuple[Path, Path, Path, Path]:
    """Produce owned terminal inputs through the real observer and Python child."""
    repository = tmp_path / "native-checkout"
    repository.mkdir()
    root = repository / ".gate-logs/observed"
    log = repository / ".gate-logs/partial-output.log"
    assert Path(owner.__file__).resolve() == ROOT / "scripts/stream_command.py"
    code = owner.stream_command(
        [
            sys.executable, "-P", "-u", "-W", "error", "-c",
            f"import os,time; os.write(1, {payload!r}); time.sleep(5)",
        ],
        log, observed_root=root, label="partial-output", run_id="native-timeout",
        heartbeat_seconds=0, quiet_seconds=0, max_seconds=0.5,
        termination_grace_seconds=0.05, quiet=True,
        environment=os.environ.copy(), cwd=repository,
    )
    status = root / "partial-output/native-timeout.json"
    value = json.loads(status.read_text())
    assert code == 124
    assert value["state"] == "timed_out" and value["exit_code"] == 124
    assert value["termination_reason"] == "max-runtime-timeout"
    assert log.read_bytes() == payload
    assert value["bytes_written"] == len(payload)
    assert value["lines_written"] == payload.count(b"\n")
    return repository, root, status, log


@pytest.mark.parametrize("payload", [
    b"complete\n",
    b"complete\nunterminated",
    b"progress\rstill unfinished",
    b"complete\r\nunterminated",
    b"",
])
def test_native_timeout_archive_preserves_complete_lf_record_contract(
    tmp_path: Path, payload: bytes,
) -> None:
    """A valid timeout envelope preserves every byte without inventing a final LF."""
    repository, root, status, log = _native_timeout_archive_inputs(tmp_path, payload)
    before = {"status": status.read_bytes(), "log": log.read_bytes()}
    destination = tmp_path / "archive"
    result = owner.runtime_startup_archive(
        repository, root, "partial-output", "native-timeout",
        destination=destination, validate_only=False,
    )
    assert result["validate_only"] is False
    assert before == {"status": status.read_bytes(), "log": log.read_bytes()}
    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["diagnostic_only"] is True
    assert manifest["label"] == "partial-output" and manifest["run_id"] == "native-timeout"
    for entry in manifest["files"]:
        archived = (destination / entry["path"]).read_bytes()
        assert len(archived) == entry["size"]
        assert hashlib.sha256(archived).hexdigest() == entry["sha256"]
        if entry["role"] in before:
            assert archived == before[entry["role"]]
    replay = owner.runtime_startup_report(
        repository, root, "partial-output", "native-timeout", archive=destination,
    )
    assert "state: timed_out; rc: 124" in replay
    assert "Diagnostic only; never admission or release evidence." in replay


@pytest.mark.parametrize("mutation", [
    "visual-line-count", "extra-lf-count", "byte-count", "appended-byte",
    "bool-lf-counter", "float-lf-counter", "bool-byte-counter", "float-byte-counter",
])
def test_native_timeout_archive_refuses_forged_counters_or_appended_bytes(
    tmp_path: Path, mutation: str,
) -> None:
    """Native fixture tampering must fail strict validation before publication."""
    payload = b"" if mutation in {"bool-byte-counter", "float-byte-counter"} else b"unterminated"
    repository, root, status, log = _native_timeout_archive_inputs(tmp_path, payload)
    value = json.loads(status.read_text())
    if mutation == "visual-line-count":
        value["lines_written"] = len(log.read_bytes().splitlines())
    elif mutation == "extra-lf-count":
        value["lines_written"] += 2
    elif mutation == "byte-count":
        value["bytes_written"] += 1
    elif mutation == "bool-lf-counter":
        value["lines_written"] = False
    elif mutation == "float-lf-counter":
        value["lines_written"] = float(value["lines_written"])
    elif mutation == "bool-byte-counter":
        value["bytes_written"] = False
    elif mutation == "float-byte-counter":
        value["bytes_written"] = float(value["bytes_written"])
    else:
        log.write_bytes(log.read_bytes() + b"\n")
    if mutation != "appended-byte":
        status.write_text(json.dumps(value) + "\n")
    destination = tmp_path / "rejected-archive"
    with pytest.raises(ValueError, match="startup log size/count"):
        owner.runtime_startup_archive(
            repository, root, "partial-output", "native-timeout",
            destination=destination, validate_only=False,
        )
    assert not destination.exists()


def test_archive_unit_inputs_require_exact_integer_lf_counters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check owned unit inputs; native producer controls remain independent."""
    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("archive input validation launched a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    repository, root, status, log = _run(tmp_path)
    value = json.loads(status.read_text())
    value.update(state="timed_out", exit_code=124)
    for index, payload in enumerate((
        b"complete\n", b"complete\nunterminated", b"progress\rstill unfinished",
        b"complete\r\nunterminated", b"x",
    )):
        log.write_bytes(payload)
        value.update(bytes_written=len(payload), lines_written=payload.count(b"\n"))
        status.write_text(json.dumps(value))
        before = (status.read_bytes(), log.read_bytes())
        destination = tmp_path / f"accepted-{index}"
        result = owner.runtime_startup_archive(
            repository, root, "profile", "run-1",
            destination=destination, validate_only=False,
        )
        assert result["validate_only"] is False
        assert json.loads((destination / "manifest.json").read_text())["diagnostic_only"] is True
        assert (destination / "child.log").read_bytes() == payload
        assert before == (status.read_bytes(), log.read_bytes())

    # The final one-byte/zero-LF fixture makes bool and float equality active.
    baseline = value.copy()
    for name, key, counter in (
        ("visual", "lines_written", 1), ("extra-lf", "lines_written", 2),
        ("extra-byte", "bytes_written", 2), ("bool-lf", "lines_written", False),
        ("float-lf", "lines_written", 0.0), ("bool-byte", "bytes_written", True),
        ("float-byte", "bytes_written", 1.0),
    ):
        status.write_text(json.dumps({**baseline, key: counter}))
        before = (status.read_bytes(), log.read_bytes())
        destination = tmp_path / f"refused-{name}"
        with pytest.raises(ValueError, match="startup log size/count"):
            owner.runtime_startup_archive(
                repository, root, "profile", "run-1",
                destination=destination, validate_only=False,
            )
        assert not destination.exists()
        assert before == (status.read_bytes(), log.read_bytes())
    status.write_text(json.dumps(baseline))
    log.write_bytes(b"x\n")
    destination = tmp_path / "refused-appended-byte"
    before = (status.read_bytes(), log.read_bytes())
    with pytest.raises(ValueError, match="startup log size/count"):
        owner.runtime_startup_archive(
            repository, root, "profile", "run-1",
            destination=destination, validate_only=False,
        )
    assert not destination.exists()
    assert before == (status.read_bytes(), log.read_bytes())
