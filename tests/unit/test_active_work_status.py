"""Behavioral tests for the cross-terminal active-work audit command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.active_work_status import _task_label

ROOT = Path(__file__).resolve().parents[2]


def test_active_work_status_is_auditable_json() -> None:
    result = subprocess.run(
        ["make", "active-work-status"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    payload = json.loads(result.stdout)
    assert isinstance(payload["processes"], list)
    assert isinstance(payload["workstreams"], dict)
    assert all("task" in process for process in payload["processes"])
    assert isinstance(payload["open_task_ids"], list)
    assert isinstance(payload["gate"], dict)
    assert isinstance(payload["git"], dict)
    assert payload["git"]["head"]
    assert payload["audit_contract"]["ps_command"] == "make ps"
    assert payload["audit_contract"]["agent_pids"] is False


def test_process_labels_separate_test_workstreams() -> None:
    assert _task_label("pytest tests/unit/test_example.py") == "unit-tests"
    assert _task_label("pytest tests/e2e/test_opencode_plugin_load.py") == "opencode-e2e"
    assert _task_label("pytest tests/e2e/test_api_routers.py") == "e2e-tests"
    assert _task_label("python scripts/task_watchdog.py") == "watchdog"
