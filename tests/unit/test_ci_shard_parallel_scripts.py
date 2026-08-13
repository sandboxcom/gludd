from __future__ import annotations

import importlib.util
import py_compile
import subprocess
import sys
from pathlib import Path
from types import ModuleType

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
