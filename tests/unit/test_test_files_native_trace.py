"""Actual pytest trace loading through the existing native Make owners."""

from __future__ import annotations

import json
import os
import pstats
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[2]
FROZEN = PROJECT
MAKE_SOURCE = PROJECT / "make/10-observability-and-tests.mk"
FOUNDATION_SOURCE = PROJECT / "make/00-foundation.mk"
TRACE_KEYS = {"GLUDD_XDIST_TRACE_LOG", "GLUDD_XDIST_TRACE_RUN_ID", "GLUDD_XDIST_TRACE_TRUNCATE"}
RECURSION = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"}


@dataclass(frozen=True)
class Fixture:
    root: Path
    makefile: Path
    observed: Path
    environment: dict[str, str] = field(repr=False)


def _mapping(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    result: dict[str, object] = {}
    for key, member in value.items():
        assert isinstance(key, str)
        result[key] = member
    return result


def _read_json(path: Path) -> dict[str, object]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    return _mapping(value)


def _selection(source: str, marker: str, end: str) -> str:
    assert source.count(marker) == 1
    return marker + source.split(marker, 1)[1].split(end, 1)[0]


def _fixture(tmp_path: Path) -> Fixture:
    root = tmp_path.resolve()
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    for name in (
        "xdist_trace_plugin.py", "stream_command.py", "item_progress.py",
        "gate_status_attestation.py", "task_scope.py", "validate_task_ledger.py",
        "summarize_xdist_trace.py",
    ):
        shutil.copy2(FROZEN / "scripts" / name, scripts / name)
    shutil.copy2(FROZEN / "pyproject.toml", root / "pyproject.toml")
    for name in ("home", "process-tmp", "resources"):
        (root / name).mkdir()
    environment = {
        key: value for key, value in os.environ.items()
        if key not in TRACE_KEYS | RECURSION | {
            "_TEST_FILES_CPROFILE", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
            "RUN_WATCHED_PYTEST_TRACE", "CMD",
        }
    }
    environment.update(
        HOME=str(root / "home"), TMPDIR=str(root / "process-tmp"),
        GLUDD_RESOURCE_ROOT=str(root / "resources"),
        UV_PROJECT_ENVIRONMENT=sys.prefix, VIRTUAL_ENV=sys.prefix,
        UV_NO_SYNC="1", UV_PYTHON=str(sys.version_info.major) + "." + str(sys.version_info.minor),
        UV_PYTHON_DOWNLOADS="never", PYTHONPATH=str(root),
    )
    uv = shutil.which("uv")
    assert uv is not None, "use the installed UV; never install/download in this fixture"
    spy = root / "uv_spy.py"
    spy.write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        + f"with Path({str(root / 'uv.jsonl')!r}).open('a', encoding='utf-8') as stream:\n"
        + "    stream.write(json.dumps({'argv':sys.argv[1:]}) + '\\n')\n"
        + f"os.execv({uv!r}, [{uv!r}, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    conftest = (
        "import json, os, sys\nfrom pathlib import Path\n"
        "def pytest_sessionstart(session):\n"
        "    config = session.config\n"
        f"    assert config.rootpath.resolve() == Path({str(root)!r})\n"
        f"    assert Path(config.getoption('confcutdir')).resolve() == Path({str(root)!r})\n"
        f"    assert config.inipath.resolve() == Path({str(FROZEN / 'pyproject.toml')!r})\n"
        "    assert '_TEST_FILES_CPROFILE' not in os.environ\n"
        "    payload = {'argv':list(config.invocation_params.args), 'pid':os.getpid(),\n"
        "               'prefix':sys.prefix, 'version':list(sys.version_info[:2]),\n"
        "               'rootdir':str(config.rootpath.resolve()),\n"
        "               'confcutdir':str(Path(config.getoption('confcutdir')).resolve()),\n"
        "               'plugin':config.pluginmanager.hasplugin('scripts.xdist_trace_plugin'),\n"
        "               'trace':os.environ.get('GLUDD_XDIST_TRACE_LOG'),\n"
        "               'run_id':os.environ.get('GLUDD_XDIST_TRACE_RUN_ID')}\n"
        f"    record = Path({str(root)!r}, 'config-' + str(os.getpid()) + '.json')\n"
        "    record.write_text(json.dumps(payload), encoding='utf-8')\n"
    )
    (root / "conftest.py").write_text(conftest, encoding="utf-8")
    (root / "test_probe.py").write_text(
        "import os\nimport pytest\n"
        "def native_work():\n    return sum(range(20))\n"
        "@pytest.mark.parametrize('probe_number',[0,1])\n"
        "def test_probe(probe_number):\n"
        "    assert native_work() == 190\n"
        "    fail = os.environ.get('GLUDD_TRACE_LOAD_FAIL') == '1'\n"
        "    assert not (fail and probe_number == 0), 'native-fixture-failure'\n",
        encoding="utf-8",
    )
    source = MAKE_SOURCE.read_text(encoding="utf-8")
    files = _selection(source, "test-files-cprofile: _TEST_FILES_CPROFILE := 1\n", "\ncoverage-files:")
    watched = _selection(source, "run-watched:\n", "\n\ntest-integration:")
    summary = _selection(source, "export GLUDD_TRACE_SUMMARY_LOG\n", "\ntest-count-e2e:")
    makefile = root / "fixture.mk"
    foundation = FOUNDATION_SOURCE.read_text(encoding="utf-8")
    assert foundation.count("# This one-shot mode is selected") == 1
    early = foundation.split("# This one-shot mode is selected", 1)[0]
    makefile.write_text(
        early + "\n.PHONY: test-files test-files-cprofile run-watched test-xdist-trace-summary\n"
        "SHELL := /bin/sh\n"
        "RUN_WATCHED_PYTEST_TRACE ?= 0\n"
        "override UV := " + shlex.join([sys.executable, str(spy)]) + "\n"
        + files + "\n" + watched + "\n" + summary,
        encoding="utf-8",
    )
    return Fixture(root, makefile, root / ".gate-logs/observed", environment)


def _run(command: list[str], fixture: Fixture, *, environment: dict[str, str] | None = None,
         cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    print("TRACE-LOAD child=" + shlex.join(command), flush=True)
    result = subprocess.run(
        command, cwd=cwd or fixture.root, env=environment or fixture.environment,
        capture_output=True, text=True, check=False, timeout=35,
    )
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", file=sys.stderr, flush=True)
    return result


def _pytest_arguments(fixture: Fixture, workers: int) -> str:
    return shlex.join([
        "-q", "-W", "error", "-n", str(workers), "--max-worker-restart=0",
        "-c", str(FROZEN / "pyproject.toml"),
        "--rootdir=" + str(fixture.root), "--confcutdir=" + str(fixture.root),
        "-p", "no:cacheprovider",
    ])


def _test_files_command(fixture: Fixture, *, target: str = "test-files", workers: int = 0) -> list[str]:
    return [
        "make", "--no-print-directory", "-f", str(fixture.makefile), target,
        "TESTFILES=" + str(fixture.root / "test_probe.py"),
        "PYTEST_ARGS=" + _pytest_arguments(fixture, workers), "_XD=",
        "ID=",
    ]


def _observe(fixture: Fixture, *, enabled: bool, workers: int = 0,
             target: str = "test-files") -> subprocess.CompletedProcess[str]:
    result = _run([
        "make", "--no-print-directory", "-f", str(fixture.makefile), "run-watched",
        "CMD=" + shlex.join(_test_files_command(fixture, target=target, workers=workers)),
        "OBSERVED_ROOT=" + str(fixture.observed), "OBSERVED_LABEL=pytest", "RUN_ID=probe",
        "LOG=" + str(fixture.observed / "pytest/probe.log"), "WORK_ITEM_ID=",
        "OBSERVED_HEARTBEAT_SECS=0.1", "OBSERVED_RETAIN_RUNS=2",
        "STALL_SECS=60", "MAX_SECS=90",
        "RUN_WATCHED_PYTEST_TRACE=" + ("1" if enabled else "0"),
    ], fixture)
    return result


def _assert_configs(fixture: Fixture, *, loaded: bool) -> None:
    paths = list(fixture.root.glob("config-*.json"))
    assert paths
    for path in paths:
        snapshot = _read_json(path)
        assert snapshot["plugin"] is loaded
        assert snapshot["rootdir"] == snapshot["confcutdir"] == str(fixture.root)
        assert snapshot["prefix"] == sys.prefix
        assert snapshot["version"] == list(sys.version_info[:2])
    calls: list[list[str]] = []
    for line in (fixture.root / "uv.jsonl").read_text(encoding="utf-8").splitlines():
        raw = _mapping(json.loads(line))["argv"]
        assert isinstance(raw, list)
        arguments: list[str] = []
        for argument in raw:
            assert isinstance(argument, str)
            arguments.append(argument)
        if arguments[:4] == ["run", "python", "-m", "pytest"]:
            calls.append(arguments)
    assert len(calls) == 1
    invocation = calls[0]
    assert invocation.count(str(fixture.root / "test_probe.py")) == 1
    assert invocation.count("scripts.xdist_trace_plugin") == int(loaded)
    if loaded:
        assert invocation[-2:] == ["-p", "scripts.xdist_trace_plugin"]
    assert "-W" in invocation and "error" in invocation
    assert "--rootdir=" + str(fixture.root) in invocation
    assert "--confcutdir=" + str(fixture.root) in invocation
    basetemps = [Path(argument.removeprefix("--basetemp=")) for argument in invocation
                 if argument.startswith("--basetemp=")]
    assert len(basetemps) == 1
    basetemp = basetemps[0]
    assert basetemp.parent == Path("/tmp")
    assert basetemp.name.removeprefix("gludd-testfiles-").isdigit()
    assert basetemp.name.startswith("gludd-testfiles-")
    assert not basetemp.exists()


def _summary(fixture: Fixture, trace: Path, run_id: str) -> dict[str, object]:
    result = _run([
        "make", "--no-print-directory", "-f", str(fixture.makefile),
        "test-xdist-trace-summary", "LOG=" + str(trace), "RUN_ID=" + run_id,
        "TRACE_SUMMARY_TIMINGS=1", "TRACE_SUMMARY_MEMORY=0",
    ], fixture)
    assert result.returncode == 0, result.stdout + result.stderr
    return _mapping(json.loads(result.stdout))


def _assert_pairs(fixture: Fixture, trace: Path, run_id: str, workers: int = 0) -> None:
    assert trace.stat().st_size > 0
    summary = _summary(fixture, trace, run_id)
    assert isinstance(summary["events"], int) and summary["events"] > 0
    timing = _mapping(summary["timings"])
    assert timing["paired_count"] == 2
    assert timing["issue_count"] == 0
    events = [_mapping(json.loads(line)) for line in trace.read_text(encoding="utf-8").splitlines()]
    matching = [event for event in events if event["run_id"] == run_id]
    assert matching
    assert all(isinstance(event["pid"], int) and event["pid"] > 0 for event in matching)
    roles = {event["worker"] for event in matching if event["event"] == "START"}
    assert roles == ({"gw0", "gw1"} if workers else {"controller"})


@pytest.mark.parametrize("trace_load_workers", [0, 2], ids=("serial", "xdist"))
@pytest.mark.parametrize("trace_load_failure", [False, True], ids=("pass", "fail"))
def test_observer_trace_contains_real_matching_protocol_pairs(
    tmp_path: Path, trace_load_workers: int, trace_load_failure: bool,
) -> None:
    fixture = _fixture(tmp_path)
    if trace_load_failure:
        fixture.environment["GLUDD_TRACE_LOAD_FAIL"] = "1"
    result = _observe(fixture, enabled=True, workers=trace_load_workers)
    status = _read_json(fixture.observed / "pytest/probe.json")
    assert result.returncode == (2 if trace_load_failure else 0)
    assert status["exit_code"] == (2 if trace_load_failure else 0)
    assert status["state"] == ("failed" if trace_load_failure else "passed")
    if trace_load_failure:
        assert "Error 1" in result.stdout + result.stderr
        assert "native-fixture-failure" in result.stdout
    trace_value = status["trace_path"]
    assert isinstance(trace_value, str)
    assert Path(trace_value) == fixture.observed / "pytest/probe.pytest.jsonl"
    _assert_configs(fixture, loaded=True)
    _assert_pairs(fixture, Path(trace_value), "probe", trace_load_workers)


@pytest.mark.parametrize("trace_load_empty", [False, True], ids=("absent", "empty"))
def test_off_preserves_plugin_free_default_and_empty_trace_path(
    tmp_path: Path, trace_load_empty: bool,
) -> None:
    fixture = _fixture(tmp_path)
    if trace_load_empty:
        fixture.environment["GLUDD_XDIST_TRACE_LOG"] = ""
    result = _observe(fixture, enabled=False)
    assert result.returncode == 0, result.stdout + result.stderr
    status = _read_json(fixture.observed / "pytest/probe.json")
    assert status["trace_path"] is None
    assert not list((fixture.observed / "pytest").glob("*.pytest.jsonl"))
    assert not (fixture.root / ".gate-logs/test-files-cprofile").exists()
    _assert_configs(fixture, loaded=False)


def test_profile_keeps_private_trace_stats_and_one_shot_mode(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    result = _observe(fixture, enabled=True, target="test-files-cprofile")
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_configs(fixture, loaded=True)
    profiles = list((fixture.root / ".gate-logs/test-files-cprofile").iterdir())
    assert len(profiles) == 1
    profile = profiles[0]
    _assert_pairs(fixture, profile / "trace.jsonl", profile.name)
    functions = pstats.Stats(str(profile / "pytest.pstats")).get_stats_profile().func_profiles
    assert "native_work" in functions
    assert "pytest_exitstatus=0" in (profile / "report.txt").read_text(encoding="utf-8")
    status = _read_json(fixture.observed / "pytest/probe.json")
    outer_trace = status["trace_path"]
    assert isinstance(outer_trace, str) and Path(outer_trace).read_bytes() == b""


@pytest.mark.parametrize("trace_load_environment_overrides", [False, True])
@pytest.mark.parametrize("trace_load_context", ["extracted", "main"])
@pytest.mark.parametrize("trace_load_origin", ["environment", "command-line", "target-specific"])
def test_inherited_literal_trace_path_survives_actual_make_startup(
    tmp_path: Path, trace_load_context: str, trace_load_origin: str,
    trace_load_environment_overrides: bool,
) -> None:
    fixture = _fixture(tmp_path)
    marker = fixture.root / "UNEXPECTED_EXPANSION"
    trace = fixture.root / (
        "literal ' spaced " + chr(96) + "true" + chr(96) +
        " $(shell touch " + str(marker) + ") $$ trace.jsonl"
    )
    environment = dict(fixture.environment)
    environment.update(GLUDD_XDIST_TRACE_RUN_ID="literal-run", GLUDD_XDIST_TRACE_TRUNCATE="0")
    if trace_load_context == "main":
        entry = fixture.root / "main-entry.mk"
        copied = _run([
            "make", "--no-print-directory", "copy-file", "SRC=Makefile",
            "DST=" + str(entry).replace("/private/tmp/", "/tmp/", 1),
        ], fixture, cwd=PROJECT)
        assert copied.returncode == 0, copied.stdout + copied.stderr
        main = entry.read_text(encoding="utf-8")
        assert main.count("include make/10-observability-and-tests.mk") == 1
        main = main.replace("include make/10-observability-and-tests.mk", "include " + str(MAKE_SOURCE), 1)
        assert main.count("include make/00-foundation.mk") == 1
        main = main.replace("include make/00-foundation.mk", "include " + str(FOUNDATION_SOURCE), 1)
        entry.write_text(main, encoding="utf-8")
        command = _test_files_command(fixture)
        command[command.index(str(fixture.makefile))] = str(entry)
        command.extend([
            "UV=" + shlex.join([sys.executable, str(fixture.root / "uv_spy.py")]),
            "NPROC=1", "GLUDD_XDIST_WORKERS=1",
        ])
        cwd = PROJECT
    else:
        entry = fixture.makefile
        command = _test_files_command(fixture)
        cwd = fixture.root
    if trace_load_origin == "environment":
        environment["GLUDD_XDIST_TRACE_LOG"] = str(trace)
    elif trace_load_origin == "command-line":
        command.append("GLUDD_XDIST_TRACE_LOG=" + str(trace))
    else:
        with entry.open("a", encoding="utf-8") as stream:
            stream.write("\ntest-files: export GLUDD_XDIST_TRACE_LOG := " + str(trace).replace("$", "$$") + "\n")
    if trace_load_environment_overrides:
        command.insert(1, "-e")
    result = _run(command, fixture, environment=environment, cwd=cwd)
    assert not marker.exists(), "trace-path data executed during native Make startup/export"
    assert result.returncode == 0, result.stdout + result.stderr
    _assert_configs(fixture, loaded=True)
    for path in fixture.root.glob("config-*.json"):
        assert _read_json(path)["trace"] == str(trace)
    _assert_pairs(fixture, trace, "literal-run")
