"""Contracts for the cheap feature-branch integration admission path."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.makefile_layout import compose_makefile

ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
CONTRACT = ROOT / "config" / "make_target_contract.json"
FEATURE_DOC = ROOT / "docs" / "features" / "INTEGRATION_ADMISSION.md"


def _target_stanza(makefile: str, target: str) -> str:
    return makefile.split(f"\n{target}:", 1)[1].split("\n\n", 1)[0]


def test_integration_admission_is_public_and_fail_fast() -> None:
    """The public target reuses existing checks in cheapest-first order."""
    makefile = compose_makefile(MAKEFILE)
    stanza = _target_stanza(makefile, "integration-admission")
    project_dispatch = _target_stanza(makefile, "_project-dispatch-integration")
    mcp_workspace_jail = _target_stanza(
        makefile, "_mcp-workspace-jail-integration"
    )
    module_graph = _target_stanza(makefile, "_module-graph-classification")

    assert "integration-admission" in makefile.split("help:", 1)[0]
    assert (
        'integration-admission  Fail-fast feature-branch checks before the full gate'
        in makefile
    )

    phases = (
        "worktree-guard",
        "check-gate-failure-promotions",
        "_dead-code-baseline-refresh",
        "check-coverage-gaps",
        "check-resource-ownership",
        "validate-task-ledger",
        "check-task-registration",
        "check-task-integrity",
        "check-generated-artifact-hygiene",
        "lint-markdown",
        "check-make-target-contract",
        "check-duplicate-code",
        "yaml-lint",
        "project-dispatch-integration",
        "mcp-workspace-jail-integration",
        "module-graph-classification",
        "presentation-browser-test",
        "pre-commit-check",
    )
    positions = [stanza.index(phase) for phase in phases]
    assert positions == sorted(positions)
    assert "PRESENTATION_BROWSER_VALIDATE_ONLY=1" in stanza
    assert "FILE_LINE_LIMIT_POLICY=\"$(FILE_LINE_LIMIT_POLICY)\"" in stanza
    assert 'DUPLICATE_CODE_CONFIG="$(DUPLICATE_CODE_CONFIG)"' in stanza
    assert 'DUPLICATE_CODE_ENGINE="$(DUPLICATE_CODE_ENGINE)"' in stanza
    assert 'DUPLICATE_CODE_SOURCE="committed"' in stanza
    assert 'DUPLICATE_CODE_BASE_REF="$(DUPLICATE_CODE_BASE_REF)"' in stanza
    assert 'DUPLICATE_CODE_CURRENT_REF="$(DUPLICATE_CODE_CURRENT_REF)"' in stanza
    assert "RESOURCE_OWNERSHIP_" not in stanza
    assert "MARKDOWN_FILES=\"$(MARKDOWN_FILES)\"" in stanza
    assert "MARKDOWNLINT_CONFIG=\"$(MARKDOWNLINT_CONFIG)\"" in stanza
    assert (
        'GATE_FAILURE_PROMOTION_MANIFEST="$(GATE_FAILURE_PROMOTION_MANIFEST)"'
        in stanza
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
        '"kind":"integration_admission_phase"',
        '"budget_class":"%s"',
    ):
        assert fragment in stanza
    assert (
        "tests/integration/test_multi_project_integration.py::"
        "TestEventLoopProjectScopedIntegration::"
        "test_event_loop_dispatch_includes_project_id" in project_dispatch
    )
    assert (
        "tests/integration/test_worker_isolation.py::"
        "TestWorkerProjectIsolation::test_dispatch_job_contains_only_project_data"
        in project_dispatch
    )
    assert (
        "tests/unit/test_event_loop.py::TestEventLoop::"
        "test_event_loop_serializes_concurrent_ticks" in project_dispatch
    )
    assert (
        "tests/unit/test_mcp_builtins_structural.py::TestBuiltinToolHandler::"
        "test_contain_workspace_escape_returns_none" in mcp_workspace_jail
    )
    assert (
        "tests/unit/test_project_runner_tool.py::TestRunProjectCheckDispatch::"
        "test_workspace_escaping_jail_is_refused" in mcp_workspace_jail
    )
    assert (
        "tests/unit/test_module_graph_deep.py::test_all_subpackages_classified"
        in module_graph
    )
    assert "gate-full" not in stanza
    assert "$(MAKE) --no-print-directory gate" not in stanza

    assert (
        stanza.index('run_phase "worktree-guard"')
        < stanza.index('run_phase "check-gate-failure-promotions"')
        < stanza.index('run_phase "_dead-code-baseline-refresh"')
        < stanza.index('run_phase "validate-task-ledger"')
    )
    assert (
        "$(MAKE) --no-print-directory _dead-code-baseline-refresh;" in stanza
    )
    assert "$(MAKE) --no-print-directory _project-dispatch-integration;" in stanza
    assert "$(MAKE) --no-print-directory _mcp-workspace-jail-integration;" in stanza
    assert "$(MAKE) --no-print-directory _module-graph-classification;" in stanza

    pre_commit = _target_stanza(makefile, "pre-commit-check")
    assert "$(MAKE) --no-print-directory lint" in pre_commit
    assert "lint: check-file-line-limits" in makefile


def test_integration_admission_reuses_canonical_resource_ownership_scope() -> None:
    """Admission, direct checks, and the gate must share checker-owned roots."""
    makefile = compose_makefile(MAKEFILE)
    admission = _target_stanza(makefile, "integration-admission")
    direct = _target_stanza(makefile, "check-resource-ownership")

    assert "RESOURCE_OWNERSHIP_PATHS ?=\n" in makefile
    assert "$(RESOURCE_OWNERSHIP_PATHS)" in direct
    assert (
        'run_phase "check-resource-ownership" "check-resource-ownership" '
        '"fast" "90" "60" $(MAKE) --no-print-directory '
        "check-resource-ownership;"
        in admission
    )
    assert "RESOURCE_OWNERSHIP_" not in admission
    assert "\tcheck-resource-ownership \\\n" in makefile


def test_yaml_lint_isolates_checkout_collection_from_user_state() -> None:
    """Admission must lint the candidate, never a user-installed collection."""
    makefile = compose_makefile(MAKEFILE)
    stanza = _target_stanza(makefile, "yaml-lint")

    assert 'mktemp -d "/tmp/gludd-yaml-lint.' in stanza
    assert 'ANSIBLE_HOME="$$ANSIBLE_STATE_DIR"' in stanza
    assert 'ANSIBLE_LOCAL_TEMP="$$ANSIBLE_STATE_DIR/tmp"' in stanza
    assert 'ANSIBLE_COLLECTIONS_PATH="$(CURDIR)/collections"' in stanza
    assert "trap 'rm -rf -- \"$$ANSIBLE_STATE_DIR\"' EXIT INT TERM" in stanza
    assert "git ls-files --" in stanza
    assert "ansible-lint $$YAML_FILES" in stanza
    assert "ansible-lint playbooks " not in stanza
    assert "scripts/stream_command.py" in stanza
    assert '--heartbeat-secs "10"' in stanza
    assert stanza.index("trap 'rm -rf") < stanza.index("YAML_FILES=")


def test_integration_admission_contract_is_safe_and_explicit() -> None:
    """The behavioral example is non-mutating and names every input."""
    payload = json.loads(CONTRACT.read_text(encoding="utf-8"))
    entries = [
        item for item in payload["targets"] if item["name"] == "integration-admission"
    ]
    assert entries == [
        {
            "name": "integration-admission",
            "make_variables": [
                "INTEGRATION_ADMISSION_VALIDATE_ONLY",
                "GATE_FAILURE_PROMOTION_MANIFEST",
                "FILE_LINE_LIMIT_POLICY",
                "DUPLICATE_CODE_CONFIG",
                "DUPLICATE_CODE_ENGINE",
                "DUPLICATE_CODE_BASE_REF",
                "DUPLICATE_CODE_CURRENT_REF",
                "MARKDOWN_FILES",
                "MARKDOWNLINT_CONFIG",
                "PRESENTATION_BROWSER_ENGINES",
                "PRESENTATION_BROWSER_ROOT",
                "PRESENTATION_BROWSER_OUTPUT",
                "PRESENTATION_BROWSER_TIMEOUT",
            ],
            "behavior": (
                "make integration-admission "
                "INTEGRATION_ADMISSION_VALIDATE_ONLY=1 "
                "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json "
                "FILE_LINE_LIMIT_POLICY=config/file_line_limits.json "
                "DUPLICATE_CODE_CONFIG=config/duplicate_code.json "
                "DUPLICATE_CODE_ENGINE=.opencode/node_modules/.bin/jscpd "
                "DUPLICATE_CODE_BASE_REF=development "
                "DUPLICATE_CODE_CURRENT_REF=HEAD "
                "MARKDOWN_FILES=docs/features/INTEGRATION_ADMISSION.md "
                "MARKDOWNLINT_CONFIG=config/markdownlint-cli2.jsonc "
                "PRESENTATION_BROWSER_ENGINES='chromium webkit' "
                "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers "
                "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-integration-admission-browser "
                "PRESENTATION_BROWSER_TIMEOUT=600"
            ),
        }
    ]


def test_integration_admission_validate_only_prints_complete_plan() -> None:
    """The safe behavioral example proves composition without running checks."""
    result = subprocess.run(
        [
            "make",
            "integration-admission",
            "INTEGRATION_ADMISSION_VALIDATE_ONLY=1",
            "GATE_FAILURE_PROMOTION_MANIFEST=config/gate_failure_promotions.json",
            "FILE_LINE_LIMIT_POLICY=config/file_line_limits.json",
            "DUPLICATE_CODE_CONFIG=config/duplicate_code.json",
            "DUPLICATE_CODE_ENGINE=.opencode/node_modules/.bin/jscpd",
            "DUPLICATE_CODE_BASE_REF=development",
            "DUPLICATE_CODE_CURRENT_REF=HEAD",
            "MARKDOWN_FILES=docs/features/INTEGRATION_ADMISSION.md",
            "MARKDOWNLINT_CONFIG=config/markdownlint-cli2.jsonc",
            "PRESENTATION_BROWSER_ENGINES=chromium webkit",
            "PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers",
            "PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-integration-admission-browser",
            "PRESENTATION_BROWSER_TIMEOUT=600",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "INTEGRATION-ADMISSION: VALIDATE-ONLY" in output
    for phase in (
        "worktree-guard",
        "check-gate-failure-promotions",
        "_dead-code-baseline-refresh",
        "check-coverage-gaps",
        "check-resource-ownership",
        "validate-task-ledger",
        "check-task-registration",
        "check-task-integrity",
        "check-generated-artifact-hygiene",
        "lint-markdown",
        "check-make-target-contract",
        "check-duplicate-code",
        "yaml-lint",
        "project-dispatch-integration",
        "mcp-workspace-jail-integration",
        "module-graph-classification",
        "presentation-browser-test",
        "pre-commit-check",
    ):
        assert f"phase={phase}" in output
    evidence = [
        json.loads(line)
        for line in output.splitlines()
        if line.startswith("{") and '"kind":"integration_admission_phase"' in line
    ]
    assert len(evidence) == 18
    assert [item["phase"] for item in evidence] == [
        "worktree-guard",
        "check-gate-failure-promotions",
        "_dead-code-baseline-refresh",
        "check-coverage-gaps",
        "check-resource-ownership",
        "validate-task-ledger",
        "check-task-registration",
        "check-task-integrity",
        "check-generated-artifact-hygiene",
        "lint-markdown",
        "check-make-target-contract",
        "check-duplicate-code",
        "yaml-lint",
        "project-dispatch-integration",
        "mcp-workspace-jail-integration",
        "module-graph-classification",
        "presentation-browser-test",
        "pre-commit-check",
    ]
    assert {item["budget_class"] for item in evidence} == {
        "fast",
        "standard",
        "slow",
    }
    assert all(item["max_seconds"] > 0 for item in evidence)
    assert all(item["quiet_seconds"] > 0 for item in evidence)
    assert all(
        item["evidence_label"] == f"integration-admission-{item['phase']}"
        for item in evidence
    )
    assert "INTEGRATION-ADMISSION: PASSED" not in output


def test_integration_admission_document_records_queue_evidence_and_boundaries() -> None:
    """The feature record retains practitioner evidence and honest scope."""
    content = FEATURE_DOC.read_text(encoding="utf-8")

    for url in (
        "https://github.com/orgs/community/discussions/14801",
        "https://github.com/orgs/community/discussions/43988",
        "https://github.com/orgs/community/discussions/103114",
        "https://github.com/ansible/ansible/issues/74917",
        "https://stackoverflow.com/questions/12101463/is-there-a-simple-way-to-use-vulture-with-django",
        "https://github.com/orgs/community/discussions/25631",
    ):
        assert url in content
    for phrase in (
        "does not replace the full gate",
        "zero-downtime",
        "fail-fast",
        "exact dead-code baseline parity",
        "PRESENTATION_BROWSER_VALIDATE_ONLY=1",
        "test_event_loop_dispatch_includes_project_id",
        "test_dispatch_job_contains_only_project_data",
        "test_event_loop_serializes_concurrent_ticks",
        "test_contain_workspace_escape_returns_none",
        "test_workspace_escaping_jail_is_refused",
        "test_all_subpackages_classified",
        "machine-readable",
        "max-runtime-timeout",
        "quiet-output-timeout",
        "2,580 seconds",
        "coverage-gap-drift",
        "resource-ownership-drift",
        "247.53 seconds",
        "43.73 seconds",
        "300-second outer bound",
        "600 seconds",
    ):
        assert phrase in content
    assert "https://github.com/modelcontextprotocol/servers/issues/1838" in content
    assert "https://github.com/seddonym/import-linter/issues/93" in content
