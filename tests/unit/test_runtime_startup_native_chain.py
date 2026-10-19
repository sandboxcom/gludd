"""Fresh native startup chains retain every real source and policy boundary."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Protocol

import pytest
from coverage import CoverageData

ROOT = Path(__file__).resolve().parents[2]

_PROBE = r"""
import importlib
import json
import subprocess
import sys
from pathlib import Path

action, source, repository, expected_head = sys.argv[1:]
source = Path(source)
assert sys.flags.ignore_environment == 1 and sys.flags.optimize == 0
assert "pytest" not in sys.modules and "conftest" not in sys.modules
initial_path = tuple(sys.path)
if action == "startup":
    observed = importlib.import_module("scripts.stream_command")
    actual = observed._startup_source(Path(repository))
prefix = "" if action == "flat" else "scripts."
verifier = importlib.import_module(prefix + "verify_dual_track_ci")
named = importlib.import_module(prefix + "ci_named_shard_files")
resources = importlib.import_module(prefix + "resource_arbiter")
runner = importlib.import_module(prefix + "run_ci_shards_serial")
for name, module in (
    ("verify_dual_track_ci", verifier), ("ci_named_shard_files", named),
    ("resource_arbiter", resources), ("run_ci_shards_serial", runner),
):
    assert Path(module.__file__).resolve() == source / "scripts" / (name + ".py")
assert verifier.SHARDS is named.SHARDS
assert verifier.resource_root is resources.resource_root
assert verifier.canonical_json_sha256 is runner.canonical_json_sha256
assert verifier.release_execution_policy is runner.release_execution_policy
assert verifier.ATTESTATION_SCHEMA_VERSION == runner.ATTESTATION_SCHEMA_VERSION
assert verifier.EXPECTED_EXECUTION_POLICY == runner.release_execution_policy()
assert verifier.EXPECTED_EXECUTION_POLICY_SHA256 == runner.canonical_json_sha256(
    verifier.EXPECTED_EXECUTION_POLICY,
)
if action == "startup":
    inputs = importlib.import_module("scripts.reviewed_head_inputs")
    bindings = {
        "receipt_owner": "build_reviewed_head_integration_receipt",
        "gate_owner": "gate_status_attestation",
        "background_owner": "start_gate_background",
        "observed_owner": "stream_command",
        "shard_owner": "verify_dual_track_ci",
        "runtime_owner": "ci_batch_receipts",
        "resource_owner": "resource_arbiter",
        "progress_owner": "item_progress",
        "trace_owner": "xdist_trace_plugin",
    }
    for attribute, name in bindings.items():
        module = importlib.import_module("scripts." + name)
        assert getattr(inputs, attribute) is module
        assert Path(module.__file__).resolve() == source / "scripts" / (name + ".py")
    auth = importlib.import_module("scripts.ci_receipt_auth")
    gate = importlib.import_module("scripts.gate_status_attestation")
    assert inputs.load_gate_receipt_auth_context is auth.load_gate_receipt_auth_context
    assert auth._read_key is gate._read_key
    assert actual == (expected_head, gate.repository_state_id(Path(repository), source="index"))
    assert len(actual[1]) == 64
    inherited = {"CI": "true", "MAKEFLAGS": "fixture parent carrier"}
    assert runner._owned_test_environment(inherited) == {"CI": "true"}
    assert inherited["MAKEFLAGS"] == "fixture parent carrier"
    try:
        observed._startup_capacity(Path(repository), {})
    except subprocess.CalledProcessError as error:
        assert error.cmd == ["make", "--no-print-directory", "ps"]
        assert "No rule to make target" in error.stderr
    else:
        raise AssertionError("missing fixture capacity owner was accepted")
    try:
        observed._startup_runtime(Path(repository), None, "3.12.15", "ci")
    except ValueError as error:
        assert str(error).startswith("startup runtime inspection refused: cannot load profile catalog")
    else:
        raise AssertionError("missing fixture profile catalog was accepted")
    profiles = importlib.import_module("scripts.dependency_profiles")
    assert Path(profiles.__file__).resolve() == source / "scripts/dependency_profiles.py"
expected_path = initial_path
if action == "startup" and str(source / "src") not in initial_path:
    expected_path = (str(source / "src"), *initial_path)
assert tuple(sys.path) == expected_path, (expected_path, tuple(sys.path))
if action != "flat":
    assert source / "scripts" not in {Path(value).resolve() for value in sys.path}
assert "conftest" not in sys.modules
print(json.dumps({"action": action, "native_chain": "verified"}))
"""

_SIBLING_PROBE = r"""
import importlib
import json
import sys
from pathlib import Path

name, context, source = sys.argv[1:]
source = Path(source)
assert sys.flags.ignore_environment == 1 and sys.flags.optimize == 0
assert "pytest" not in sys.modules and "conftest" not in sys.modules
before = tuple(sys.path)
prefix = "scripts." if context == "package" else ""
module = importlib.import_module(prefix + name)
assert Path(module.__file__).resolve() == source / "scripts" / (name + ".py")
bindings = {
    "run_ci_shards_serial": (
        ("_receipt_runtime", "ci_shadow_receipt_runtime", None),
        ("BatchReceiptSession", "ci_shadow_receipt_runtime", "BatchReceiptSession"),
        ("ShadowBatchReceiptWriter", "ci_batch_receipts", "ShadowBatchReceiptWriter"),
        ("ShadowReplayAuditor", "ci_batch_replay_audit", "ShadowReplayAuditor"),
        ("ShadowGateProgress", "ci_gate_progress", "ShadowGateProgress"),
        ("_env_for_shard", "run_ci_shards_parallel", "_env_for_shard"),
    ),
    "ci_shadow_receipt_runtime": (
        ("ShadowBatchReceiptWriter", "ci_batch_receipts", "ShadowBatchReceiptWriter"),
        ("ShadowReplayAuditor", "ci_batch_replay_audit", "ShadowReplayAuditor"),
        ("ShadowGateProgress", "ci_gate_progress", "ShadowGateProgress"),
    ),
    "ci_batch_receipts": (
        ("ReceiptSigner", "ci_receipt_auth", "ReceiptSigner"),
        ("create_receipt_envelope", "ci_receipt_auth", "create_receipt_envelope"),
    ),
    "ci_batch_replay_audit": (
        ("validate_batch_receipt", "ci_batch_receipts", "validate_batch_receipt"),
        ("receipt_duration_seconds", "ci_gate_progress", "receipt_duration_seconds"),
        ("authenticate_receipt", "ci_receipt_auth", "authenticate_receipt"),
    ),
    "ci_gate_progress": (
        ("validate_batch_receipt", "ci_batch_receipts", "validate_batch_receipt"),
        ("authenticate_receipt", "ci_receipt_auth", "authenticate_receipt"),
    ),
    "run_ci_shards_parallel": (
        ("expand_shard", "ci_named_shard_files", "expand_shard"),
        ("project_resource_root", "resource_arbiter", "resource_root"),
    ),
}
for attribute, peer_name, peer_attribute in bindings[name]:
    peer = importlib.import_module(prefix + peer_name)
    expected = peer if peer_attribute is None else getattr(peer, peer_attribute)
    assert getattr(module, attribute) is expected
if name == "ci_shadow_receipt_runtime":
    other = "ci_shadow_receipt_runtime" if prefix else "scripts.ci_shadow_receipt_runtime"
    assert sys.modules[other] is module
assert tuple(sys.path) == before
if prefix:
    assert source / "scripts" not in {Path(value).resolve() for value in sys.path}
assert "conftest" not in sys.modules
print(json.dumps({"module": name, "context": context, "bindings": "verified"}))
"""


@pytest.mark.parametrize("import_context", ["package", "flat"])
@pytest.mark.parametrize("module", [
    "run_ci_shards_serial", "ci_shadow_receipt_runtime", "ci_batch_receipts",
    "ci_batch_replay_audit", "ci_gate_progress", "run_ci_shards_parallel",
])
def test_fresh_native_direct_closure_retains_real_api_and_class_identity(module: str, import_context: str) -> None:
    directory = ROOT if import_context == "package" else ROOT / "scripts"
    result = subprocess.run(
        [sys.executable, "-E", "-W", "error", "-c", _SIBLING_PROBE, module, import_context, str(ROOT)],
        cwd=directory, check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {"module": module, "context": import_context, "bindings": "verified"}


def _git(repository: Path, *arguments: str) -> str:
    """Reuse the existing native-fixture pattern with no shared Git mutation."""
    return subprocess.run(
        ["git", "-C", str(repository), *arguments], cwd=repository,
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()


def _fresh_probe(action: str, repository: Path, head: str) -> subprocess.CompletedProcess[str]:
    directory = ROOT / "scripts" if action == "flat" else ROOT
    return subprocess.run(
        [sys.executable, "-E", "-W", "error", "-c", _PROBE, action, str(ROOT), str(repository), head],
        cwd=directory, check=False, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("import_context", ["package", "flat"])
def test_fresh_native_verifier_keeps_actual_sibling_and_policy_identity(tmp_path: Path, import_context: str) -> None:
    result = _fresh_probe(import_context, tmp_path, "")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {"action": import_context, "native_chain": "verified"}


def test_complete_startup_lazy_chain_reaches_real_clean_source_identity(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "development")
    _git(repository, "config", "user.name", "Gludd native tests")
    _git(repository, "config", "user.email", "native@gludd.invalid")
    (repository / "source.txt").write_text("actual native source\n", encoding="utf-8")
    _git(repository, "add", "source.txt")
    _git(repository, "commit", "-m", "native source fixture")
    head = _git(repository, "rev-parse", "HEAD")
    result = _fresh_probe("startup", repository, head)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {"action": "startup", "native_chain": "verified"}
    assert _git(repository, "status", "--porcelain") == ""
    assert _git(repository, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("validate_only", ["1", "0"])
def test_native_make_startup_plan_and_source_refusal_never_launch_observer(validate_only: str) -> None:
    label = "native-chain-" + uuid.uuid4().hex
    log = ROOT / ".gate-logs" / (label + ".log")
    observed = ROOT / ".gate-logs/observed" / label
    command = [
        "make", "--no-print-directory", "runtime-startup-diagnostic",
        "RUNTIME_DIAGNOSTIC_VALIDATE_ONLY=" + validate_only,
        "OBSERVED_ROOT=.gate-logs/observed", "OBSERVED_LABEL=" + label, "RUN_ID=probe",
        "RUNTIME_DIAGNOSTIC_SOURCE_SHA=" + "0" * 40,
        "RUNTIME_DIAGNOSTIC_ENVIRONMENT=", "RUNTIME_DIAGNOSTIC_PYTHON=3.12.15",
        "RUNTIME_DIAGNOSTIC_SET=ci", "LOG=" + str(log),
        "OBSERVED_HEARTBEAT_SECS=10", "OBSERVED_RETAIN_RUNS=20", "WORK_ITEM_ID=S98.4",
        "UV=" + os.environ.get("UV", "uv"), "UV_PROJECT_ENVIRONMENT=" + sys.prefix,
        "UV_PYTHON=" + sys.executable, "UV_NO_SYNC=1", "UV_PYTHON_DOWNLOADS=never",
        "PYTHONPATH=" + str(ROOT / "src"), "NPROC=1", "GLUDD_XDIST_WORKERS=1",
    ]
    assert not log.exists() and not observed.exists()
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True, timeout=30)
    output = result.stdout + result.stderr
    assert "Traceback" not in output and "ModuleNotFoundError" not in output, output
    if validate_only == "1":
        assert result.returncode == 0, output
        plan = json.loads(result.stdout)
        assert plan["validate_only"] is True and plan["diagnostic_only"] is True
        assert plan["command"] == ["make", "--no-print-directory", "_pytest-runtime-compatibility-admission"]
        assert (plan["max_seconds"], plan["quiet_seconds"]) == (90, 60)
    else:
        assert result.returncode != 0, output
        assert any(reason in output for reason in (
            "native inputs require an exact clean repository",
            "startup source does not match the exact clean candidate",
        )), output
    assert not log.exists() and not observed.exists()



_FRAGMENT_CHILD = r"""
import json
import os
import runpy
import signal
import sys
from pathlib import Path

from coverage import Coverage

mode, target, events = sys.argv[1:]
def record(event):
    with Path(events).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"event": event, "pid": os.getpid()}) + "\n")
def finish(signum, frame):
    record("interrupted")
    raise SystemExit(130)
signal.signal(signal.SIGINT, finish)
record("ready")
if mode == "pending":
    while True:
        signal.pause()
if mode != "missing":
    collector = Coverage(
        data_file=os.environ["COVERAGE_FILE"], config_file=False,
        branch=True, include=[target],
    )
    with collector.collect():
        runpy.run_path(target, run_name="__main__")
    collector.save()
Path(os.environ["COVERAGE_FILE"]).with_name("junit.xml").write_text(
    '<testsuite><testcase classname="native.fixture" name="body"/></testsuite>',
    encoding="utf-8",
)
raise SystemExit(3 if mode == "failure" else 0)
"""


class _NativeRunningShard(Protocol):
    @property
    def process(self) -> subprocess.Popen[bytes]: ...

    @property
    def coverage_file(self) -> Path | None: ...


@dataclass
class _ParallelNativeFixture:
    owner: ModuleType
    repository: Path
    resources: Path
    resume: Path
    target: Path
    events: Path
    mode: list[str]
    commands: list[list[str]]
    workspaces: list[Path]
    processes: list[subprocess.Popen[bytes]]
    captured_digests: list[str]


@pytest.fixture
def parallel_native_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_ParallelNativeFixture]:
    """Exercise the existing coordinator with one positively owned native child."""
    owner = importlib.import_module("scripts.run_ci_shards_parallel")
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "development")
    _git(repository, "config", "user.name", "Gludd native fixture")
    _git(repository, "config", "user.email", "native@gludd.invalid")
    target = repository / "test_fixture.py"
    target.write_text("answer = 42\n", encoding="utf-8")
    _git(repository, "add", "test_fixture.py")
    _git(repository, "commit", "-m", "confined native fixture")
    resources = tmp_path / "owned"
    state = _ParallelNativeFixture(
        owner, repository, resources, resources / "ci-shards-parallel/resume.json",
        target, tmp_path / "child-events.jsonl", ["pass"], [], [], [], [],
    )
    monkeypatch.setattr(owner, "__file__", str(repository / "scripts/run_ci_shards_parallel.py"))
    monkeypatch.setattr(owner, "project_resource_root", lambda: resources)
    monkeypatch.setattr(owner, "expand_shard", lambda _name: ["test_fixture.py"])
    monkeypatch.setenv("GLUDD_SHARD_SUMMARY_DIR", str(tmp_path / "summaries"))

    def command_for_shard(
        shard: str, arguments: list[str], workers: int, *,
        files: list[str], batch_index: int, coverage: bool,
    ) -> tuple[list[str], Path]:
        assert (shard, arguments, workers, files, batch_index, coverage) == (
            "native-fixture", ["-W", "error"], 0, ["test_fixture.py"], 1, True,
        )
        workspace = tmp_path / f"native-child-{len(state.commands)}"
        workspace.mkdir()
        command = [
            sys.executable, "-E", "-W", "error", "-c", _FRAGMENT_CHILD,
            state.mode[0], str(target), str(state.events),
        ]
        state.commands.append(command)
        state.workspaces.append(workspace)
        return command, workspace

    terminate = owner._terminate_all

    def terminate_owned(running: list[_NativeRunningShard]) -> None:
        for item in running:
            process = item.process
            assert isinstance(process, subprocess.Popen)
            state.processes.append(process)
            coverage_file = item.coverage_file
            if isinstance(coverage_file, Path) and coverage_file.is_file():
                state.captured_digests.append(hashlib.sha256(coverage_file.read_bytes()).hexdigest())
        terminate(running)

    monkeypatch.setattr(owner, "_command_for_shard", command_for_shard)
    monkeypatch.setattr(owner, "_terminate_all", terminate_owned)
    try:
        yield state
    finally:
        for process in state.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _run_parallel_fixture(state: _ParallelNativeFixture, *, limit: int = 10) -> int:
    """Keep each real fixture invocation bounded and single-child."""
    result = state.owner.run(
        ["native-fixture"], ["-W", "error"], 0, 5,
        max_runtime_seconds=limit, max_files_per_batch=1, resume_path=state.resume,
    )
    assert isinstance(result, int)
    assert all(process.poll() is not None for process in state.processes)
    assert all(not path.exists() for path in state.workspaces)
    return result


def test_native_package_resume_reuses_only_current_candidate_and_real_fragment(
    parallel_native_fixture: _ParallelNativeFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = parallel_native_fixture
    assert _run_parallel_fixture(state) == 0
    payload = json.loads(state.resume.read_text(encoding="utf-8"))
    head = _git(state.repository, "rev-parse", "HEAD")
    key = state.owner._batch_key("native-fixture", 1, ["test_fixture.py"])
    assert payload["candidate_sha"] == head
    assert payload["runner"] == "scripts/run_ci_shards_parallel.py"
    fragment = state.resources / "ci-shards-parallel" / payload[key]["coverage_fragment"]
    assert hashlib.sha256(fragment.read_bytes()).hexdigest() == state.captured_digests[0]
    data = CoverageData(basename=str(fragment))
    data.read()
    assert data.lines(str(state.target)) == [1]
    assert data.has_arcs()
    assert _run_parallel_fixture(state) == 0
    assert len(state.commands) == 1
    assert "RESUME-SKIP shard=native-fixture" in capsys.readouterr().out
    _git(state.repository, "commit", "--allow-empty", "-m", "next actual candidate")
    assert _run_parallel_fixture(state) == 0
    assert len(state.commands) == 2
    assert json.loads(state.resume.read_text(encoding="utf-8"))["candidate_sha"] != head


@pytest.mark.parametrize("mode, expected", [("failure", 3), ("missing", 0)])
def test_native_package_resume_never_reuses_failed_or_absent_fragment(
    parallel_native_fixture: _ParallelNativeFixture, mode: str, expected: int,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = parallel_native_fixture
    state.mode[0] = mode
    assert _run_parallel_fixture(state) == expected
    assert not state.resume.exists()
    state.mode[0] = "pass"
    assert _run_parallel_fixture(state) == 0
    assert len(state.commands) == 2
    output = capsys.readouterr().out
    assert "RESUME-SKIP" not in output
    assert ("SHARD-FAIL" if mode == "failure" else "SHARD-COVERAGE-MISSING") in output


def test_native_package_resume_timeout_reaps_live_child_without_pass_state(
    parallel_native_fixture: _ParallelNativeFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = parallel_native_fixture
    state.mode[0] = "pending"
    assert _run_parallel_fixture(state, limit=1) == 124
    assert not state.resume.exists()
    events = [json.loads(line) for line in state.events.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["ready", "interrupted"]
    assert events[0]["pid"] == state.processes[0].pid
    assert "SHARD-TIMEOUT shard=native-fixture batch=1" in capsys.readouterr().out


def test_native_package_resume_interruption_restores_cleanup_without_pass_state(
    parallel_native_fixture: _ParallelNativeFixture, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = parallel_native_fixture
    state.mode[0] = "pending"
    sleep = state.owner.time.sleep
    monotonic = state.owner.time.monotonic
    seen_live: list[int] = []

    def interrupt(_seconds: float) -> None:
        state.owner.time.sleep = sleep
        deadline = monotonic() + 5
        while not state.events.exists():
            assert monotonic() < deadline, "native fixture did not acknowledge startup"
            sleep(0.01)
        event = json.loads(state.events.read_text(encoding="utf-8").splitlines()[0])
        os.kill(event["pid"], 0)
        seen_live.append(event["pid"])
        raise KeyboardInterrupt

    monkeypatch.setattr(state.owner.time, "sleep", interrupt)
    assert _run_parallel_fixture(state) == 130
    assert seen_live == [state.processes[0].pid]
    assert not state.resume.exists()
    assert "SHARD-INTERRUPTED terminating children" in capsys.readouterr().out


@pytest.mark.parametrize("field", ["runtime", "batch"])
def test_native_package_resume_rejects_nonpositive_bounds_before_child(
    parallel_native_fixture: _ParallelNativeFixture, field: str,
) -> None:
    state = parallel_native_fixture
    with pytest.raises(ValueError, match="must be positive"):
        state.owner.run(
            ["native-fixture"], ["-W", "error"], 0, 5,
            max_runtime_seconds=0 if field == "runtime" else 10,
            max_files_per_batch=0 if field == "batch" else 1,
            resume_path=state.resume,
        )
    assert not state.commands and not state.processes and not state.resume.exists()


def test_native_package_resume_rejects_empty_registry_before_child(
    parallel_native_fixture: _ParallelNativeFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = parallel_native_fixture
    monkeypatch.setattr(state.owner, "expand_shard", lambda _name: [])
    with pytest.raises(SystemExit, match="expanded to no files"):
        _run_parallel_fixture(state)
    assert not state.commands and not state.processes and not state.resume.exists()


@pytest.mark.parametrize("entry", [
    None, {"rc": 1}, {"rc": 0}, {"rc": 0, "coverage_fragment": "zero"},
])
def test_native_package_resume_refuses_incomplete_exact_key_evidence(
    parallel_native_fixture: _ParallelNativeFixture, entry: object,
) -> None:
    state = parallel_native_fixture
    paths = state.owner._resource_paths()
    paths.coverage_shards.mkdir(parents=True)
    (paths.root / "zero").write_bytes(b"")
    key = state.owner._batch_key("native-fixture", 1, ["test_fixture.py"])
    assert state.owner._resume_skip_batch(
        {key: entry}, "native-fixture", 1, ["test_fixture.py"],
        paths.coverage_shards, paths.root,
    ) is False
    assert not state.commands and not state.resume.exists()


@pytest.mark.parametrize("fault", ["copy", "digest", "read"])
def test_native_package_resume_refuses_fragment_copy_or_digest_failure(
    parallel_native_fixture: _ParallelNativeFixture, monkeypatch: pytest.MonkeyPatch,
    fault: str,
) -> None:
    state = parallel_native_fixture
    assert _run_parallel_fixture(state) == 0
    paths = state.owner._resource_paths()
    payload = json.loads(state.resume.read_text(encoding="utf-8"))
    key = state.owner._batch_key("native-fixture", 1, ["test_fixture.py"])
    saved = paths.root / payload[key]["coverage_fragment"]
    source = paths.root / "original-fragment"
    saved.rename(source)
    original = source.read_bytes()
    entry = {"rc": 0, "coverage_fragment": source.name}
    copy = state.owner.shutil.copy2

    def copy_fragment(incoming: Path, outgoing: Path) -> None:
        if fault == "copy":
            raise OSError("confined copy refusal")
        copy(incoming, outgoing)
        outgoing.write_bytes(outgoing.read_bytes() + b"confined corruption")

    if fault == "read":
        def refuse_digest(_path: Path) -> str:
            raise ValueError("confined digest refusal")
        monkeypatch.setattr(state.owner, "_file_sha256", refuse_digest)
    else:
        monkeypatch.setattr(state.owner.shutil, "copy2", copy_fragment)
    assert state.owner._resume_skip_batch(
        {key: entry}, "native-fixture", 1, ["test_fixture.py"],
        paths.coverage_shards, paths.root,
    ) is False
    assert source.read_bytes() == original
    assert not (paths.coverage_shards / source.name).exists()
    assert len(state.commands) == 1



_RUNTIME_CONTEXT_PROBE = r"""
import importlib
import json
import sys
from pathlib import Path

context, source = sys.argv[1:]
source = Path(source)
assert sys.flags.ignore_environment == 1 and sys.flags.optimize == 0
assert "pytest" not in sys.modules and "conftest" not in sys.modules
before = tuple(sys.path)
prefix = "scripts." if context == "package" else ""
owner = importlib.import_module(prefix + "dependency_profiles")
assert Path(owner.__file__).resolve() == source / "scripts/dependency_profiles.py"
catalog = owner.load_catalog(source)
try:
    owner.inspect_runtime(
        catalog, profile_set="ci", environment=None, python="3.10.99",
    )
except owner.ProfileError as error:
    assert str(error) == "inspection requires an exact supported Python patch version"
else:
    raise AssertionError("unsupported native runtime version was accepted")
peer = importlib.import_module(prefix + "ci_batch_receipts")
assert Path(peer.__file__).resolve() == source / "scripts/ci_batch_receipts.py"
assert peer.file_sha256.__module__ == prefix + "ci_batch_receipts"
expected_path = before
if str(source / "src") not in before:
    expected_path = (str(source / "src"), *before)
assert tuple(sys.path) == expected_path, (expected_path, tuple(sys.path))
if prefix:
    assert source / "scripts" not in {Path(value).resolve() for value in sys.path}
assert "pytest" not in sys.modules and "conftest" not in sys.modules
print(json.dumps({"context": context, "native_version_refusal": "verified"}))
"""


@pytest.mark.parametrize("import_context", ["package", "flat"])
def test_native_runtime_inspection_context_reaches_exact_version_refusal(import_context: str) -> None:
    directory = ROOT if import_context == "package" else ROOT / "scripts"
    result = subprocess.run(
        [sys.executable, "-E", "-W", "error", "-c", _RUNTIME_CONTEXT_PROBE, import_context, str(ROOT)],
        cwd=directory, check=False, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr
    assert json.loads(result.stdout) == {"context": import_context, "native_version_refusal": "verified"}
