"""Tests for scripts/task_ttl_check.py — stale/frozen task TTL detector."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "task_ttl_check.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("task_ttl_check", SCRIPT)
    assert spec and spec.loader, "could not load task_ttl_check spec"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ttl():
    return _load_module()


def test_script_exists_and_importable(ttl):
    """Deliverable: the script exists and is importable."""
    assert SCRIPT.exists(), f"{SCRIPT} does not exist"
    assert hasattr(ttl, "main")
    assert hasattr(ttl, "find_stale")
    assert hasattr(ttl, "load_deadlines")


def test_identifies_stale_task(ttl, tmp_path, monkeypatch):
    """A task whose elapsed > TTL is reported stale; exit code 1."""
    state = tmp_path / "deadlines.json"
    now_ms = 1_000_000_000_000.0
    state.write_text(json.dumps({"task-stale": now_ms - 600_000}))  # 600s ago

    rc = ttl.main(["--timeout", "300", "--state", str(state)], now_ms=now_ms)
    assert rc == 1

    deadlines = ttl.load_deadlines(str(state))
    stale = ttl.find_stale(deadlines, 300, now=now_ms)
    assert len(stale) == 1
    assert stale[0][0] == "task-stale"
    assert stale[0][1] >= 600.0


def test_fresh_task_passes(ttl, tmp_path):
    """A task under the TTL is fresh; exit code 0."""
    state = tmp_path / "deadlines.json"
    now_ms = 1_000_000_000_000.0
    state.write_text(json.dumps({"task-fresh": now_ms - 10_000}))  # 10s ago

    rc = ttl.main(["--timeout", "300", "--state", str(state)], now_ms=now_ms)
    assert rc == 0

    deadlines = ttl.load_deadlines(str(state))
    stale = ttl.find_stale(deadlines, 300, now=now_ms)
    assert stale == []


def test_empty_deadlines_file_passes(ttl, tmp_path):
    """Edge case: missing or empty deadlines file = pass (exit 0)."""
    missing = tmp_path / "does-not-exist.json"
    assert ttl.load_deadlines(str(missing)) == {}
    rc = ttl.main(["--timeout", "300", "--state", str(missing)])
    assert rc == 0


def test_mixed_stale_and_fresh_only_reports_stale(ttl, tmp_path):
    """A mix yields exit 1 but only the stale task is listed."""
    state = tmp_path / "deadlines.json"
    now_ms = 1_000_000_000_000.0
    state.write_text(
        json.dumps(
            {
                "task-fresh": now_ms - 5_000,    # 5s ago
                "task-stale": now_ms - 700_000,  # 700s ago
            }
        )
    )
    rc = ttl.main(["--timeout", "300", "--state", str(state)], now_ms=now_ms)
    assert rc == 1

    deadlines = ttl.load_deadlines(str(state))
    stale = ttl.find_stale(deadlines, 300, now=now_ms)
    assert {tid for tid, _ in stale} == {"task-stale"}


def test_invalid_json_fail_open(ttl, tmp_path):
    """Corrupt JSON does not crash; treated as empty (fail-open)."""
    state = tmp_path / "broken.json"
    state.write_text("{not valid json")
    assert ttl.load_deadlines(str(state)) == {}
    rc = ttl.main(["--timeout", "300", "--state", str(state)])
    assert rc == 0
