"""Regression coverage for the bounded E2E runner Make target."""

import subprocess
from pathlib import Path

MAKEFILE = Path(__file__).resolve().parents[2] / "Makefile"


def _target_body(name: str) -> str:
    content = MAKEFILE.read_text(encoding="utf-8")
    start = content.index(f"{name}:")
    remaining = content[start:]
    return remaining.split("\n\n", 1)[0]


def test_e2e_runner_uses_unique_basetemp() -> None:
    body = _target_body("test-e2e")
    assert 'BT="/tmp/gludd-e2e-' in body
    assert '--basetemp=$$FILE_BT' in body


def test_e2e_runner_marks_nested_execution() -> None:
    body = _target_body("test-e2e")
    assert "GLUDD_E2E_ACTIVE=1" in body


def test_e2e_runner_has_per_test_timeout_and_cleanup() -> None:
    body = _target_body("test-e2e")
    assert "--timeout=" in body
    assert 'rm -rf "$$BT"' in body
    assert "exit $$RC" in body


def test_e2e_marker_is_registered() -> None:
    config = (MAKEFILE.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert '"e2e:' in config


def test_e2e_runner_has_suite_watchdog_bounds() -> None:
    body = _target_body("test-e2e")
    assert "run-watched" in body
    assert "E2E_FILE_MAX_SECS" in body
    assert "E2E_STALL_SECS" in body


def test_e2e_runner_log_is_namespaced_by_process() -> None:
    body = _target_body("test-e2e")
    assert 'LOG="/tmp/gludd-e2e-$$$$.log"' in body


def test_e2e_runner_exclusively_owns_full_suite() -> None:
    body = _target_body("test-e2e")
    assert 'LOCK="/tmp/gludd-e2e-run.lock"' in body
    assert 'mkdir "$$LOCK"' in body
    assert "E2E_RUN_BUSY" in body
    assert "exit 75" in body


def test_e2e_runner_releases_its_own_lock_without_killing_owner() -> None:
    body = _target_body("test-e2e")
    assert "trap" in body
    assert 'rm -rf "$$LOCK"' in body
    assert "pkill" not in body


def test_worktree_e2e_cleanup_is_scoped_to_the_requesting_worktree() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    assert "kill-worktree-e2e:" in content
    start = content.index("kill-worktree-e2e:")
    body = content[start:].split("\n\n", 1)[0]
    assert "$(CURDIR)" in body
    assert "pytest tests/e2e/" in body
    assert "pgrep -P" in body
    assert "Refusing to kill unrelated" in body

    assert "tree_contains_local_e2e" in body


def test_e2e_runner_executes_files_in_bounded_serial_processes() -> None:
    body = _target_body("test-e2e")
    assert "e2e_supervisor.py pending" in body
    assert "for test_file in" in body
    assert "E2E_WORKERS" in body


def test_nested_full_unit_suite_is_rejected_during_e2e() -> None:
    body = _target_body("test-unit")
    assert "GLUDD_E2E_ACTIVE" in body
    assert "nested full test-unit" in body


def test_e2e_runner_uses_durable_restart_supervisor() -> None:
    body = _target_body("test-e2e")
    assert "e2e_supervisor.py pending" in body
    assert "e2e_supervisor.py record" in body
    assert "heartbeat-loop" in body
    assert "E2E_HEARTBEAT_SECS" in body


def test_e2e_runner_namespaces_progress_by_shard() -> None:
    body = _target_body("test-e2e")
    assert "E2E_SHARD" in body
    assert "E2E_TOTAL" in body
    assert "e2e-state-shard" in body
    assert "--shard" in body
    assert "--total" in body


def test_e2e_runner_exposes_bounded_file_parallelism() -> None:
    body = _target_body("test-e2e")
    assert "E2E_FILE_WORKERS" in body
    assert "active" in body
    assert "wait" in body


def test_e2e_runner_isolates_artifacts_per_file() -> None:
    body = _target_body("test-e2e")
    assert "FILE_BT" in body
    assert "FILE_LOG" in body
    assert "--basetemp=$$FILE_BT" in body
    assert "LOG=\"$$FILE_LOG\"" in body


def test_e2e_runner_namespaces_mutable_enforcement_state_per_file() -> None:
    """Separate pytest processes must not race on plugin simulator state."""
    body = _target_body("test-e2e")
    assert "GLUDD_E2E_STATE_ROOT=$$FILE_BT/state" in body

    helper = (MAKEFILE.parent / "tests/e2e/enforcement_state.py").read_text(
        encoding="utf-8"
    )
    assert 'os.environ.get("GLUDD_E2E_STATE_ROOT", "/tmp")' in helper

    for relative_path in (
        "tests/e2e/test_enforcement_e2e.py",
        "tests/e2e/test_enforcement_plugin_e2e.py",
    ):
        source = (MAKEFILE.parent / relative_path).read_text(encoding="utf-8")
        assert "from tests.e2e.enforcement_state import" in source


def test_e2e_runner_keeps_file_pool_bounded() -> None:
    body = _target_body("test-e2e")
    assert 'active" -ge "$$FILE_WORKERS' in body
    assert "active=$$((active - 1))" in body


def test_e2e_runner_treats_collection_skip_as_success() -> None:
    body = _target_body("test-e2e")
    assert 'FILE_RC" -eq 5' in body
    assert "return 0" in body


def test_azure_provision_sourced_target_uses_explicit_env_file_contract() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    assert "AZURE_E2E_ENV_FILE ?= /tmp/general-ludd.env" in content
    body = _target_body("test-e2e-azure-provision-sourced")
    assert 'test -r "$(AZURE_E2E_ENV_FILE)"' in body
    assert '. "$(AZURE_E2E_ENV_FILE)"' in body
    assert "AZURE_E2E_VALIDATE_ONLY" in body
    assert "--timeout=3600" in body
    assert ". /tmp/general-ludd.env" not in body


def test_game_provision_target_uses_env_file_and_hour_long_timeout_contract() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    body = _target_body("test-e2e-games-provision")

    assert "GAME_E2E_TIMEOUT_SECS ?= 3600" in content
    assert 'test -r "$(AZURE_E2E_ENV_FILE)"' in body
    assert '. "$(AZURE_E2E_ENV_FILE)"' in body
    assert "AZURE_E2E_VALIDATE_ONLY" in body
    assert '"$(GAME_E2E_TIMEOUT_SECS)" -lt 3600' in body
    assert '--timeout "$(GAME_E2E_TIMEOUT_SECS)"' in body
    assert "--timeout=$(GAME_E2E_TIMEOUT_SECS)" in body
    assert ". /tmp/general-ludd.env" not in body


def test_game_targets_install_declared_media_extra_instead_of_silently_skipping() -> None:
    content = (MAKEFILE.parent / "pyproject.toml").read_text(encoding="utf-8")

    for target in ("test-e2e-games", "test-e2e-games-provision", "test-e2e-games-local"):
        assert "--extra game-e2e" in _target_body(target)
    assert 'game-e2e = [' in content
    assert '"yt-dlp>=' in content


def test_game_provision_target_behavioral_example_never_provisions() -> None:
    result = subprocess.run(
        [
            "make",
            "test-e2e-games-provision",
            "AZURE_E2E_ENV_FILE=tests/fixtures/azure-e2e.env.example",
            "AZURE_E2E_VALIDATE_ONLY=1",
            "GAME_E2E_TIMEOUT_SECS=3600",
        ],
        cwd=MAKEFILE.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "GAME_E2E_ENV_FILE_OK" in result.stdout
    assert "timeout_seconds=3600" in result.stdout
    assert "e2e_log_capture.py" not in result.stdout


def test_game_provision_target_rejects_short_timeout_before_provisioning() -> None:
    result = subprocess.run(
        [
            "make",
            "test-e2e-games-provision",
            "AZURE_E2E_ENV_FILE=tests/fixtures/azure-e2e.env.example",
            "AZURE_E2E_VALIDATE_ONLY=1",
            "GAME_E2E_TIMEOUT_SECS=3599",
        ],
        cwd=MAKEFILE.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "GAME_E2E_TIMEOUT_SECS must be >=3600" in result.stdout
    assert "e2e_log_capture.py" not in result.stdout


def test_azure_cleanup_target_is_bounded_observable_and_env_parameterized() -> None:
    content = MAKEFILE.read_text(encoding="utf-8")
    body = _target_body("azure-cleanup-e2e")

    assert "AZURE_CLEANUP_TIMEOUT_SECS ?= 1800" in content
    assert "AZURE_CLEANUP_POLL_SECS ?=" in content
    assert "AZURE_CLI ?=" in content
    assert 'test -r "$(AZURE_E2E_ENV_FILE)"' in body
    assert '. "$(AZURE_E2E_ENV_FILE)"' in body
    assert ". /tmp/general-ludd.env" not in body
    assert "CLEANUP_SCAN leaked_resources=" in body
    assert "CLEANUP_POLL attempt=" in body
    assert "CLEANUP_VERIFIED leaked_resources=0" in body
    assert "CLEANUP_TIMEOUT" in body


def test_azure_cleanup_inspect_reports_group_and_resource_states() -> None:
    body = _target_body("azure-cleanup-inspect")
    assert 'test -r "$(AZURE_E2E_ENV_FILE)"' in body
    assert '. "$(AZURE_E2E_ENV_FILE)"' in body
    assert "CLEANUP_INSPECT groups=" in body
    assert "CLEANUP_GROUP resource_group=" in body
    assert "properties.provisioningState" in body
    assert "resource list" in body
    assert "monitor activity-log list" in body


def test_azure_cleanup_target_behavioral_example_is_deletion_free() -> None:
    result = subprocess.run(
        [
            "make",
            "azure-cleanup-e2e",
            "AZURE_E2E_ENV_FILE=tests/fixtures/azure-e2e.env.example",
            "AZURE_CLEANUP_TIMEOUT_SECS=1",
            "AZURE_CLEANUP_POLL_SECS=1",
            "AZURE_CLI=true",
        ],
        cwd=MAKEFILE.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "CLEANUP_SCAN leaked_resources=0" in result.stdout
    assert "CLEANUP_VERIFIED leaked_resources=0" in result.stdout


def test_azure_cleanup_target_fails_closed_on_timeout(tmp_path: Path) -> None:
    fake_az = tmp_path / "fake-az"
    fake_az.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  'group list '* ) printf 'gludd-gpu-stuck\\n' ;;\n"
        "  'group delete '* ) exit 0 ;;\n"
        "  * ) exit 64 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_az.chmod(0o700)
    env_file = tmp_path / "azure.env"
    env_file.write_text("ARM_SUBSCRIPTION_ID=test-subscription\n", encoding="utf-8")

    result = subprocess.run(
        [
            "make",
            "azure-cleanup-e2e",
            f"AZURE_E2E_ENV_FILE={env_file}",
            "AZURE_CLEANUP_TIMEOUT_SECS=1",
            "AZURE_CLEANUP_POLL_SECS=1",
            f"AZURE_CLI={fake_az}",
        ],
        cwd=MAKEFILE.parent,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert result.stdout.count("CLEANUP_POLL attempt=") >= 2
    assert "CLEANUP_TIMEOUT" in result.stdout
    assert "CLEANUP_VERIFIED" not in result.stdout
