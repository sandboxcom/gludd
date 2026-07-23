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


def test_parallel_shard_runner_passes_isolated_env_to_popen() -> None:
    source = (ROOT / "scripts" / "run_ci_shards_parallel.py").read_text(encoding="utf-8")

    assert "env = _env_for_shard(shard, basetemp)" in source
    assert "subprocess.Popen(command, start_new_session=True, env=env)" in source
    assert "SHARD-ISOLATION" in source


def test_parallel_shard_runner_does_not_use_sigterm_cleanup() -> None:
    source = (ROOT / "scripts" / "run_ci_shards_parallel.py").read_text(encoding="utf-8")

    assert "SIGTERM" not in source
    assert "signal.SIGINT" in source
