"""Contracts for deterministic full-gate failure promotion into admission."""

from __future__ import annotations

import importlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path
from textwrap import dedent
from types import ModuleType

import pytest
from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "config" / "gate_failure_promotions.json"
CONTRACT = ROOT / "config" / "make_target_contract.json"
FEATURE_DOC = ROOT / "docs" / "features" / "INTEGRATION_ADMISSION.md"

EXPECTED_RUNTIME_PHASES = [
    ("worktree-guard", "worktree-guard", "fast", 90, 60),
    (
        "check-gate-failure-promotions",
        "check-gate-failure-promotions",
        "fast",
        90,
        60,
    ),
    (
        "ansible-role-variable-prefix",
        "_ansible-role-variable-prefix",
        "fast",
        90,
        60,
    ),
    (
        "cloud-iam-generation-parity",
        "_cloud-iam-generation-parity",
        "fast",
        90,
        60,
    ),
    (
        "_dead-code-baseline-refresh",
        "_dead-code-baseline-refresh",
        "fast",
        90,
        60,
    ),
    ("check-coverage-gaps", "check-coverage-gaps", "fast", 90, 60),
    (
        "check-resource-ownership",
        "check-resource-ownership",
        "fast",
        90,
        60,
    ),
    ("validate-task-ledger", "validate-task-ledger", "fast", 90, 60),
    ("check-task-registration", "check-task-registration", "fast", 90, 60),
    ("check-task-integrity", "check-task-integrity", "fast", 90, 60),
    (
        "check-generated-artifact-hygiene",
        "check-generated-artifact-hygiene",
        "fast",
        90,
        60,
    ),
    ("lint-markdown", "lint-markdown", "fast", 90, 60),
    (
        "check-make-target-contract",
        "check-make-target-contract",
        "fast",
        90,
        60,
    ),
    ("check-duplicate-code", "check-duplicate-code", "standard", 180, 120),
    ("yaml-lint", "yaml-lint", "standard", 180, 120),
    (
        "project-dispatch-integration",
        "_project-dispatch-integration",
        "standard",
        180,
        120,
    ),
    (
        "mcp-workspace-jail-integration",
        "_mcp-workspace-jail-integration",
        "standard",
        180,
        120,
    ),
    (
        "module-graph-classification",
        "_module-graph-classification",
        "fast",
        90,
        60,
    ),
    (
        "presentation-browser-test",
        "presentation-browser-test",
        "standard",
        180,
        120,
    ),
    ("pre-commit-check", "pre-commit-check", "slow", 600, 300),
]


def _checker() -> ModuleType:
    return importlib.import_module("scripts.check_gate_failure_promotions")


def _target_stanza(makefile: str, target: str) -> str:
    return makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]


def _synthetic_repository(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_promoted.py").write_text(
        dedent(
            """
            class TestPromoted:
                def test_claim_fence(self):
                    pass
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "Makefile").write_text(
        dedent(
            """
            GATE_PREFLIGHT_TARGETS := _dead-code-baseline-refresh

            integration-admission:
            \t@set -eu; printf '{"kind":"integration_admission_phase","evidence_label":"%s%s"}'
            \t@$(UV) run python scripts/stream_command.py \
            \t\t--root ".gate-logs/observed" \
            \t\t--label "integration-admission-$$phase" --run-id "$$run_id" \
            \t\t--heartbeat-secs "10" --quiet-secs "$$quiet_seconds" \
            \t\t--max-secs "$$max_seconds" --retain-runs "20"
            \t@run_phase "check-gate-failure-promotions" \
            \t\t"check-gate-failure-promotions" "fast" "90" "60" \
            \t\t$(MAKE) --no-print-directory check-gate-failure-promotions
            \t@run_phase "_dead-code-baseline-refresh" \
            \t\t"_dead-code-baseline-refresh" "fast" "90" "60" \
            \t\t$(MAKE) --no-print-directory _dead-code-baseline-refresh
            \t@run_phase "project-dispatch-integration" \
            \t\t"_project-dispatch-integration" "slow" "600" "300" \
            \t\t$(MAKE) --no-print-directory _project-dispatch-integration

            check-gate-failure-promotions:
            \t@true

            _dead-code-baseline-refresh:
            \t@true

            _project-dispatch-integration:
            \t@echo tests/test_promoted.py::TestPromoted::test_claim_fence

            gate:
            \t@echo "=== GATE PHASE: preflights ==="
            \t@echo "=== GATE PHASE: test ==="
            \t@bash scripts/run_gate.sh
            """
        ).lstrip(),
        encoding="utf-8",
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "admission_target": "integration-admission",
        "full_gate_target": "gate",
        "full_gate_required": True,
        "runtime_contract": {
            "observer": "scripts/stream_command.py",
            "evidence_root": ".gate-logs/observed",
            "label_prefix": "integration-admission-",
            "heartbeat_seconds": 10,
            "max_retained_runs": 20,
            "phases": [
                {
                    "name": "check-gate-failure-promotions",
                    "target": "check-gate-failure-promotions",
                    "budget_class": "fast",
                    "max_seconds": 90,
                    "quiet_seconds": 60,
                },
                {
                    "name": "_dead-code-baseline-refresh",
                    "target": "_dead-code-baseline-refresh",
                    "budget_class": "fast",
                    "max_seconds": 90,
                    "quiet_seconds": 60,
                },
                {
                    "name": "project-dispatch-integration",
                    "target": "_project-dispatch-integration",
                    "budget_class": "slow",
                    "max_seconds": 600,
                    "quiet_seconds": 300,
                },
            ],
        },
        "families": [
            {
                "id": "dead-code-baseline-drift",
                "owner_target": "_dead-code-baseline-refresh",
                "node_kind": "make_target",
                "node": "_dead-code-baseline-refresh",
                "full_gate_phase": "preflights",
            },
            {
                "id": "claim-fence",
                "owner_target": "_project-dispatch-integration",
                "node_kind": "pytest_node",
                "node": "tests/test_promoted.py::TestPromoted::test_claim_fence",
                "full_gate_phase": "test",
            },
        ],
    }
    return tmp_path, payload


def test_validator_accepts_owned_make_and_pytest_nodes(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)

    assert _checker().validate_manifest(payload, repository_root=repository) == []


def test_validator_rejects_duplicate_missing_and_unwired_nodes(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)
    families = payload["families"]
    assert isinstance(families, list)
    broken = families[1]
    assert isinstance(broken, dict)
    broken["id"] = "dead-code-baseline-drift"
    broken["owner_target"] = "_missing-owner"
    broken["node"] = "tests/test_promoted.py::TestPromoted::test_missing"

    errors = _checker().validate_manifest(payload, repository_root=repository)

    assert any("duplicate family id" in error for error in errors)
    assert any("owner target is not defined: _missing-owner" in error for error in errors)
    assert any("pytest node does not exist" in error for error in errors)
    assert any("owner target is not wired into integration-admission" in error for error in errors)


def test_validator_rejects_schema_and_makefile_load_failures(tmp_path: Path) -> None:
    checker = _checker()

    schema_errors = checker.validate_manifest({}, repository_root=tmp_path)
    assert any("schema_version" in error for error in schema_errors)
    assert checker._pytest_node_exists("src/not-a-test.py::test_nope", tmp_path) is False

    valid_root = tmp_path / "valid"
    valid_root.mkdir()
    _, payload = _synthetic_repository(valid_root)
    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    makefile_errors = checker.validate_manifest(payload, repository_root=missing_root)
    assert len(makefile_errors) == 1
    assert "Makefile composition failed" in makefile_errors[0]
    assert str(missing_root / "Makefile") in makefile_errors[0]


def test_validator_rejects_gate_and_owner_topology_drift(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)
    (repository / "Makefile").write_text(
        dedent(
            """
            integration-admission:
            \t@$(MAKE) --no-print-directory gate

            _dead-code-baseline-refresh:
            \t@true

            _project-dispatch-integration:
            \t@echo unrelated

            gate:
            \t@echo no-declared-phases
            """
        ).lstrip(),
        encoding="utf-8",
    )
    families = payload["families"]
    assert isinstance(families, list)
    make_family = families[0]
    pytest_family = families[1]
    assert isinstance(make_family, dict)
    assert isinstance(pytest_family, dict)
    make_family["node"] = "other-target"
    pytest_family["node"] = "tests/missing.py::test_missing"
    duplicate = deepcopy(pytest_family)
    duplicate["id"] = "second-pytest-family"
    families.append(duplicate)

    errors = _checker().validate_manifest(payload, repository_root=repository)

    for phrase in (
        "does not invoke the promotion checker",
        "must not invoke the full gate",
        "owner target is not wired into integration-admission",
        "full gate phase is not defined",
        "make node must equal its owner target",
        "make owner is absent from gate preflights",
        "pytest node is absent from its owner target",
        "pytest node does not exist",
        "full gate test runner is not wired",
        "duplicate promoted node",
    ):
        assert any(phrase in error for error in errors)

    missing_targets = deepcopy(payload)
    missing_targets["admission_target"] = "absent"
    missing_targets["full_gate_target"] = "absent"
    missing_errors = _checker().validate_manifest(
        missing_targets, repository_root=repository
    )
    assert any("admission target is not defined" in error for error in missing_errors)
    assert any("full gate target is not defined" in error for error in missing_errors)
    assert any("admission target must remain separate" in error for error in missing_errors)


def test_validator_rejects_runtime_budget_and_observer_drift(tmp_path: Path) -> None:
    repository, payload = _synthetic_repository(tmp_path)
    runtime = payload["runtime_contract"]
    assert isinstance(runtime, dict)
    phases = runtime["phases"]
    assert isinstance(phases, list)
    phase = phases[0]
    assert isinstance(phase, dict)
    phase["max_seconds"] = 91
    phase["quiet_seconds"] = 92
    runtime["label_prefix"] = "unattributed-"

    errors = _checker().validate_manifest(payload, repository_root=repository)

    assert any("quiet_seconds must not exceed max_seconds" in error for error in errors)
    assert any("runtime phase is not wired with its exact budget" in error for error in errors)
    assert any("observer contract is absent from integration admission" in error for error in errors)


def test_cli_rejects_missing_invalid_and_oversized_manifests(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    checker = _checker()
    missing = tmp_path / "missing.json"
    assert (
        checker.main(
            ["--manifest", str(missing), "--repository-root", str(ROOT)]
        )
        == 1
    )
    assert "cannot load manifest" in capsys.readouterr().err

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}", encoding="utf-8")
    assert (
        checker.main(
            ["--manifest", str(invalid), "--repository-root", str(ROOT)]
        )
        == 1
    )
    assert "schema_version" in capsys.readouterr().err

    oversized = tmp_path / "oversized.json"
    oversized.write_text(" " * 256_001, encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds"):
        checker._load_manifest(oversized)


def test_repository_manifest_seeds_each_promoted_failure_family() -> None:
    payload = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert payload["full_gate_required"] is True
    assert [family["id"] for family in payload["families"]] == [
        "dead-code-baseline-drift",
        "coverage-gap-drift",
        "resource-ownership-drift",
        "claim-fence",
        "project-isolation",
        "concurrent-tick",
        "mcp-workspace-containment",
        "mcp-workspace-dispatch-jail",
        "module-graph-classification-drift",
        "ansible-role-variable-prefix-drift",
        "cloud-iam-resource-pruning-trigger",
        "cloud-iam-generation-parity",
    ]
    families = {family["id"]: family for family in payload["families"]}
    assert families["mcp-workspace-containment"]["node"] == (
        "tests/unit/test_mcp_builtins_structural.py::TestBuiltinToolHandler::"
        "test_contain_workspace_escape_returns_none"
    )
    assert families["mcp-workspace-dispatch-jail"]["node"] == (
        "tests/unit/test_project_runner_tool.py::TestRunProjectCheckDispatch::"
        "test_workspace_escaping_jail_is_refused"
    )
    assert families["module-graph-classification-drift"]["node"] == (
        "tests/unit/test_module_graph_deep.py::test_all_subpackages_classified"
    )
    assert families["ansible-role-variable-prefix-drift"]["node"] == (
        "tests/unit/test_ansible_lint_deep.py::test_role_variables_use_namespaced_prefix"
    )
    assert families["cloud-iam-resource-pruning-trigger"]["node"] == (
        "tests/unit/test_cloud_role_generator.py::"
        "TestGenerateRoleFromTemplateWithResourceTypes::"
        "test_aws_prune_produces_warning_when_actions_removed"
    )
    assert families["cloud-iam-generation-parity"]["node"] == (
        "tests/unit/test_cloud_iam_expert.py::TestGenerateCloudRole::"
        "test_generate_aws_terraform_deploy"
    )
    assert families["coverage-gap-drift"]["node"] == "check-coverage-gaps"
    assert families["resource-ownership-drift"]["node"] == (
        "check-resource-ownership"
    )
    runtime = payload["runtime_contract"]
    assert runtime["observer"] == "scripts/stream_command.py"
    assert runtime["max_retained_runs"] == 20
    assert [
        (
            phase["name"],
            phase["target"],
            phase["budget_class"],
            phase["max_seconds"],
            phase["quiet_seconds"],
        )
        for phase in runtime["phases"]
    ] == EXPECTED_RUNTIME_PHASES
    assert _checker().validate_manifest(payload, repository_root=ROOT) == []


def test_make_wiring_and_target_contract_are_explicit() -> None:
    makefile = compose_makefile(ROOT / "Makefile")
    admission = _target_stanza(makefile, "integration-admission")
    owner = _target_stanza(makefile, "_project-dispatch-integration")
    mcp_owner = _target_stanza(makefile, "_mcp-workspace-jail-integration")
    module_graph_owner = _target_stanza(makefile, "_module-graph-classification")
    variable_prefix_owner = _target_stanza(makefile, "_ansible-role-variable-prefix")
    cloud_iam_owner = _target_stanza(makefile, "_cloud-iam-generation-parity")

    assert "check-gate-failure-promotions" in makefile.split("help:", 1)[0]
    assert (
        "check-gate-failure-promotions  Validate owned fast-admission failure nodes"
        in makefile
    )
    assert (
        admission.index('run_phase "worktree-guard"')
        < admission.index('run_phase "check-gate-failure-promotions"')
        < admission.index('run_phase "ansible-role-variable-prefix"')
        < admission.index('run_phase "cloud-iam-generation-parity"')
        < admission.index('run_phase "_dead-code-baseline-refresh"')
        < admission.index('run_phase "check-coverage-gaps"')
        < admission.index('run_phase "check-resource-ownership"')
    )
    assert "$(MAKE) --no-print-directory _project-dispatch-integration;" in admission
    assert "$(MAKE) --no-print-directory _mcp-workspace-jail-integration;" in admission
    assert "$(MAKE) --no-print-directory _module-graph-classification;" in admission
    assert "$(MAKE) --no-print-directory _ansible-role-variable-prefix;" in admission
    assert "$(MAKE) --no-print-directory _cloud-iam-generation-parity;" in admission
    assert (
        'GATE_FAILURE_PROMOTION_MANIFEST="$(GATE_FAILURE_PROMOTION_MANIFEST)"'
        in admission
    )
    for fragment in (
        "scripts/stream_command.py",
        '--root ".gate-logs/observed"',
        '--label "integration-admission-$$phase"',
        '--run-id "$$run_id"',
        '--heartbeat-secs "10"',
        '--quiet-secs "$$quiet_seconds"',
        '--max-secs "$$max_seconds"',
        '--retain-runs "20"',
    ):
        assert fragment in admission
    for node in (
        "test_event_loop_dispatch_includes_project_id",
        "test_dispatch_job_contains_only_project_data",
        "test_event_loop_serializes_concurrent_ticks",
    ):
        assert node in owner
    for node in (
        "test_contain_workspace_escape_returns_none",
        "test_workspace_escaping_jail_is_refused",
    ):
        assert node in mcp_owner
    assert "test_all_subpackages_classified" in module_graph_owner
    assert "test_role_variables_use_namespaced_prefix" in variable_prefix_owner
    prune_node = "test_aws_prune_produces_warning_when_actions_removed"
    full_role_node = "test_generate_aws_terraform_deploy"
    assert prune_node in cloud_iam_owner
    assert full_role_node in cloud_iam_owner
    assert cloud_iam_owner.index(prune_node) < cloud_iam_owner.index(full_role_node)
    assert 'PYTEST_ARGS="-W error -q -n 0"' in cloud_iam_owner

    contracts = json.loads(CONTRACT.read_text(encoding="utf-8"))["targets"]
    check_contract = next(
        item for item in contracts if item["name"] == "check-gate-failure-promotions"
    )
    assert check_contract == {
        "name": "check-gate-failure-promotions",
        "make_variables": ["GATE_FAILURE_PROMOTION_MANIFEST"],
        "behavior": (
            "make check-gate-failure-promotions "
            "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json"
        ),
    }
    admission_contract = next(
        item for item in contracts if item["name"] == "integration-admission"
    )
    assert "GATE_FAILURE_PROMOTION_MANIFEST" in admission_contract["make_variables"]
    assert (
        "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json"
        in admission_contract["behavior"]
    )


def test_target_and_feature_document_preserve_full_gate_boundary() -> None:
    result = subprocess.run(
        [
            "make",
            "check-gate-failure-promotions",
            "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    content = FEATURE_DOC.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "gate-failure-promotions: PASS families=12" in result.stdout
    assert "runtime_phases=20" in result.stdout
    assert "runtime_ceiling_seconds=2760" in result.stdout
    for phrase in (
        "dead-code-baseline-drift",
        "coverage-gap-drift",
        "resource-ownership-drift",
        "claim-fence",
        "project-isolation",
        "concurrent-tick",
        "mcp-workspace-containment",
        "mcp-workspace-dispatch-jail",
        "module-graph-classification-drift",
        "ansible-role-variable-prefix-drift",
        "cloud-iam-resource-pruning-trigger",
        "cloud-iam-generation-parity",
        "exact full gate remains mandatory",
        "https://github.com/orgs/community/discussions/41726",
        "https://github.com/modelcontextprotocol/servers/issues/1838",
        "https://github.com/seddonym/import-linter/issues/93",
    ):
        assert phrase in content
