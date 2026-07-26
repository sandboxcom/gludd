"""Behavioral tests for the cross-terminal active-work audit command."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

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
