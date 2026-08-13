"""Parity and release-gate tests for the local named CI shards."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
EXPECTED_SHARDS = (
    "unit-1a1",
    "unit-1a2",
    "unit-1b",
    "unit-1d",
    "unit-2",
    "unit-3",
    "other",
)


def _load_script(name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec = importlib.util.spec_from_file_location(f"gludd_{name}", SCRIPTS / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS))


def test_local_shard_names_match_beta3_ci_matrix() -> None:
    module = _load_script("ci_named_shard_files")

    assert tuple(module.SHARDS) == EXPECTED_SHARDS


def test_local_shard_patterns_match_workflow_matrix() -> None:
    module = _load_script("ci_named_shard_files")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    include = workflow["jobs"]["test-shard"]["strategy"]["matrix"]["include"]
    workflow_shards = {
        item["shard"]: (
            tuple(item["testpaths"].split()),
            tuple(item.get("exclude", "").split()),
        )
        for item in include
        if "testpaths" in item
    }

    assert workflow_shards == module.SHARDS


def test_local_unit_1a1_excludes_isolated_node_runtime_suite() -> None:
    module = _load_script("ci_named_shard_files")
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "build.yml").read_text())
    unit_1a1 = next(
        item
        for item in workflow["jobs"]["test-shard"]["strategy"]["matrix"]["include"]
        if item["shard"] == "unit-1a1"
    )

    assert "tests/unit/test_all_plugins_runtime.py" not in module.expand_shard("unit-1a1")
    assert "*/test_all_plugins_runtime.py" in module.SHARDS["unit-1a1"][1]
    assert module.ISOLATED_TESTS == ("tests/unit/test_all_plugins_runtime.py",)
    assert tuple(str(unit_1a1["isolated_testpaths"]).split()) == module.ISOLATED_TESTS


def test_every_unit_test_file_has_exactly_one_execution_lane() -> None:
    module = _load_script("ci_named_shard_files")
    selected: dict[str, set[str]] = {}
    for shard in EXPECTED_SHARDS:
        files: set[str] = set()
        for token in module.expand_shard(shard):
            path = ROOT / token
            if path.is_dir():
                files.update(item.relative_to(ROOT).as_posix() for item in path.rglob("test_*.py"))
            else:
                files.add(token)
        selected[shard] = files
    selected["isolated"] = set(module.ISOLATED_TESTS)

    for path in sorted((ROOT / "tests" / "unit").rglob("test_*.py")):
        relative = path.relative_to(ROOT).as_posix()
        owners = [shard for shard, files in selected.items() if relative in files]
        assert len(owners) == 1, f"{relative} belongs to {owners}, expected exactly one shard"


def test_shard_slice_supports_inclusive_boundaries() -> None:
    module = _load_script("ci_named_shard_files")
    paths = ["a.py", "b.py", "c.py", "d.py"]

    assert module.slice_paths(paths, from_path="b.py", to_path="c.py") == [
        "b.py",
        "c.py",
    ]


def test_shard_slice_supports_exclusive_boundaries() -> None:
    module = _load_script("ci_named_shard_files")
    paths = ["a.py", "b.py", "c.py", "d.py"]

    assert module.slice_paths(paths, after_path="a.py", before_path="d.py") == [
        "b.py",
        "c.py",
    ]


def test_shard_slice_fails_closed_for_unknown_boundary() -> None:
    module = _load_script("ci_named_shard_files")

    with pytest.raises(SystemExit, match="not present"):
        module.slice_paths(["a.py"], from_path="missing.py")


def test_serial_gate_runner_is_fresh_process_and_coverage_complete() -> None:
    runner = SCRIPTS / "run_ci_shards_serial.py"
    assert runner.is_file()
    source = runner.read_text(encoding="utf-8")

    for token in (
        "adaptive_test.py",
        "COVERAGE_FILE",
        "coverage combine",
        "--cov=general_ludd",
        "--cov-fail-under=0",
        "coverage report",
        "--fail-under=85",
        "audit_coverage.py",
        "--threshold=75",
    ):
        assert token in source


def test_run_gate_delegates_to_serial_named_shards() -> None:
    source = (SCRIPTS / "run_gate.sh").read_text(encoding="utf-8")

    assert "run_ci_shards_serial.py" in source


def test_serial_pytest_command_uses_adaptive_runner_and_isolated_basetemp(
    tmp_path: Path,
) -> None:
    module = _load_script("run_ci_shards_serial")

    command = module._pytest_command(
        "unit-2", ["tests/unit/test_alpha.py"], tmp_path, ["-q"]
    )
    greenlet_command = module._pytest_command(
        "unit-3", ["tests/unit/test_zeta.py"], tmp_path, ["-q"]
    )

    assert command[0] == sys.executable
    assert command[1].endswith("scripts/adaptive_test.py")
    assert "tests/unit/test_alpha.py" in command
    assert "--cov=general_ludd" in command
    assert (
        "--cov=collections/ansible_collections/general_ludd/governance/plugins/module_utils"
        in command
    )
    assert f"--cov-config={module.ROOT / 'pyproject.toml'}" in command
    assert f"--cov-config={module.ROOT / '.coveragerc-greenlet'}" in greenlet_command
    assert f"--basetemp={tmp_path / 'pytest'}" in command


def test_serial_runner_uses_a_fresh_non_coverage_process_for_isolated_tests() -> None:
    module = _load_script("run_ci_shards_serial")

    command = module._isolated_pytest_command([])

    assert command == [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit/test_all_plugins_runtime.py",
        "-v",
    ]
    assert all(not argument.startswith("--cov") for argument in command)


def test_serial_runner_continues_after_a_failed_shard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    launched: list[str] = []
    temp_index = 0

    def fake_mkdtemp(*, prefix: str, dir: str) -> str:
        nonlocal temp_index
        temp_index += 1
        path = tmp_path / f"{prefix}{temp_index}"
        path.mkdir()
        return str(path)

    def fake_run(command: list[str], *, env=None) -> int:
        joined = " ".join(command)
        if "adaptive_test.py" not in joined:
            return 0
        shard = "unit-1a1" if "unit-1a1.py" in joined else "unit-1a2"
        launched.append(shard)
        return 1 if shard == "unit-1a1" else 0

    monkeypatch.setattr(module.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(
        module,
        "_env_for_shard",
        lambda shard, basetemp: {"COVERAGE_FILE": str(basetemp / ".coverage")},
    )
    monkeypatch.setattr(module, "_run_command", fake_run)
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1", "unit-1a2"], [])

    assert result == 1
    assert launched == ["unit-1a1", "unit-1a2"]


def test_serial_runner_records_isolated_failure_and_continues_shards(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_JSON = tmp_path / "coverage.json"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    shard_launched = False

    def fake_run(command: list[str], *, env=None) -> int:
        nonlocal shard_launched
        if "tests/unit/test_all_plugins_runtime.py" in command:
            return 7
        if "adaptive_test.py" in " ".join(command):
            shard_launched = True
        return 0

    monkeypatch.setattr(module, "_run_command", fake_run)
    monkeypatch.setattr(module, "expand_shard", lambda shard: [f"tests/{shard}.py"])
    monkeypatch.setattr(
        module,
        "_env_for_shard",
        lambda shard, basetemp: {"COVERAGE_FILE": str(basetemp / ".coverage")},
    )
    monkeypatch.setattr(module, "_save_shard_coverage", lambda *args: True)
    monkeypatch.setattr(module, "_aggregate_coverage", lambda: 0)

    result = module.run(["unit-1a1"], [])

    assert result == 7
    assert shard_launched is True


def test_serial_runner_fails_closed_when_coverage_erase_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_script("run_ci_shards_serial")
    module.COVERAGE_SHARDS = tmp_path / "coverage-shards"
    module.COVERAGE_AUDIT = tmp_path / "logs" / "coverage.json"
    expanded = False

    def fake_expand(shard: str) -> list[str]:
        nonlocal expanded
        expanded = True
        return [f"tests/{shard}.py"]

    monkeypatch.setattr(module, "expand_shard", fake_expand)
    monkeypatch.setattr(module, "_run_command", lambda *args, **kwargs: 2)

    result = module.run(["unit-1a1"], [])

    assert result == 2
    assert expanded is False
