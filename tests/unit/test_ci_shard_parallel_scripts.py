from __future__ import annotations

import importlib.util
import py_compile
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_NAMES = (
    "ci_shards_parallel_status.py",
    "run_ci_shard_summary.py",
    "run_ci_shards_parallel.py",
    "run_ci_shards_serial.py",
    "start_ci_shards_parallel_bg.py",
)


def test_local_ci_shard_helper_scripts_compile() -> None:
    for script_name in SCRIPT_NAMES:
        py_compile.compile(str(ROOT / "scripts" / script_name), doraise=True)


def test_ci_shards_parallel_status_fails_closed_without_state(tmp_path: Path) -> None:
    script = ROOT / "scripts" / "ci_shards_parallel_status.py"

    result = subprocess.run(
        [sys.executable, str(script), "--lines", "1"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "CI-SHARDS-STATUS missing state" in result.stdout


def _load_script(script_name: str) -> ModuleType:
    scripts_dir = ROOT / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location(
            f"gludd_{script_name.replace(chr(46), chr(95))}",
            scripts_dir / script_name,
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(scripts_dir))


def test_parallel_shard_runner_namespaces_gludd_state_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GLUDD_STOP_STATE_FILE", "/tmp/gludd-stop-state.json")
    module = _load_script("run_ci_shards_parallel.py")
    env_for_shard = module._env_for_shard
    state_vars = module.SHARD_STATE_ENV_VARS

    basetemp = tmp_path / "base"
    env = env_for_shard("unit-2", basetemp)

    assert env["TMPDIR"] == str(basetemp / "tmp")
    assert env["GLUDD_SHARD_NAME"] == "unit-2"
    assert env["GLUDD_SHARD_STATE_DIR"] == str(basetemp / "state")
    assert env["GLUDD_STOP_STATE_FILE"] != "/tmp/gludd-stop-state.json"
    assert env["GLUDD_STOP_STATE_FILE"].startswith(str(basetemp / "state"))
    assert env["GLUDD_HOT_MODULE_PREFIX"].startswith(str(basetemp / "state"))
    assert (basetemp / "tmp").is_dir()
    assert (basetemp / "state").is_dir()
    for name in state_vars:
        assert env[name].startswith(str(basetemp / "state")), name


def test_parallel_shard_runner_keeps_tmpdir_outside_pytest_basetemp(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_parallel.py")
    workspace = tmp_path / "shard"
    env = module._env_for_shard("unit-3", workspace)
    pytest_basetemp = module._pytest_basetemp(workspace)

    assert pytest_basetemp.parent == workspace
    assert pytest_basetemp != workspace
    assert not Path(env["TMPDIR"]).is_relative_to(pytest_basetemp)


def test_parallel_shard_command_uses_nested_pytest_basetemp(monkeypatch) -> None:
    module = _load_script("run_ci_shards_parallel.py")
    monkeypatch.setattr(module, "expand_shard", lambda _shard: ["tests/unit/test_example.py"])
    monkeypatch.setattr(module.os, "getpid", lambda: 4242)

    command, workspace = module._command_for_shard("unit-3", [], 0)
    basetemp_arg = next(arg for arg in command if arg.startswith("--basetemp="))
    junit_arg = next(arg for arg in command if arg.startswith("--junitxml="))

    assert Path(basetemp_arg.removeprefix("--basetemp=")) == module._pytest_basetemp(workspace)
    assert Path(junit_arg.removeprefix("--junitxml=")).parent == workspace


def test_parallel_shard_runner_passes_isolated_env_to_popen() -> None:
    source = (ROOT / "scripts" / "run_ci_shards_parallel.py").read_text(encoding="utf-8")

    assert "env = _env_for_shard(shard, basetemp)" in source
    assert "subprocess.Popen(command, start_new_session=True, env=env)" in source
    assert "SHARD-ISOLATION" in source


def test_parallel_shard_runner_does_not_use_sigterm_cleanup() -> None:
    source = (ROOT / "scripts" / "run_ci_shards_parallel.py").read_text(encoding="utf-8")

    assert "SIGTERM" not in source
    assert "signal.SIGINT" in source


def test_junit_summary_counts_and_first_failure_ids(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_parallel.py")
    report = tmp_path / "unit-1a.xml"
    report.write_text(
        """<testsuite tests=\"4\" failures=\"1\" errors=\"0\" skipped=\"1\">
        <testcase classname=\"tests.test_ok\" name=\"test_pass\" />
        <testcase classname=\"tests.test_bad\" name=\"test_fail\"><failure /></testcase>
        <testcase classname=\"tests.test_skip\" name=\"test_skip\"><skipped /></testcase>
        <testcase classname=\"tests.test_ok\" name=\"test_pass_two\" />
        </testsuite>""",
        encoding="utf-8",
    )

    assert module._read_junit_summary(report) == {
        "passed": 2,
        "failed": 1,
        "skipped": 1,
        "first_failure_ids": ["tests.test_bad::test_fail"],
    }


def test_persist_shard_summary_is_durable_and_namespaced(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_parallel.py")

    path = module._persist_shard_summary(
        tmp_path,
        "unit/1",
        7,
        {"passed": 3, "failed": 1, "skipped": 0, "first_failure_ids": ["x::test_y"]},
    )

    assert path == tmp_path / "unit_1.json"
    assert path.exists()
    assert path.read_text(encoding="utf-8").find('"shard": "unit/1"') >= 0
    assert '"returncode": 7' in path.read_text(encoding="utf-8")


def test_shard_argument_and_worker_parsing() -> None:
    module = _load_script("run_ci_shards_parallel.py")

    assert module._parse_shards("unit-1a, unit-2 unit-3") == ["unit-1a", "unit-2", "unit-3"]
    with pytest.raises(SystemExit, match="no shards supplied"):
        module._parse_shards(" , ")

    assert module._has_xdist_worker_arg(["-n", "2"])
    assert module._has_xdist_worker_arg(["-n4"])
    assert module._has_xdist_worker_arg(["--numprocesses=3"])
    assert not module._has_xdist_worker_arg(["-q"])


def test_command_rejects_empty_shard(monkeypatch) -> None:
    module = _load_script("run_ci_shards_parallel.py")
    monkeypatch.setattr(module, "expand_shard", lambda _shard: [])

    with pytest.raises(SystemExit, match="expanded to no files"):
        module._command_for_shard("empty", [], 1)


def test_missing_junit_summary_is_zeroed(tmp_path: Path) -> None:
    module = _load_script("run_ci_shards_parallel.py")

    assert module._read_junit_summary(tmp_path / "missing.xml") == {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "first_failure_ids": [],
    }


def test_run_persists_results_and_cleans_workspaces(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    module = _load_script("run_ci_shards_parallel.py")
    workspaces: list[Path] = []

    def command_for_shard(shard: str, _args: list[str], _workers: int):
        workspace = tmp_path / shard
        workspace.mkdir()
        workspaces.append(workspace)
        return ["pytest", shard], workspace

    class FinishedProcess:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.pid = 5000 + returncode

        def poll(self) -> int:
            return self.returncode

    processes = iter([FinishedProcess(0), FinishedProcess(3)])
    monkeypatch.setattr(module, "_command_for_shard", command_for_shard)
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda _command, start_new_session, env: next(processes),
    )
    summary_dir = tmp_path / "summaries"
    monkeypatch.setenv("GLUDD_SHARD_SUMMARY_DIR", str(summary_dir))

    assert module.run(["ok", "bad"], [], 0, 5) == 3
    assert (summary_dir / "ok.json").is_file()
    assert (summary_dir / "bad.json").is_file()
    assert all(not workspace.exists() for workspace in workspaces)
    output = capsys.readouterr().out
    assert "SHARD-PASS shard=ok" in output
    assert "SHARD-FAIL shard=bad" in output


def test_run_terminates_children_on_unexpected_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_script("run_ci_shards_parallel.py")
    workspace = tmp_path / "pending"
    workspace.mkdir()

    class PendingProcess:
        pid = 9001

        @staticmethod
        def poll() -> None:
            return None

    monkeypatch.setattr(
        module,
        "_command_for_shard",
        lambda *_args: (["pytest", "pending"], workspace),
    )
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: PendingProcess())
    terminated: list[object] = []
    monkeypatch.setattr(module, "_terminate_all", lambda running: terminated.extend(running))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError, match="boom"):
        module.run(["pending"], [], 1, 5)

    assert len(terminated) == 1


def test_run_reports_signal_exit(tmp_path: Path, monkeypatch, capsys) -> None:
    module = _load_script("run_ci_shards_parallel.py")

    class SignaledProcess:
        pid = 6000

        def poll(self) -> int:
            return -2

    def command_for_shard(shard: str, _args: list[str], _workers: int):
        workspace = tmp_path / shard
        workspace.mkdir()
        return ["pytest", shard], workspace

    monkeypatch.setattr(module, "_command_for_shard", command_for_shard)
    monkeypatch.setattr(
        module.subprocess,
        "Popen",
        lambda _command, start_new_session, env: SignaledProcess(),
    )
    monkeypatch.setenv("GLUDD_SHARD_SUMMARY_DIR", str(tmp_path / "summaries"))

    assert module.run(["signal"], [], 0, 5) == 130
    assert "SHARD-SIGNAL shard=signal signal=SIGINT rc=-2" in capsys.readouterr().out


def test_terminate_all_escalates_process_group(tmp_path: Path, monkeypatch) -> None:
    module = _load_script("run_ci_shards_parallel.py")

    class RunningProcess:
        pid = 7000

        def poll(self):
            return None

    process = RunningProcess()
    item = module.RunningShard("live", process, tmp_path, ["pytest"], tmp_path / "junit.xml")
    signals: list[tuple[int, int]] = []
    moments = iter([0.0, 11.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module.os, "killpg", lambda pid, sig: signals.append((pid, sig)))

    module._terminate_all([item])

    assert signals == [(7000, module.signal.SIGINT), (7000, module.signal.SIGKILL)]


def test_main_forwards_parsed_arguments(monkeypatch) -> None:
    module = _load_script("run_ci_shards_parallel.py")
    observed: dict[str, object] = {}

    def fake_run(shards, pytest_args, workers_per_shard, heartbeat_seconds):
        observed.update(
            shards=shards,
            pytest_args=pytest_args,
            workers_per_shard=workers_per_shard,
            heartbeat_seconds=heartbeat_seconds,
        )
        return 7

    monkeypatch.setattr(module, "run", fake_run)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "run_ci_shards_parallel.py",
            "--shards",
            "unit-1a unit-2",
            "--pytest-args=-q -x",
            "--workers-per-shard",
            "2",
            "--heartbeat-seconds",
            "9",
        ],
    )

    assert module.main() == 7
    assert observed == {
        "shards": ["unit-1a", "unit-2"],
        "pytest_args": ["-q", "-x"],
        "workers_per_shard": 2,
        "heartbeat_seconds": 9,
    }
