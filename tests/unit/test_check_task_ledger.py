import os
import tempfile

import pytest


def test_tasks_md_missing_exits_1(tmp_path):
    r = os.system("uv run python scripts/check_task_ledger.py 2>/dev/null")
    assert r != 0, "should exit 1 when TASKS.md missing"

def test_tasks_md_no_session_exits_1(tmp_path):
    f = tmp_path / "TASKS.md"
    f.write_text("# Archived Phases\n")
    r = os.system("uv run python scripts/check_task_ledger.py 2>/dev/null")
    assert r != 0, "should exit 1 when Current Session missing"

def test_tasks_md_valid_passes(tmp_path):
    f = tmp_path / "TASK.md"
    f.write_text("# Archived\n## Current Session\n- [] test\n")
    r = os.system("uv run python scripts/check_task_ledger.py 2>/dev/null")
    assert r == 0, f"should pass, got {r}"

@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory() as d:
        old = os.getcwd()
        os.chdir(d)
        yield __import__("pathlib").Path(d)
        os.chdir(old)
