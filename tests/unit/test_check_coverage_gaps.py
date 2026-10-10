"""Regression tests for the static coverage-gap mapper."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from scripts import check_coverage_gaps as checker

EXPECTED_SPLIT_MAPPINGS = {
    "general_ludd.cli_commands.parser": (
        "general_ludd.cli",
        "tests/unit/test_cli_module_split.py",
    ),
    "general_ludd.decision_codification.artifact_auth": (
        "general_ludd.decision_codification.artifact_store",
        "tests/unit/test_decision_codification_artifact_store.py",
    ),
    "general_ludd.decision_codification.durable_generation": (
        "general_ludd.decision_codification.durable_feedback",
        "tests/unit/test_decision_codification_durable.py",
    ),
    "general_ludd.decision_codification.durable_observability": (
        "general_ludd.decision_codification.durable",
        "tests/unit/test_decision_codification_observability.py",
    ),
    "general_ludd.decision_codification.runtime_context": (
        "general_ludd.decision_codification.runtime",
        "tests/unit/test_decision_codification_runtime.py",
    ),
    "general_ludd.decision_codification.shared_generation_candidates": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_codec": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_coordination": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_history": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_outcomes": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_revocation": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_rollback": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_transactions": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.decision_codification.shared_generation_usage": (
        "general_ludd.decision_codification.shared_generation",
        "tests/unit/test_decision_codification_shared_generation.py",
    ),
    "general_ludd.daemon_components.ports": (
        "general_ludd.daemon",
        "tests/unit/test_daemon_module_split_compat.py",
    ),
    "general_ludd.event_loop.compute_lifecycle": (
        "general_ludd.event_loop.loop",
        "tests/unit/test_event_loop_module_split.py",
    ),
    "general_ludd.event_loop.decision_completion": (
        "general_ludd.event_loop.loop",
        "tests/unit/test_event_loop_module_split.py",
    ),
    "general_ludd.event_loop.decision_validation": (
        "general_ludd.event_loop.decision_reconciliation",
        "tests/unit/test_validation_request_orchestration.py",
    ),
    "general_ludd.event_loop.execution_dispatch": (
        "general_ludd.event_loop.loop",
        "tests/unit/test_event_loop_module_split.py",
    ),
    "general_ludd.event_loop.review_dispatch": (
        "general_ludd.event_loop.loop",
        "tests/unit/test_event_loop_module_split.py",
    ),
    "general_ludd.event_loop.self_improve_lifecycle": (
        "general_ludd.event_loop.loop",
        "tests/unit/test_event_loop_module_split.py",
    ),
    "general_ludd.event_loop.tick_lifecycle": (
        "general_ludd.event_loop.loop",
        "tests/unit/test_event_loop_module_split.py",
    ),
    "general_ludd.models.freellmapi_sync_plans": (
        "general_ludd.models.freellmapi_sync_verifier",
        "tests/unit/test_freellmapi_sync_verifier.py",
    ),
    "general_ludd.pipeline.merge_io": (
        "general_ludd.pipeline.daemon_adapters",
        "tests/unit/test_pipeline_daemon_adapters.py",
    ),
    "general_ludd.pipeline.merge_runtime": (
        "general_ludd.pipeline.daemon_adapters",
        "tests/unit/test_pipeline_daemon_adapters.py",
    ),
    "general_ludd.process.registry_identity": (
        "general_ludd.process.registry",
        "tests/unit/test_process_registry.py",
    ),
    "general_ludd.process.signal_policy": (
        "general_ludd.process.registry",
        "tests/unit/test_process_registry.py",
    ),
    "general_ludd.reload.worker_liveness": (
        "general_ludd.reload.worker_broadcast",
        "tests/unit/test_worker_liveness_lease.py",
    ),
    "general_ludd.review.estimation_reporting": (
        "general_ludd.review.estimation_tracker",
        "tests/unit/test_estimation_tracker.py",
    ),
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    source = root / "src" / "general_ludd" / "widgets"
    tests = root / "tests" / "unit"
    source.mkdir(parents=True)
    tests.mkdir(parents=True)
    (source / "engine.py").write_text("class Engine:\n    pass\n")
    (source / "other.py").write_text("class Other:\n    pass\n")
    (source / "__init__.py").write_text(
        "from general_ludd.widgets.engine import Engine\nfrom general_ludd.widgets.other import Other\n"
    )
    monkeypatch.setattr(checker, "PROJECT_ROOT", root)
    monkeypatch.setattr(checker, "SRC_DIR", root / "src" / "general_ludd")
    monkeypatch.setattr(checker, "TESTS_DIR", tests)
    return root


def _status(project: Path, module: str = "engine.py") -> checker.CoverageResult:
    return checker._check_module(project / "src" / "general_ludd" / "widgets" / module)


def test_finds_direct_import_in_differently_named_test(project: Path) -> None:
    (project / "tests" / "unit" / "test_widget_behavior.py").write_text(
        "from general_ludd.widgets.engine import Engine\n\ndef test_engine():\n    assert Engine() is not None\n"
    )

    result = _status(project)

    assert result["status"] == "OK"
    assert result["test_file"] == "tests/unit/test_widget_behavior.py"


def test_resolves_from_package_reexport_to_defining_module(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_public_api.py").write_text(
        "from general_ludd.widgets import Engine\n\ndef test_engine():\n    assert Engine() is not None\n"
    )

    assert _status(project)["status"] == "OK"


def test_resolves_used_attribute_from_imported_package_alias(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_public_api.py").write_text(
        "import general_ludd.widgets as widgets\n\ndef test_engine():\n    assert widgets.Engine() is not None\n"
    )

    assert _status(project)["status"] == "OK"
    assert _status(project, "other.py")["status"] == "UNTESTED"


def test_resolves_used_attribute_reexported_by_regular_module(
    project: Path,
) -> None:
    """A stable type module remains covered through its public behavior module."""
    widgets = project / "src" / "general_ludd" / "widgets"
    (widgets / "types.py").write_text("class Snapshot:\n    pass\n")
    (widgets / "engine.py").write_text("from general_ludd.widgets.types import Snapshot\n")
    (project / "tests" / "unit" / "test_snapshot_behavior.py").write_text(
        "import general_ludd.widgets.engine as engine\n\n"
        "def test_snapshot():\n    assert engine.Snapshot() is not None\n"
    )

    result = _status(project, "types.py")

    assert result["status"] == "OK"
    assert result["test_file"] == "tests/unit/test_snapshot_behavior.py"


def test_recognizes_file_path_import_used_by_isolated_module_tests(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_widget_behavior.py").write_text(
        "import importlib.util\n"
        "import os\n\n"
        "_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))\n"
        "_ENGINE = os.path.join(_ROOT, 'src', 'general_ludd', 'widgets', 'engine.py')\n"
        "_SPEC = importlib.util.spec_from_file_location('engine_under_test', _ENGINE)\n"
        "assert _SPEC is not None and _SPEC.loader is not None\n"
        "engine = importlib.util.module_from_spec(_SPEC)\n"
        "_SPEC.loader.exec_module(engine)\n\n"
        "def test_engine():\n    assert engine.Engine() is not None\n"
    )

    assert _status(project)["status"] == "OK"


def test_module_name_in_text_or_unrelated_reexport_is_not_coverage(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_public_api.py").write_text(
        '"""general_ludd.widgets.engine is intentionally mentioned only in prose."""\n'
        "from general_ludd.widgets import Other\n\n"
        "def test_other():\n    assert Other() is not None\n"
    )

    assert _status(project)["status"] == "UNTESTED"


def test_named_candidate_importing_another_module_remains_no_import(
    project: Path,
) -> None:
    (project / "tests" / "unit" / "test_widgets_engine.py").write_text(
        "from general_ludd.widgets.other import Other\n\ndef test_other():\n    assert Other() is not None\n"
    )

    assert _status(project)["status"] == "NO_IMPORT"


def test_named_candidate_short_circuits_repository_index(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A canonical importing test must not trigger a repository-wide AST scan."""
    candidate = project / "tests" / "unit" / "test_widgets_engine.py"
    candidate.write_text(
        "from general_ludd.widgets.engine import Engine\n\ndef test_engine():\n    assert Engine() is not None\n"
    )
    original = checker._build_test_index
    calls: list[tuple[Path, ...] | None] = []

    def tracked(test_files: tuple[Path, ...] | None = None) -> checker.TestIndex:
        calls.append(test_files)
        if test_files is None:
            raise AssertionError("canonical candidate should avoid the full repository scan")
        return original(test_files)

    monkeypatch.setattr(checker, "_build_test_index", tracked)

    result = _status(project)

    assert result["status"] == "OK"
    assert calls == [(candidate,)]


def test_validated_indirect_mapping_follows_facade_dependency(
    project: Path,
) -> None:
    """A behavior test may cover a component through a facade that imports it."""
    widgets = project / "src" / "general_ludd" / "widgets"
    (widgets / "facade.py").write_text(
        "from general_ludd.widgets.engine import Engine as _Engine\n\ndef build_engine():\n    return _Engine()\n"
    )
    test_file = project / "tests" / "unit" / "test_widget_facade.py"
    test_file.write_text(
        "import general_ludd.widgets.facade as facade\n\n"
        "def test_engine_through_facade():\n"
        "    assert facade.build_engine() is not None\n"
    )
    config = project / "config" / "coverage_gap_test_mappings.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "indirect_test_mappings": {
                    "general_ludd.widgets.engine": {
                        "via": "general_ludd.widgets.facade",
                        "tests": ["tests/unit/test_widget_facade.py"],
                    }
                }
            }
        )
    )

    result = _status(project)

    assert result["status"] == "OK"
    assert result["test_file"] == "tests/unit/test_widget_facade.py"


def test_validated_indirect_mapping_follows_transitive_facade_dependency(
    project: Path,
) -> None:
    """A facade test may own a component reached through a split-module chain."""
    widgets = project / "src" / "general_ludd" / "widgets"
    (widgets / "middle.py").write_text(
        "from general_ludd.widgets.engine import Engine as _Engine\n\ndef build_engine():\n    return _Engine()\n"
    )
    (widgets / "facade.py").write_text("from general_ludd.widgets.middle import build_engine\n")
    test_file = project / "tests" / "unit" / "test_widget_facade.py"
    test_file.write_text(
        "import general_ludd.widgets.facade as facade\n\n"
        "def test_engine_through_facade():\n"
        "    assert facade.build_engine() is not None\n"
    )
    config = project / "config" / "coverage_gap_test_mappings.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "indirect_test_mappings": {
                    "general_ludd.widgets.engine": {
                        "via": "general_ludd.widgets.facade",
                        "tests": ["tests/unit/test_widget_facade.py"],
                    }
                }
            }
        )
    )

    result = _status(project)

    assert result["status"] == "OK"
    assert result["test_file"] == "tests/unit/test_widget_facade.py"


def test_indirect_mapping_rejects_unrelated_facade_test(project: Path) -> None:
    """Configuration cannot turn an unrelated passing test into coverage."""
    widgets = project / "src" / "general_ludd" / "widgets"
    (widgets / "facade.py").write_text(
        "from general_ludd.widgets.other import Other as _Other\n\ndef build_other():\n    return _Other()\n"
    )
    test_file = project / "tests" / "unit" / "test_widget_facade.py"
    test_file.write_text(
        "import general_ludd.widgets.facade as facade\n\n"
        "def test_other_through_facade():\n"
        "    assert facade.build_other() is not None\n"
    )
    config = project / "config" / "coverage_gap_test_mappings.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "indirect_test_mappings": {
                    "general_ludd.widgets.engine": {
                        "via": "general_ludd.widgets.facade",
                        "tests": ["tests/unit/test_widget_facade.py"],
                    }
                }
            }
        )
    )

    with pytest.raises(checker.CoverageMappingError, match="does not import"):
        _status(project)


def test_repository_split_component_mappings_are_exact_and_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin every split component that the development coverage gate reported.

    The full-repository index build is slow, so we verify the config file
    exactly and spot-check a representative sample of live mappings.
    """
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(checker, "PROJECT_ROOT", root)
    monkeypatch.setattr(checker, "SRC_DIR", root / "src" / "general_ludd")
    monkeypatch.setattr(checker, "TESTS_DIR", root / "tests" / "unit")

    mappings = checker._load_test_mappings(root / "config" / "coverage_gap_test_mappings.json")
    observed = {
        module: (entry["via"], entry["tests"][0].relative_to(root).as_posix()) for module, entry in mappings.items()
    }

    assert observed == EXPECTED_SPLIT_MAPPINGS

    # Spot-check a representative sample to prove the mappings are live.
    sample = {
        "general_ludd.cli_commands.parser": ("tests/unit/test_cli_module_split.py"),
        "general_ludd.daemon_components.ports": ("tests/unit/test_daemon_module_split_compat.py"),
        "general_ludd.event_loop.tick_lifecycle": ("tests/unit/test_event_loop_module_split.py"),
    }
    test_index = checker._build_test_index()
    tests_by_module, test_counts = test_index
    source_modules = checker._source_module_paths()
    for module, expected_test in sample.items():
        result = checker._check_module(source_modules[module], test_index)
        assert result["status"] == "OK", module
        covering_tests = {
            path.relative_to(root).as_posix() for path in tests_by_module[module] if test_counts[path] > 0
        }
        assert expected_test in covering_tests, module
        assert result["test_file"] in covering_tests, module


def test_repository_chemistry_installed_import_is_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pin the installed-package import used by chemistry acceptance suites."""
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setattr(checker, "PROJECT_ROOT", root)
    monkeypatch.setattr(checker, "SRC_DIR", root / "src" / "general_ludd")
    monkeypatch.setattr(checker, "TESTS_DIR", root / "tests" / "unit")
    result = checker._check_module(root / "src" / "general_ludd" / "chemistry" / "analytical.py")

    assert result["status"] == "OK"
    assert result["test_file"] == "tests/unit/test_chemistry_analytical.py"


def test_baseline_round_trip_and_malformed_input(project: Path) -> None:
    baseline = project / "config" / "coverage.json"

    assert checker._load_baseline(baseline) == set()
    assert checker._generate_baseline(["z.py", "a.py"], baseline) == 2
    assert checker._load_baseline(baseline) == {"a.py", "z.py"}
    assert json.loads(baseline.read_text()) == {"allowed_gaps": ["a.py", "z.py"]}

    baseline.write_text("{")
    assert checker._load_baseline(baseline) == set()


def test_source_walk_and_path_candidates_are_exact(project: Path) -> None:
    source = project / "src" / "general_ludd" / "widgets"
    (source / "typing.pyi").write_text("class Hint: ...\n")
    cached = source / "__pycache__"
    cached.mkdir()
    (cached / "cached.py").write_text("cached = True\n")

    modules = checker._walk_source_modules()

    assert [path.name for path in modules] == ["engine.py", "other.py"]
    engine = source / "engine.py"
    assert checker._module_path(engine) == "general_ludd.widgets.engine"
    assert checker._candidate_test_paths(engine) == [
        project / "tests" / "unit" / "test_general_ludd_widgets_engine.py",
        project / "tests" / "unit" / "test_widgets_engine.py",
    ]


def test_path_mapping_supports_a_top_level_module(project: Path) -> None:
    standalone = project / "standalone.py"
    standalone.write_text("value = 1\n")

    assert checker._module_path(standalone) == "standalone"
    assert checker._candidate_test_paths(standalone) == [project / "tests" / "unit" / "test_standalone.py"]


def test_python_and_relative_import_parsing_is_fail_closed(
    project: Path,
) -> None:
    valid = project / "valid.py"
    invalid = project / "invalid.py"
    valid.write_text("value = 1\n")
    invalid.write_text("def broken(:\n")

    assert isinstance(checker._parse_python(valid), ast.Module)
    assert checker._parse_python(invalid) is None
    assert checker._parse_python(project / "missing.py") is None

    absolute = ast.ImportFrom(module="x", names=[], level=0)
    relative = ast.ImportFrom(module="types", names=[], level=1)
    parent = ast.ImportFrom(module="common", names=[], level=2)
    invalid_parent = ast.ImportFrom(module=None, names=[], level=5)
    assert checker._absolute_import_from(absolute, "general_ludd.widgets") == "x"
    assert checker._absolute_import_from(relative, "general_ludd.widgets") == ("general_ludd.widgets.types")
    assert checker._absolute_import_from(parent, "general_ludd.widgets") == ("general_ludd.common")
    assert checker._absolute_import_from(invalid_parent, "general_ludd") is None


def test_static_expression_and_source_path_resolution(project: Path) -> None:
    tree = ast.parse(
        "from pathlib import Path\n"
        "ROOT = Path('src') / 'general_ludd'\n"
        "ENGINE: object = ROOT / 'widgets' / 'engine.py'\n"
    )
    assignments = checker._assigned_expressions(tree)
    source_modules = checker._source_module_paths()

    dotted_statement = ast.parse("pkg.loader.read").body[0]
    assert isinstance(dotted_statement, ast.Expr)
    assert checker._dotted_name(dotted_statement.value) == "pkg.loader.read"
    assert checker._dotted_name(ast.Constant(value=1)) is None
    call_statement = ast.parse("pkg.loader.read()").body[0]
    assert isinstance(call_statement, ast.Expr)
    call = call_statement.value
    assert isinstance(call, ast.Call)
    assert checker._call_name(call) == "pkg.loader.read"
    assert checker._static_path_parts(assignments["ENGINE"], assignments) == [
        "src",
        "general_ludd",
        "widgets",
        "engine.py",
    ]
    assert checker._static_path_parts(ast.Name(id="missing"), assignments) == []
    assert (
        checker._module_from_source_path(["repo", "src", "general_ludd", "widgets", "engine.py"], source_modules)
        == "general_ludd.widgets.engine"
    )
    assert checker._module_from_source_path(["src", "other", "engine.py"], source_modules) is None


def test_counts_sync_and_async_tests_and_rejects_invalid_files(project: Path) -> None:
    tests = project / "tests" / "unit"
    valid = tests / "test_counts.py"
    invalid = tests / "test_invalid.py"
    valid.write_text("def test_sync():\n    pass\n\nasync def test_async():\n    pass\n\ndef helper():\n    pass\n")
    invalid.write_text("def broken(:\n")

    assert checker._count_test_functions(valid) == 2
    assert checker._count_test_functions(invalid) == 0
    assert checker._count_test_functions(tests / "missing.py") == 0


def test_named_candidate_without_tests_is_a_stub(project: Path) -> None:
    candidate = project / "tests" / "unit" / "test_widgets_engine.py"
    candidate.write_text("from general_ludd.widgets.engine import Engine\n")

    result = _status(project)

    assert result["status"] == "STUB"
    assert result["test_file"] == "tests/unit/test_widgets_engine.py"


def test_main_json_threshold_baseline_and_error_paths(
    project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    test_file = project / "tests" / "unit" / "test_widget_behavior.py"
    test_file.write_text(
        "from general_ludd.widgets.engine import Engine\n\ndef test_engine():\n    assert Engine() is not None\n"
    )

    assert checker.main(["check", "--threshold=bad"]) == 2
    assert "invalid threshold" in capsys.readouterr().err

    assert checker.main(["check", "--json", "--threshold=1"]) == 1
    json_report = json.loads(capsys.readouterr().out)
    assert json_report["summary"]["new_gaps"] == 1
    assert json_report["exit_code"] == 1

    assert checker.main(["check", "--threshold=2"]) == 1
    assert "BELOW TEST THRESHOLD" in capsys.readouterr().out

    baseline_arg = "--baseline=config/generated-coverage.json"
    assert checker.main(["check", "--generate-baseline", baseline_arg]) == 0
    assert "Baseline written" in capsys.readouterr().out
    assert checker.main(["check", baseline_arg]) == 0
    assert "PASS: all modules have test coverage" in capsys.readouterr().out
