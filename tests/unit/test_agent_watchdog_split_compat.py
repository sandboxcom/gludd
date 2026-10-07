"""Compatibility contract for the split agent-watchdog implementation."""

from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
FACADE = ROOT / "scripts" / "agent_watchdog.py"
COMPONENT_ROOT = ROOT / "scripts" / "watchdog_components"


def _load_facade(module_name: str = "agent_watchdog_split_contract"):
    spec = importlib.util.spec_from_file_location(module_name, FACADE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_watchdog_facade_and_components_stay_below_split_budget() -> None:
    paths = [
        FACADE,
        COMPONENT_ROOT / "types.py",
        COMPONENT_ROOT / "lease.py",
        COMPONENT_ROOT / "task_health.py",
        COMPONENT_ROOT / "enforcement.py",
        COMPONENT_ROOT / "release.py",
    ]
    assert all(path.is_file() for path in paths)
    counts = {path.name: len(path.read_text().splitlines()) for path in paths}
    assert counts["agent_watchdog.py"] < 2_500, counts
    assert all(count < 2_000 for count in counts.values()), counts


def test_pure_facade_exports_retain_component_identity() -> None:
    facade = _load_facade()
    types = importlib.import_module("scripts.watchdog_components.types")
    assert facade.State is types.State
    assert facade.classify_tail is types.classify_tail
    assert facade.scan_tasks_dir is types.scan_tasks_dir


def test_wrapped_runtime_entrypoints_retain_call_signatures() -> None:
    facade = _load_facade("agent_watchdog_signature_contract")
    cycle = inspect.signature(facade.check_and_reset)
    assert list(cycle.parameters) == ["secrets_check"]
    assert cycle.parameters["secrets_check"].kind is inspect.Parameter.KEYWORD_ONLY
    assert cycle.parameters["secrets_check"].default is None
    assert str(inspect.signature(facade.acquire_watchdog_lock)).startswith("(*, lock_path:")
    assert str(inspect.signature(facade.main)).startswith("(argv:")


def test_components_never_import_the_facade() -> None:
    for path in COMPONENT_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        assert "scripts.agent_watchdog" not in imports, path


def test_component_first_import_and_cli_subprocess_are_compatible(
    tmp_path: Path,
) -> None:
    import_script = (
        "import scripts.watchdog_components.types; "
        "import scripts.agent_watchdog as aw; "
        "assert aw.State is scripts.watchdog_components.types.State"
    )
    imported = subprocess.run(
        [sys.executable, "-c", import_script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr

    stale = tmp_path / "stale.output"
    stale.write_text("continuing with remaining work:\n")
    stale.touch()
    completed = subprocess.run(
        [sys.executable, str(FACADE), str(tmp_path), "--count-stalled"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "0"
