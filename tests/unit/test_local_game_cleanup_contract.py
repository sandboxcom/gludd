from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any, cast

import yaml

ROOT = Path(__file__).resolve().parents[2]
STOP_PLAYBOOK = ROOT / "playbooks/local_model_stop.yml"
GAME_PROFILE = ROOT / "requirements/profiles/game-e2e/pyproject.toml"
GAME_LOCK = ROOT / "requirements/profiles/game-e2e/uv.lock"
DEPENDENCY_PROFILES = ROOT / "config/dependency_profiles.toml"
GENERATION_TASKS = (
    ROOT
    / "collections/ansible_collections/general_ludd/agent/roles/local_game_gen"
    / "tasks/generate_and_verify.yml"
)


def _stop_tasks() -> list[dict[str, Any]]:
    plays = cast(list[dict[str, Any]], yaml.safe_load(STOP_PLAYBOOK.read_text()))
    return cast(list[dict[str, Any]], plays[0]["tasks"])


def _terminal_cleanup() -> list[dict[str, Any]]:
    lifecycle = next(task for task in _stop_tasks() if "always" in task)
    return cast(list[dict[str, Any]], lifecycle["always"])


def _flatten(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for task in tasks:
        flattened.append(task)
        for key in ("block", "rescue", "always"):
            nested = task.get(key)
            if isinstance(nested, list):
                flattened.extend(_flatten(cast(list[dict[str, Any]], nested)))
    return flattened


def test_absent_pid_file_is_an_explicit_noop() -> None:
    tasks = _flatten(_stop_tasks())
    pid_stat = next(task for task in tasks if task.get("register") == "_pid_file_stat")
    pid_slurp = next(task for task in tasks if task.get("register") == "_pid_slurp")

    assert pid_stat["ansible.builtin.stat"]["follow"] is False
    assert any(
        "_pid_file_stat.stat.exists" in condition
        for condition in pid_slurp["when"]
    )
    assert pid_slurp["failed_when"] is False


def test_absent_pid_file_replays_cleanly_with_ansible(tmp_path: Path) -> None:
    ansible_playbook = ROOT / ".venv/bin/ansible-playbook"
    assert ansible_playbook.is_file(), "development profile must provide ansible-playbook"
    local_tmp = tmp_path / "local"
    remote_tmp = tmp_path / "remote"
    local_tmp.mkdir()
    remote_tmp.mkdir()
    missing_pid_file = tmp_path / "never-created.pid"
    environment = {
        **os.environ,
        "ANSIBLE_LOCAL_TEMP": str(local_tmp),
        "ANSIBLE_REMOTE_TEMP": str(remote_tmp),
    }

    completed = subprocess.run(
        [
            str(ansible_playbook),
            "-i",
            "localhost,",
            "-c",
            "local",
            str(STOP_PLAYBOOK),
            "--extra-vars",
            json.dumps(
                {
                    "server_id": "cleanup-contract",
                    "server_pid": None,
                    "pid_file": str(missing_pid_file),
                }
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "local model cleanup completed for cleanup-contract" in completed.stdout
    assert not missing_pid_file.exists()


def test_signal_and_pid_removal_are_terminal_cleanup() -> None:
    terminal = _flatten(_terminal_cleanup())
    term = next(task for task in terminal if task.get("register") == "_term_result")
    remove = next(
        task
        for task in terminal
        if task.get("ansible.builtin.file", {}).get("state") == "absent"
    )

    assert term["ansible.builtin.command"]["argv"][1] == "-TERM"
    assert remove["ansible.builtin.file"]["path"] == "{{ pid_file }}"


def test_pid_cleanup_uses_argv_and_rejects_non_numeric_identity() -> None:
    tasks = _flatten(_stop_tasks())
    assertion = next(
        task
        for task in tasks
        if "ansible.builtin.assert" in task
        and "_local_model_pid_candidate" in str(task["ansible.builtin.assert"])
    )
    commands = [
        cast(dict[str, Any], task["ansible.builtin.command"])
        for task in tasks
        if "ansible.builtin.command" in task
    ]

    assert "^[1-9][0-9]*$" in str(assertion)
    assert commands
    assert all("argv" in command for command in commands)


def test_game_profile_and_lock_require_pillow_security_floor() -> None:
    profile = tomllib.loads(GAME_PROFILE.read_text())
    lock = tomllib.loads(GAME_LOCK.read_text())
    dependency_profiles = tomllib.loads(DEPENDENCY_PROFILES.read_text())

    assert "pillow>=12.3.0" in profile["project"]["dependencies"]
    assert "game-e2e" in dependency_profiles["sets"]["game-e2e"]["profiles"]
    assert "game-e2e" in dependency_profiles["sets"]["e2e-all"]["profiles"]
    pillow = next(package for package in lock["package"] if package["name"] == "pillow")
    assert pillow["version"] == "12.3.0"
    assert pillow["sdist"]["hash"].startswith("sha256:")


def test_cleanup_does_not_weaken_generation_failures() -> None:
    tasks = cast(list[dict[str, Any]], yaml.safe_load(GENERATION_TASKS.read_text()))
    commands = [
        task
        for task in _flatten(tasks)
        if "ansible.builtin.command" in task
        and str(task.get("name", "")).startswith("Verify")
    ]

    assert len(commands) >= 3
    assert all(task.get("failed_when") is not False for task in commands)
