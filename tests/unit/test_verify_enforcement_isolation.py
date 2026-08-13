from __future__ import annotations

import subprocess
from pathlib import Path

from scripts import test_hook_runtime, verify_enforcement

COVERAGE_ENV = {
    "COVERAGE_FILE": "/tmp/global.coverage",
    "COVERAGE_PROCESS_START": "/tmp/global.coveragerc",
    "COV_CORE_DATAFILE": "/tmp/global.coverage",
    "COV_CORE_SOURCE": "general_ludd",
    "PYTEST_ADDOPTS": "--cov=general_ludd -n auto",
}

ISOLATED_STATE_VARS = (
    "GLUDD_DISENGAGE_PATH",
    "GLUDD_FALSE_DONE_BLOCKS_FILE",
    "GLUDD_HOT_MODULE_PREFIX",
    "GLUDD_MAINTHREAD_STREAK_FILE",
    "GLUDD_MULTITASK_STATE_FILE",
    "GLUDD_PERSIST_STOP_BLOCK_FILE",
    "GLUDD_SESSION_STATE",
    "GLUDD_STOP_STATE_FILE",
    "GLUDD_STREAK_FILE",
    "GLUDD_TASK_DEADLINE_STATE",
    "GLUDD_TASK_STALE_FILE",
)


def test_runtime_check_uses_clean_coverage_env_and_process_state_root(
    monkeypatch,
) -> None:
    for name, value in COVERAGE_ENV.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GLUDD_PROJECT_ROOT", "/tmp/unrelated-project")
    monkeypatch.setenv("GLUDD_HOT_MODULE_PREFIX", "/tmp/gludd-hot-")
    monkeypatch.setenv(
        "GLUDD_MULTITASK_STATE_FILE",
        "/tmp/gludd-multitask-state.json",
    )

    captured: dict[str, object] = {}

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        cwd: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout
        state_root = Path(env["GLUDD_RUNTIME_TEST_STATE_DIR"])
        assert state_root.is_dir()
        captured.update(args=args, cwd=cwd, env=dict(env), state_root=state_root)
        return subprocess.CompletedProcess(args, 0, "125 passed\n", "")

    monkeypatch.setattr(verify_enforcement.subprocess, "run", fake_run)

    assert verify_enforcement._check_runtime() == (125, 0, 125, set())

    env = captured["env"]
    assert isinstance(env, dict)
    assert captured["cwd"] == str(verify_enforcement.ROOT)
    assert env["GLUDD_PROJECT_ROOT"] == str(verify_enforcement.ROOT)
    assert env["OPENCODE_SUBAGENT"] == "0"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "PYTEST_ADDOPTS" not in env
    assert not any(
        name.startswith(("COVERAGE_", "COV_CORE_"))
        for name in env
    )

    state_root = captured["state_root"]
    assert isinstance(state_root, Path)
    assert state_root.name.startswith(
        f"gludd-verify-enforcement-{verify_enforcement.os.getpid()}-"
    )
    assert env["TMPDIR"] == str(state_root)
    for name in ISOLATED_STATE_VARS:
        assert Path(env[name]).is_relative_to(state_root), name
    assert not state_root.exists()


def test_runtime_cleanup_redirects_global_state_into_process_root(
    monkeypatch,
    tmp_path: Path,
) -> None:
    deleted: list[str] = []
    monkeypatch.setenv("GLUDD_RUNTIME_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(test_hook_runtime.os, "unlink", deleted.append)

    test_hook_runtime._clean_state_files(
        "/tmp/gludd-block-counter.json",
        "/tmp/unrelated-file",
    )

    assert deleted == [
        str(tmp_path / "gludd-block-counter.json"),
        "/tmp/unrelated-file",
    ]


def test_runtime_invocations_use_unique_hot_module_prefixes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    captured_envs: list[dict[str, str]] = []
    monkeypatch.setenv("GLUDD_RUNTIME_TEST_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GLUDD_HOT_MODULE_PREFIX", "/tmp/gludd-hot-")

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        cwd: str,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout, cwd
        captured_envs.append(dict(env))
        return subprocess.CompletedProcess(args, 0, '{"ok": true}\n', "")

    monkeypatch.setattr(test_hook_runtime.subprocess, "run", fake_run)

    assert test_hook_runtime._run_ts("console.log('{}')") == {"ok": True}
    assert test_hook_runtime._run_ts("console.log('{}')") == {"ok": True}

    prefixes = [env["GLUDD_HOT_MODULE_PREFIX"] for env in captured_envs]
    assert prefixes[0] != prefixes[1]
    assert all(Path(prefix).is_relative_to(tmp_path) for prefix in prefixes)
