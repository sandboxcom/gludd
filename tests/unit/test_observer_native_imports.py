"""Fresh native observer imports retain package/flat sibling function identity."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_PROBE = r"""
import importlib
import json
import sys
from pathlib import Path

entry, context, source = sys.argv[1:]
source = Path(source)
assert sys.flags.ignore_environment == 1 and sys.flags.optimize == 0
assert "pytest" not in sys.modules and "conftest" not in sys.modules
assert "resource_arbiter" not in sys.modules and "scripts.resource_arbiter" not in sys.modules
initial_path = tuple(sys.path)
prefix = "scripts." if context == "package" else ""
module = importlib.import_module(prefix + entry)
assert Path(module.__file__).resolve() == source / "scripts" / (entry + ".py")
if entry == "pipeline_status":
    # The existing flat pipeline caller deliberately uses its package closure.
    prefix = "scripts."
    active = importlib.import_module(prefix + "active_work_status")
    assert module.active_work_status is active
    cleanup = importlib.import_module(prefix + "kill_owned_gate")
    assert active._process_cwd is cleanup._process_cwd
elif entry == "active_work_status":
    cleanup = importlib.import_module(prefix + "kill_owned_gate")
    assert module._process_cwd is cleanup._process_cwd
else:
    cleanup = module
resources = importlib.import_module(prefix + "resource_arbiter")
assert Path(cleanup.__file__).resolve() == source / "scripts/kill_owned_gate.py"
assert Path(resources.__file__).resolve() == source / "scripts/resource_arbiter.py"
assert cleanup.project_namespace is resources.project_namespace
assert cleanup.resource_root is resources.resource_root
assert cleanup.project_namespace.__module__ == prefix + "resource_arbiter"
assert cleanup.resource_root.__module__ == prefix + "resource_arbiter"
expected_path = initial_path
if context == "flat" and entry == "pipeline_status":
    # Preserve this caller's pre-existing single root insertion; add no path shim.
    expected_path = (str(source), *initial_path)
if entry in {"pipeline_status", "active_work_status"} and str(source / "src") not in expected_path:
    # The existing task-ledger dependency exposes its own native src package.
    expected_path = (str(source / "src"), *expected_path)
assert tuple(sys.path) == expected_path
if context == "package":
    assert source / "scripts" not in {Path(value).resolve() for value in sys.path}
    assert "resource_arbiter" not in sys.modules
assert "pytest" not in sys.modules and "conftest" not in sys.modules
print(json.dumps({"entry": entry, "context": context, "identity": "verified"}))
"""


@pytest.mark.parametrize("import_context", ["package", "flat"])
@pytest.mark.parametrize("entry", ["pipeline_status", "active_work_status", "kill_owned_gate"])
def test_fresh_observer_import_chain_retains_canonical_sibling_identity(
    import_context: str, entry: str,
) -> None:
    directory = ROOT if import_context == "package" else ROOT / "scripts"
    result = subprocess.run(
        [sys.executable, "-E", "-B", "-W", "error", "-c", _PROBE, entry, import_context, str(ROOT)],
        cwd=directory, capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"entry": entry, "context": import_context, "identity": "verified"}


@pytest.mark.parametrize("import_context", ["package", "flat"])
@pytest.mark.parametrize("entry", ["pipeline_status", "active_work_status"])
def test_fresh_observer_cli_help_never_runs_current_observation_or_network(
    tmp_path: Path, import_context: str, entry: str,
) -> None:
    arguments = ["-m", "scripts." + entry] if import_context == "package" else [
        str(ROOT / "scripts" / (entry + ".py")),
    ]
    result = subprocess.run(
        [sys.executable, "-E", "-B", "-W", "error", *arguments, "--help"],
        cwd=ROOT, env=dict(os.environ, GLUDD_PROJECT_ROOT=str(tmp_path), APPLY="0"),
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == "" and "usage:" in result.stdout.lower()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("import_context", ["package", "flat"])
def test_fresh_cleanup_cli_missing_fixture_lock_retains_read_only_noop(
    tmp_path: Path, import_context: str,
) -> None:
    sentinel = tmp_path / "retained.txt"
    sentinel.write_text("retained native fixture\n", encoding="utf-8")
    before = sentinel.read_bytes()
    arguments = ["-m", "scripts.kill_owned_gate"] if import_context == "package" else [
        str(ROOT / "scripts/kill_owned_gate.py"),
    ]
    result = subprocess.run(
        [sys.executable, "-E", "-B", "-W", "error", *arguments],
        cwd=ROOT, env=dict(os.environ, GLUDD_PROJECT_ROOT=str(tmp_path), APPLY="0"),
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        f"[gate-kill] no readable owned gate lock at {tmp_path.resolve()}/.gate-logs/gate-run.lock\n"
    )
    assert sentinel.read_bytes() == before
    assert list(tmp_path.iterdir()) == [sentinel]
