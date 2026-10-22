"""Confined real-Make controls for the complete run-watched command boundary."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import types
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

SOURCE = Path(__file__).resolve().parents[2]
MAKE_SOURCE = SOURCE / "make/10-observability-and-tests.mk"
ITEM_SOURCE = SOURCE / "scripts/item_progress.py"
RECURSION = {"MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES", "GNUMAKEFLAGS"}
TRACE_KEYS = {"GLUDD_XDIST_TRACE_LOG", "GLUDD_XDIST_TRACE_RUN_ID", "GLUDD_XDIST_TRACE_TRUNCATE"}

# Only fixture source/task identity seams are substituted. The frozen admission
# algorithm, native file lock/ledger, observer, Make and child processes are real.
IDENTITY_SEAM = """import hashlib
def repository_state_id(root, **kwargs):
    return hashlib.sha256((root / 'fixture-source').read_bytes()).hexdigest()
"""
TASK_SEAM = """import re
TASK_LINE_RE = re.compile(r'^- \\[ \\] (?P<task_id>S98[.]4)\\b.*$', re.M)
def task_inventory(text):
    return {'open_task_ids': [match.group('task_id') for match in TASK_LINE_RE.finditer(text)]}
"""
UV_ADAPTER = """import importlib, json, os, sys
from pathlib import Path
assert sys.argv[1:4] == ['run', 'python', 'scripts/stream_command.py']
root = Path(__file__).resolve().parent
sys.path.insert(0, str(root))
with (root / 'uv-argv.jsonl').open('a', encoding='utf-8') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
from scripts import item_progress, stream_command
if (root / 'ENABLE_FOCUS').exists() and not (root / '.gludd/item-progress.json').exists():
    item_progress.focus_item(root, 'S98.4')
raise SystemExit(stream_command.main(sys.argv[4:]))
"""
CHILD = """import json, os, sys, time
from pathlib import Path
root = Path(__file__).resolve().parent
phase, code = sys.argv[1], int(sys.argv[2])
current = root / 'observed/demo/current.json'
snapshot = json.loads(current.read_text()) if current.exists() else None
payload = {'phase': phase, 'argv': sys.argv[3:], 'pid': os.getpid(),
           'pgid': os.getpgrp(), 'parent': os.getppid(),
           'observer_state': None if snapshot is None else snapshot['state'],
           'trace': os.environ.get('GLUDD_XDIST_TRACE_LOG'),
           'trace_run': os.environ.get('GLUDD_XDIST_TRACE_RUN_ID')}
(root / (phase + '.json')).write_text(json.dumps(payload), encoding='utf-8')
if payload['trace']:
    with Path(payload['trace']).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'phase': phase, 'argv': payload['argv']}) + '\\n')
print('fixture-phase:' + phase, flush=True)
if '--hang' in sys.argv[3:]:
    time.sleep(30)
raise SystemExit(code)
"""


@dataclass
class Probe:
    root: Path
    environment: dict[str, str]

    def command(self, phase: str, code: int = 0, *args: str) -> str:
        return shlex.join([sys.executable, str(self.root / "child.py"), phase, str(code), *args])

    def payload(self, phase: str) -> dict[str, object]:
        return cast("dict[str, object]", json.loads((self.root / (phase + ".json")).read_text(encoding="utf-8")))

    def status(self, run_id: str = "run-1") -> dict[str, object]:
        path = self.root / "observed/demo" / (run_id + ".json")
        result = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
        assert result == json.loads((path.parent / "current.json").read_text())
        assert not list(path.parent.glob(".*.tmp"))
        return result

    def log(self, run_id: str = "run-1") -> str:
        return (self.root / "observed/demo" / (run_id + ".log")).read_text(encoding="utf-8")

    def make(
        self, command: str, *, trace: str = "0", run_id: str = "run-1",
        limits: tuple[str, str] = ("3", "5"), positional: bool = False,
        origin: str = "command-line", inherited_override: bool = False,
        late: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        environment = dict(self.environment)
        goal = "boundary-alias" if late else "run-watched"
        arguments = ["make", "--no-print-directory"]
        if inherited_override:
            arguments.append("-e")
        arguments.extend([
            "-f", str(self.root / "Makefile"), goal,
            "UV=" + shlex.join([sys.executable, str(self.root / "selected-uv.py")]),
            "OBSERVED_ROOT=" + str(self.root / "observed"), "OBSERVED_LABEL=demo",
            "RUN_ID=" + run_id, "WORK_ITEM_ID=S98.4", "STALL_SECS=" + limits[0],
            "MAX_SECS=" + limits[1], "LOG=" + str(self.root / "observed/demo" / (run_id + ".log")),
            "OBSERVED_HEARTBEAT_SECS=0.02", "OBSERVED_RETAIN_RUNS=2",
            "RUN_WATCHED_PYTEST_TRACE=" + trace,
        ])
        if origin == "environment":
            environment["CMD"] = command
        else:
            assert origin == "command-line"
            arguments.append("CMD=" + command)
        if positional:
            with (self.root / "Makefile").open("a", encoding="utf-8") as stream:
                stream.write('\noverride CMD := $(value CMD) "$$@"\n')
        if late:
            with (self.root / "Makefile").open("a", encoding="utf-8") as stream:
                stream.write(
                    "\n.PHONY: boundary-alias\n"
                    "boundary-alias: override CMD = " + command + "\n"
                    "boundary-alias: run-watched\n"
                )
            if origin == "environment":
                environment["CMD"] = "printf wrong-early-binding"
            else:
                arguments[-1] = "CMD=printf wrong-early-binding"
        process = subprocess.Popen(
            arguments, cwd=self.root, env=environment, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=3)
            return subprocess.CompletedProcess(arguments, 900, stdout, stderr + "\nFIXTURE_DEADLINE")
        return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _target() -> str:
    text = MAKE_SOURCE.read_text(encoding="utf-8")
    marker = "\nrun-watched:\n"
    assert text.count(marker) == 1, "exactly one frozen production target required"
    lines = text.splitlines()
    bindings = [line for line in lines if line.startswith("run-watched: ") and "GLUDD_RUN_WATCHED_CMD" in line]
    assert len(bindings) <= 1, "duplicate production CMD binding"
    body = text.split(marker, 1)[1].split("\n\n", 1)[0]
    return "\n".join([*bindings, "run-watched:", body]) + "\n"


@pytest.fixture
def probe(tmp_path: Path) -> Iterator[Probe]:
    root = tmp_path.resolve()
    scripts = root / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").write_text("", encoding="utf-8")
    (scripts / "stream_command.py").write_bytes((SOURCE / "scripts/stream_command.py").read_bytes())
    (scripts / "item_progress.py").write_bytes(ITEM_SOURCE.read_bytes())
    (scripts / "gate_status_attestation.py").write_text(IDENTITY_SEAM, encoding="utf-8")
    (scripts / "task_scope.py").write_text(TASK_SEAM, encoding="utf-8")
    (root / "fixture-source").write_text("d0aba940a650a14404853cf23499f932ee8ee5e3\n", encoding="utf-8")
    (root / "TASKS.md").write_text("- [ ] S98.4 fixture owner\n", encoding="utf-8")
    (root / "selected-uv.py").write_text(UV_ADAPTER, encoding="utf-8")
    (root / "child.py").write_text(CHILD, encoding="utf-8")
    (root / "Makefile").write_text(
        _target() + "\n.PHONY: disk-check _pytest-runtime-compatibility-admission\n"
        "disk-check:\n\t@printf 'fixture-diagnostic\\n'\n"
        "_pytest-runtime-compatibility-admission:\n\t@printf 'fixture-compatibility\\n'\n",
        encoding="utf-8",
    )
    for name in ("home", "tmp"):
        (root / name).mkdir()
    removed = RECURSION | TRACE_KEYS | {
        "CMD", "GLUDD_RUN_WATCHED_CMD", "RUN_WATCHED_PYTEST_TRACE",
        "PYTEST_CURRENT_TEST", "PYTHONPATH",
    }
    environment = {name: value for name, value in os.environ.items() if name not in removed}
    environment.update(HOME=str(root / "home"), TMPDIR=str(root / "tmp"), PYTHONPATH=str(root),
                       PYTHONDONTWRITEBYTECODE="1", MAKEFILES="")
    fixture = Probe(root, environment)
    try:
        yield fixture
    finally:
        # Drain only groups recorded by fixture children. This cannot authorize
        # production cleanup and never substitutes for native-cleanup assertions.
        groups = set()
        for path in root.glob("*.json"):
            value = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and type(value.get("pgid")) is int:
                groups.add(value["pgid"])
        for group in groups:
            if group != os.getpgrp():
                with suppress(ProcessLookupError):
                    os.killpg(group, signal.SIGKILL)


@pytest.mark.parametrize("trace", ["0", "1"])
@pytest.mark.parametrize("last_status", [0, 7])
def test_compound_tail_is_running_inside_one_terminal_receipt(
    probe: Probe, trace: str, last_status: int,
) -> None:
    command = probe.command("first") + " && " + probe.command("second", last_status)
    result = probe.make(command, trace=trace)
    assert result.returncode == (0 if last_status == 0 else 2), result.stdout + result.stderr
    assert probe.payload("second")["observer_state"] == "running"
    status = probe.status()
    assert status["state"] == ("passed" if last_status == 0 else "failed")
    assert status["exit_code"] == last_status
    assert status["termination_reason"] is None
    assert "fixture-phase:first\n" in probe.log()
    assert "fixture-phase:second\n" in probe.log()
    if last_status:
        assert "Error 7" in result.stderr
    if trace == "1":
        path = Path(str(status["trace_path"]))
        assert path == probe.root / "observed/demo/run-1.pytest.jsonl"
        assert [json.loads(line)["phase"] for line in path.read_text().splitlines()] == ["first", "second"]
        for phase in ("first", "second"):
            assert probe.payload(phase)["trace"] == str(path)
            assert probe.payload(phase)["trace_run"] == "run-1"
    else:
        assert status["trace_path"] is None
        assert all(probe.payload(phase)["trace"] is None for phase in ("first", "second"))


def test_failed_first_stage_keeps_native7_and_stops_and_tail(probe: Probe) -> None:
    result = probe.make(probe.command("first", 7) + " && " + probe.command("second"))
    assert result.returncode == 2 and "Error 7" in result.stderr
    assert probe.status()["state"] == "failed" and probe.status()["exit_code"] == 7
    assert not (probe.root / "second.json").exists()


@pytest.mark.parametrize(("operator", "first_status", "last_status"), [
    (";", 0, 7), ("||", 7, 0), ("|", 0, 7),
])
def test_native_shell_operators_keep_their_actual_terminal_status(
    probe: Probe, operator: str, first_status: int, last_status: int,
) -> None:
    command = probe.command("first", first_status) + " " + operator + " " + probe.command("second", last_status)
    result = probe.make(command)
    assert result.returncode == (0 if last_status == 0 else 2), result.stdout + result.stderr
    assert probe.status()["exit_code"] == last_status
    assert probe.payload("second")["observer_state"] == "running"
    assert "fixture-phase:second\n" in probe.log()


@pytest.mark.parametrize("trace", ["0", "1"])
def test_shell_positional_parameters_and_quoted_arguments_remain_exact(probe: Probe, trace: str) -> None:
    arguments = ("literal && ; | > <", "", "a b", 'single\'double"quote', "*.unexpanded", r"back\slash")
    result = probe.make(probe.command("only", 0, *arguments), trace=trace, positional=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.payload("only")["argv"] == list(arguments)
    assert probe.status()["state"] == "passed"


@pytest.mark.parametrize("origin", ["command-line", "environment"])
@pytest.mark.parametrize("inherited_override", [False, True])
@pytest.mark.parametrize("late", [False, True])
def test_command_uses_actual_target_context_without_outer_shell_reinterpretation(
    probe: Probe, origin: str, inherited_override: bool, late: bool,
) -> None:
    command = probe.command("first") + " && " + probe.command("second")
    result = probe.make(command, origin=origin, inherited_override=inherited_override, late=late)
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.payload("second")["observer_state"] == "running"
    assert probe.status()["state"] == "passed"


def test_complete_actual_shell_argv_is_the_native_admission_identity(probe: Probe) -> None:
    (probe.root / "ENABLE_FOCUS").touch()
    command = probe.command("first") + " && " + probe.command("second")
    result = probe.make(command)
    assert result.returncode == 0, result.stdout + result.stderr
    raw_identity = probe.status()["progress_identity"]
    assert isinstance(raw_identity, dict)
    identity = cast("dict[str, object]", raw_identity)
    assert identity["command"] == ["/bin/sh", "-c", command]
    assert identity["limits"] == [3.0, 5.0]
    ledger = json.loads((probe.root / ".gludd/item-progress.json").read_text())
    assert len(ledger["observations"]) == 1 and ledger["reservations"] == {}


def test_unchanged_failed_complete_script_is_refused_but_changed_tail_is_new_input(probe: Probe) -> None:
    (probe.root / "ENABLE_FOCUS").touch()
    failed = probe.command("first") + " && " + probe.command("second", 7)
    first = probe.make(failed)
    assert first.returncode == 2 and probe.status()["exit_code"] == 7
    second = probe.make(failed, run_id="retry")
    assert second.returncode == 2 and "unchanged failed inputs" in second.stderr
    assert not (probe.root / "observed/demo/retry.json").exists()
    changed = probe.command("first") + " && " + probe.command("second", 0)
    third = probe.make(changed, run_id="changed-tail")
    assert third.returncode == 0, third.stdout + third.stderr
    assert probe.status("changed-tail")["exit_code"] == 0


def _progress_module(probe: Probe, monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    package = types.ModuleType("scripts")
    package.__path__ = [str(probe.root / "scripts")]
    monkeypatch.setitem(sys.modules, "scripts", package)
    for name in ("gate_status_attestation", "stream_command", "task_scope", "item_progress"):
        key = "scripts." + name
        spec = importlib.util.spec_from_file_location(key, probe.root / "scripts" / (name + ".py"))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, key, module)
        spec.loader.exec_module(module)
    return sys.modules["scripts.item_progress"]


def test_fixed_compatibility_lifecycle_normalization_retains_other_private_bytes(
    probe: Probe, monkeypatch: pytest.MonkeyPatch,
) -> None:
    progress = _progress_module(probe, monkeypatch)
    command = ["/bin/sh", "-c", "make --no-print-directory _pytest-runtime-compatibility-admission"]
    environment = {
        "GATE_CANDIDATE_SHA": "changing-native-owner-sha", "GLUDD_GATE_OWNER_PID": "123",
        "MAKEOVERRIDES": "opaque-native-reference", "PRIVATE_INPUT": "fixture-only",
    }
    assert progress._semantic_environment(command, environment) == {
        "MAKEOVERRIDES": "opaque-native-reference", "PRIVATE_INPUT": "fixture-only",
    }
    unsafe = ["/bin/sh", "-c", command[2] + " && printf extra-stage"]
    assert progress._semantic_environment(unsafe, environment) is environment


@pytest.mark.parametrize("script", [
    "make disk-check",
    "make --no-print-directory disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 "
    "DISK_CLEANUP_PREFLIGHT_DRY_RUN=1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800",
])
def test_exact_canonical_readonly_make_keeps_the_existing_capacity_exemption(
    probe: Probe, monkeypatch: pytest.MonkeyPatch, script: str,
) -> None:
    progress = _progress_module(probe, monkeypatch)
    assert progress._read_only_capacity_diagnostic(["/bin/sh", "-c", script])


@pytest.mark.parametrize("script", [
    "make disk-check && printf unrelated",
    "make disk-check;printf unrelated",
    "make disk-check\nprintf unrelated",
    'make "$$BOUNDARY_TARGET"',
    "make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 "
    "DISK_CLEANUP_PREFLIGHT_DRY_RUN=0 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1800",
    "make disk-cleanup-preflight DISK_CLEANUP_PREFLIGHT_VALIDATE_ONLY=0 "
    "DISK_CLEANUP_PREFLIGHT_DRY_RUN=1 DISK_CLEANUP_RECEIPT_GRACE_SECONDS=1799",
])
def test_compound_dynamic_or_mutating_script_never_inherits_readonly_capacity_authority(
    probe: Probe, monkeypatch: pytest.MonkeyPatch, script: str,
) -> None:
    progress = _progress_module(probe, monkeypatch)
    assert not progress._read_only_capacity_diagnostic(["/bin/sh", "-c", script])


@pytest.mark.parametrize(("command", "exempt"), [
    (["/bin/sh", "-c", "make cat-file 'FILE=literal && not-a-command'"], True),
    (["/bin/sh", "-c", "make cat-file 'UV=literal && not-a-command'"], False),
    (["/tmp/fixture-spoof/sh", "-c", "make cat-file"], False),
    (["/bin/sh", "-c", "make cat-file", "extra-positional-context"], False),
    (["/bin/sh", "-ec", "make cat-file"], False),
    (["/bin/sh", "-c", "make $(printf cat-file)"], False),
    (["/bin/sh", "-c", "make " + chr(96) + "printf cat-file" + chr(96)], False),
    (["/bin/sh", "-c", "make cat-file\nprintf unrelated"], False),
])
def test_native_admission_predicates_keep_only_exact_literal_diagnostics(
    probe: Probe, monkeypatch: pytest.MonkeyPatch, command: list[str], exempt: bool,
) -> None:
    progress = _progress_module(probe, monkeypatch)
    with progress.command_admission(
        probe.root, command, environment=probe.environment,
        quiet_seconds=3.0, max_seconds=5.0,
        primary_task_id="S98.4", task_id="S98.4",
    ) as admission:
        if exempt:
            assert admission is None
            assert not (probe.root / ".gludd/item-progress.json").exists()
        else:
            assert admission is not None
            assert admission.identity["command"] == command
            assert admission.identity["limits"] == [3.0, 5.0]


def test_external_pytest_configuration_uses_one_physical_owned_root(pytestconfig: pytest.Config) -> None:
    expected = SOURCE.resolve()
    assert Path(pytestconfig.rootpath).resolve() == expected
    confcutdir = pytestconfig.getoption("confcutdir")
    assert confcutdir is None or Path(confcutdir).resolve() == expected
    assert pytestconfig.inipath is not None
    assert Path(pytestconfig.inipath).resolve() == expected / "pyproject.toml"


def test_second_stage_timeout_has_native124_and_is_drained(probe: Probe) -> None:
    command = probe.command("first") + " && " + probe.command("second", 0, "--hang")
    result = probe.make(command, limits=("0", "1"), trace="1")
    assert result.returncode == 2 and "Error 124" in result.stderr, result.stdout + result.stderr
    status = probe.status()
    assert status["state"] == "timed_out" and status["exit_code"] == 124
    assert status["termination_reason"] == "max-runtime-timeout"
    assert "fixture-phase:second\n" in probe.log()
    for phase in ("first", "second"):
        child_pid = probe.payload(phase)["pid"]
        assert isinstance(child_pid, int) and child_pid > 0
        with pytest.raises(ProcessLookupError):
            os.kill(child_pid, 0)
