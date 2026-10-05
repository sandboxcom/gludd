"""Controller Python boundary contracts for the E2E test-generation collection."""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

COLLECTION_ROOT = Path(__file__).resolve().parents[2]
ROLES_ROOT = COLLECTION_ROOT / "roles"
ACTION_FQCN = "general_ludd.e2e_test_gen.e2e_pipeline"
PIPELINE_ROLES = {
    "analyze_code_paths": "analyze",
    "generate_scenarios": "generate",
    "validate_scenarios": "validate",
    "write_e2e_tests": "write_tests",
    "verify_coverage": "verify_coverage",
}
CORE_IMPORT = re.compile(r"(?:from|import)\s+general_ludd(?:\.|\s)")
SYS_PATH_MUTATION = re.compile(r"\bsys\.path\.(?:insert|append|extend)\s*\(")
AMBIENT_PYTHON = re.compile(
    r"(?:^|[\s:'\"=])(?:/usr/bin/python3?|/usr/local/bin/python3?|python3?|py)(?:\s|$)"
)


def _load_runner() -> Any:
    collections_root = str(COLLECTION_ROOT.parents[2])
    sys.path.insert(0, collections_root)
    try:
        return importlib.import_module(
            "ansible_collections.general_ludd.e2e_test_gen.plugins.module_utils.pipeline_runner"
        )
    finally:
        sys.path.remove(collections_root)


def _load_action() -> Any:
    collections_root = str(COLLECTION_ROOT.parents[2])
    sys.path.insert(0, collections_root)
    try:
        return importlib.import_module(
            "ansible_collections.general_ludd.e2e_test_gen.plugins.action.e2e_pipeline"
        )
    finally:
        sys.path.remove(collections_root)


def test_roles_use_packaged_fqcn_action_without_ambient_python() -> None:
    for role, operation in PIPELINE_ROLES.items():
        tasks_path = ROLES_ROOT / role / "tasks/main.yml"
        serialized = str(
            yaml.safe_dump(yaml.safe_load(tasks_path.read_text(encoding="utf-8")), sort_keys=False)
        )
        assert ACTION_FQCN in serialized
        assert f"operation: {operation}" in serialized
        assert "ansible.builtin.command" not in serialized
        assert AMBIENT_PYTHON.search(serialized) is None


def test_release_python_has_no_core_import_or_path_mutation() -> None:
    findings: list[str] = []
    for path in sorted(COLLECTION_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if CORE_IMPORT.search(source) or SYS_PATH_MUTATION.search(source):
            findings.append(path.relative_to(COLLECTION_ROOT).as_posix())

    assert findings == []


def test_action_resolves_scripts_from_packaged_collection_root() -> None:
    action_path = COLLECTION_ROOT / "plugins/action/e2e_pipeline.py"
    source = action_path.read_text(encoding="utf-8")

    assert "pipeline_runner" in source
    assert "Path(__file__).resolve().parents[2]" in source
    assert "general_ludd.agent" not in source


def test_failed_pipeline_does_not_publish_candidate_artifact(tmp_path: Path) -> None:
    runner = _load_runner()
    candidate = tmp_path / "candidate.json"

    with pytest.raises(runner.PipelineExecutionError, match="File not found"):
        runner.run_pipeline(
            COLLECTION_ROOT,
            "analyze",
            {"target_module": str(tmp_path / "missing.py"), "output": str(candidate)},
        )

    assert not candidate.exists()


def test_pipeline_round_trip_publishes_only_completed_artifacts(tmp_path: Path) -> None:
    runner = _load_runner()
    source = tmp_path / "accounts.py"
    symbols = tmp_path / "symbols.json"
    scenarios = tmp_path / "scenarios.json"
    validated = tmp_path / "validated.json"
    generated = tmp_path / "generated"
    manifest = tmp_path / "manifest.json"
    source.write_text(
        "def create_user():\n    return {'id': 1}\n\ndef delete_user():\n    return None\n",
        encoding="utf-8",
    )

    analyzed = runner.run_pipeline(
        COLLECTION_ROOT,
        "analyze",
        {"target_module": str(source), "output": str(symbols)},
    )
    generated_scenarios = runner.run_pipeline(
        COLLECTION_ROOT,
        "generate",
        {"symbols_file": str(symbols), "output": str(scenarios)},
    )
    validated_scenarios = runner.run_pipeline(
        COLLECTION_ROOT,
        "validate",
        {
            "scenarios_file": str(scenarios),
            "output": str(validated),
            "daemon_url": "http://127.0.0.1:8000",
            "research_time_range": "year",
            "confidence_threshold": 0.0,
            "mock": True,
            "psk": "test-only-psk",
        },
    )
    written = runner.run_pipeline(
        COLLECTION_ROOT,
        "write_tests",
        {
            "scenarios_file": str(validated),
            "output_dir": str(generated),
            "manifest": str(manifest),
            "test_file_prefix": "test_e2e_generated_",
        },
    )

    assert analyzed["artifact"]["status"] == "completed"
    assert generated_scenarios["artifact"]["scenario_count"] >= 1
    assert validated_scenarios["artifact"]["status"] == "completed"
    assert written["artifact"]["scenario_count"] >= 1
    published = json.loads(manifest.read_text(encoding="utf-8"))
    assert published["test_files"]
    assert all(Path(item["file"]).is_file() for item in published["test_files"])


def test_verify_pipeline_uses_bounded_explicit_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _load_runner()
    output = tmp_path / "coverage.json"
    captured: dict[str, object] = {}

    def complete(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update({"command": command, **kwargs})
        stage_output = Path(command[command.index("--output") + 1])
        stage_output.write_text('{"status": "completed", "coverage": 91}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, '{"status": "completed"}\n', "")

    monkeypatch.setattr(runner.subprocess, "run", complete)
    result = runner.run_pipeline(
        COLLECTION_ROOT,
        "verify_coverage",
        {
            "test_dir": str(tmp_path / "tests"),
            "source_module": "accounts",
            "output": str(output),
            "threshold": 90,
            "pytest_timeout": 12,
            "test_file_prefix": "test_e2e_",
            "scenarios_file": str(tmp_path / "scenarios.json"),
        },
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[0] == sys.executable
    assert command[command.index("--threshold") + 1] == "90"
    assert captured["timeout"] == 360
    assert result["artifact"] == {"status": "completed", "coverage": 91}


def test_runner_rejects_invalid_contracts_before_execution(tmp_path: Path) -> None:
    runner = _load_runner()

    with pytest.raises(ValueError, match="unsupported"):
        runner.run_pipeline(COLLECTION_ROOT, "unknown", {})
    with pytest.raises(ValueError, match="symbols_file or target_module"):
        runner.run_pipeline(COLLECTION_ROOT, "generate", {"output": str(tmp_path / "out.json")})
    with pytest.raises(ValueError, match="research_categories"):
        runner.run_pipeline(
            COLLECTION_ROOT,
            "validate",
            {
                "scenarios_file": "scenarios.json",
                "output": "validated.json",
                "daemon_url": "http://127.0.0.1:8000",
                "research_time_range": "year",
                "research_categories": "general",
            },
        )
    with pytest.raises(ValueError, match="positive integer"):
        runner.run_pipeline(
            COLLECTION_ROOT,
            "analyze",
            {"target_module": "source.py", "output": "symbols.json", "timeout": False},
        )


def test_action_module_preserves_ansible_result_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action_module = _load_action()
    monkeypatch.setattr(action_module.ActionBase, "run", lambda *_args, **_kwargs: {"base": True})
    captured: dict[str, object] = {}

    def run_pipeline(root: Path, operation: str, arguments: dict[str, object]) -> dict[str, object]:
        captured.update(root=root, operation=operation, arguments=arguments)
        return {"operation": operation, "artifact": {"status": "completed"}}

    monkeypatch.setattr(action_module, "run_pipeline", run_pipeline)
    action = object.__new__(action_module.ActionModule)
    action._task = SimpleNamespace(args={"operation": "write_tests", "output_dir": "/tmp/tests"})

    assert action.run() == {
        "base": True,
        "operation": "write_tests",
        "artifact": {"status": "completed"},
        "changed": True,
    }
    assert captured["root"] == COLLECTION_ROOT
    assert captured["arguments"] == {"output_dir": "/tmp/tests"}

    action._task = SimpleNamespace(args={"operation": ""})
    assert action.run()["failed"] is True

    def fail(*_args: object, **_kwargs: object) -> None:
        raise action_module.PipelineExecutionError("candidate failed")

    monkeypatch.setattr(action_module, "run_pipeline", fail)
    action._task = SimpleNamespace(args={"operation": "analyze"})
    result = action.run()
    assert result["failed"] is True
    assert result["msg"] == "candidate failed"


def test_collection_declares_authenticated_daemon_dependency() -> None:
    galaxy = yaml.safe_load((COLLECTION_ROOT / "galaxy.yml").read_text(encoding="utf-8"))

    assert galaxy["dependencies"]["general_ludd.agent"] == ">=0.2.0"
