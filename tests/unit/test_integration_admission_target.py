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

    assert "integration-admission" in makefile.split("help:", 1)[0]
    assert (
        'integration-admission  Fail-fast feature-branch checks before the full gate'
        in makefile
    )

    phases = (
        "worktree-guard",
        "validate-task-ledger",
        "check-task-registration",
        "check-task-integrity",
        "check-generated-artifact-hygiene",
        "lint-markdown",
        "check-make-target-contract",
        "yaml-lint",
        "presentation-browser-test",
        "pre-commit-check",
    )
    positions = [stanza.index(phase) for phase in phases]
    assert positions == sorted(positions)
    assert "PRESENTATION_BROWSER_VALIDATE_ONLY=1" in stanza
    assert "FILE_LINE_LIMIT_POLICY=\"$(FILE_LINE_LIMIT_POLICY)\"" in stanza
    assert "MARKDOWN_FILES=\"$(MARKDOWN_FILES)\"" in stanza
    assert "MARKDOWNLINT_CONFIG=\"$(MARKDOWNLINT_CONFIG)\"" in stanza
    assert "gate-full" not in stanza
    assert "$(MAKE) --no-print-directory gate" not in stanza

    pre_commit = _target_stanza(makefile, "pre-commit-check")
    assert "$(MAKE) --no-print-directory lint" in pre_commit
    assert "lint: check-file-line-limits" in makefile


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
                "FILE_LINE_LIMIT_POLICY",
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
                "FILE_LINE_LIMIT_POLICY=config/file_line_limits.json "
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
            "FILE_LINE_LIMIT_POLICY=config/file_line_limits.json",
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
        "validate-task-ledger",
        "check-task-registration",
        "check-task-integrity",
        "check-generated-artifact-hygiene",
        "lint-markdown",
        "check-make-target-contract",
        "yaml-lint",
        "presentation-browser-test",
        "pre-commit-check",
    ):
        assert f"phase={phase}" in output
    assert "INTEGRATION-ADMISSION: PASSED" not in output


def test_integration_admission_document_records_queue_evidence_and_boundaries() -> None:
    """The feature record retains practitioner evidence and honest scope."""
    content = FEATURE_DOC.read_text(encoding="utf-8")

    for url in (
        "https://github.com/orgs/community/discussions/14801",
        "https://github.com/orgs/community/discussions/43988",
        "https://github.com/orgs/community/discussions/103114",
        "https://github.com/ansible/ansible/issues/74917",
    ):
        assert url in content
    for phrase in (
        "does not replace the full gate",
        "zero-downtime",
        "fail-fast",
        "PRESENTATION_BROWSER_VALIDATE_ONLY=1",
        "247.53 seconds",
        "43.73 seconds",
        "300-second outer bound",
        "600 seconds",
    ):
        assert phrase in content
