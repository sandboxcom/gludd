"""Structural contract tests for branch coverage enforcement.

Verifies that the E2E_COVERAGE_AUDIT_CONTRACT.md, pyproject.toml,
audit_coverage.py, and Makefile are mutually consistent regarding
branch coverage thresholds and reporting.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# pyproject.toml contract
# ---------------------------------------------------------------------------


class TestPyprojectCoverageConfig:
    """pyproject.toml has required coverage configuration."""

    def test_coverage_run_section_exists(self):
        content = (ROOT / "pyproject.toml").read_text()
        assert "[tool.coverage.run]" in content

    def test_coverage_report_section_exists(self):
        content = (ROOT / "pyproject.toml").read_text()
        assert "[tool.coverage.report]" in content

    def test_coverage_source_is_general_ludd(self):
        content = (ROOT / "pyproject.toml").read_text()
        assert 'source = ["general_ludd"]' in content

    def test_coverage_report_has_fail_under_85(self):
        content = (ROOT / "pyproject.toml").read_text()
        # Find the [tool.coverage.report] section
        start = content.index("[tool.coverage.report]")
        section = content[start : start + 500]
        assert "fail_under = 85" in section

    def test_coverage_report_show_missing_true(self):
        content = (ROOT / "pyproject.toml").read_text()
        start = content.index("[tool.coverage.report]")
        section = content[start : start + 500]
        assert "show_missing = true" in section

    def test_coverage_run_omits_tests(self):
        content = (ROOT / "pyproject.toml").read_text()
        assert 'omit = ["tests/*"]' in content

    def test_pytest_cov_in_dev_dependencies(self):
        content = (ROOT / "pyproject.toml").read_text()
        assert "pytest-cov" in content, "pytest-cov must be in dev dependencies for --cov-branch support"

    def test_branch_coverage_not_enabled_by_default_no_issue(self):
        content = (ROOT / "pyproject.toml").read_text()
        start = content.index("[tool.coverage.run]")
        section = content[start : content.find("\n[", start + 1) if "\n[" in content[start + 1 :] else len(content)]
        assert "branch = True" not in section, (
            "branch mode is opt-in via --cov-branch flag per E2E_COVERAGE_AUDIT_CONTRACT, "
            "not a default. Adding branch = True here would affect every coverage run."
        )


# ---------------------------------------------------------------------------
# audit_coverage.py contract
# ---------------------------------------------------------------------------


class TestAuditCoverageScriptContract:
    """audit_coverage.py enforces the contract mechanically."""

    AUDIT_SCRIPT = ROOT / "scripts" / "audit_coverage.py"

    def test_script_exists(self):
        assert self.AUDIT_SCRIPT.exists()

    def test_script_uses_cov_branch_flag(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "--cov-branch" in content, (
            "audit_coverage.py must pass --cov-branch to pytest per E2E_COVERAGE_AUDIT_CONTRACT"
        )

    def test_script_uses_cov_fail_under_zero(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "--cov-fail-under=0" in content, (
            "audit_coverage.py must use --cov-fail-under=0 per shard so aggregate thresholds control pass/fail"
        )

    def test_script_uses_cov_context_test(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "--cov-context=test" in content, "audit_coverage.py must use --cov-context=test for coverage contexts"

    def test_script_uses_cov_append(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "--cov-append" in content, "audit_coverage.py must use --cov-append to merge coverage across shards"

    def test_script_has_parse_coverage_json(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "def parse_coverage_json" in content

    def test_script_has_run_pytest_coverage(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "def run_pytest_coverage" in content

    def test_parse_coverage_json_has_per_file_threshold_param(self):
        spec = importlib.util.spec_from_file_location("contract_check", self.AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import inspect

        sig = inspect.signature(module.parse_coverage_json)
        params = list(sig.parameters.keys())
        assert "per_file_threshold" in params

    def test_run_pytest_coverage_returns_int(self):
        spec = importlib.util.spec_from_file_location("contract_check_2", self.AUDIT_SCRIPT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import inspect

        sig = inspect.signature(module.run_pytest_coverage)
        assert sig.return_annotation in (int, "int", "int | None")

    def test_script_produces_e2e_branch_totals(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "e2e_branch_totals" in content
        assert "e2e_branch_coverage" in content

    def test_default_threshold_is_85(self):
        module = _load_audit("contract_threshold")
        assert module.main.__getattribute__ is not None
        # The default threshold value in main() should be 85
        content = self.AUDIT_SCRIPT.read_text()
        # Find threshold default in main()
        main_start = content.index("def main()" + " -> None:")
        main_body = content[main_start:]
        assert "threshold = 85.0" in main_body or "threshold=85" in main_body

    def test_default_per_file_threshold_is_75(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "per_file_threshold = 75.0" in content

    def test_script_has_progress_sidecar(self):
        content = self.AUDIT_SCRIPT.read_text()
        assert "_progress_path" in content
        assert "_publish_progress" in content
        assert "_progress_snapshot" in content


# ---------------------------------------------------------------------------
# Makefile contract
# ---------------------------------------------------------------------------


class TestMakefileCoverageContract:
    """Makefile includes coverage targets with branch support."""

    MAKEFILE = ROOT / "Makefile"

    def test_audit_coverage_target_exists(self):
        content = self.MAKEFILE.read_text()
        assert "audit-coverage:" in content

    def test_gate_audit_target_exists(self):
        content = self.MAKEFILE.read_text()
        assert "gate-audit:" in content

    def test_coverage_json_target_exists(self):
        content = self.MAKEFILE.read_text()
        assert "coverage-json:" in content

    def test_audit_coverage_uses_project_python(self):
        content = self.MAKEFILE.read_text()
        start = content.index("audit-coverage:")
        recipe = content[start : content.find("\n\n", start)]
        assert "$(UV) run python scripts/audit_coverage.py" in recipe

    def test_audit_coverage_supports_threshold_override(self):
        content = self.MAKEFILE.read_text()
        assert "THRESHOLD ?= 85" in content, "Makefile must allow THRESHOLD override for branch coverage audit"

    def test_gate_audit_runs_both_gate_and_audit(self):
        content = self.MAKEFILE.read_text()
        start = content.index("gate-audit:")
        recipe = content[start : content.find("\n\n", start)]
        assert "gate" in recipe
        assert "audit-coverage" in recipe

    def test_audit_coverage_in_phony(self):
        content = self.MAKEFILE.read_text()
        assert "audit-coverage" in content

    def test_audit_coverage_in_help(self):
        content = self.MAKEFILE.read_text()
        assert "Run coverage audit" in content


# ---------------------------------------------------------------------------
# E2E_COVERAGE_AUDIT_CONTRACT.md consistency
# ---------------------------------------------------------------------------


class TestContractDocConsistency:
    """E2E_COVERAGE_AUDIT_CONTRACT.md is consistent with code."""

    CONTRACT = ROOT / "docs" / "E2E_COVERAGE_AUDIT_CONTRACT.md"

    def test_contract_doc_exists(self):
        assert self.CONTRACT.exists()

    def test_contract_mentions_85_percent_aggregate(self):
        content = self.CONTRACT.read_text()
        assert "85%" in content or "85%" in content.replace("**", "")

    def test_contract_mentions_75_percent_per_file(self):
        content = self.CONTRACT.read_text()
        assert "75%" in content

    def test_contract_mentions_e2e_branch_totals(self):
        content = self.CONTRACT.read_text()
        assert "e2e_branch_totals" in content

    def test_contract_mentions_e2e_branch_coverage(self):
        content = self.CONTRACT.read_text()
        assert "e2e_branch_coverage" in content

    def test_contract_mentions_audit_coverage_target(self):
        content = self.CONTRACT.read_text()
        assert "audit-coverage" in content

    def test_contract_mentions_progress_sidecar(self):
        content = self.CONTRACT.read_text()
        assert "progress.json" in content

    def test_contract_mentions_shard_handling(self):
        content = self.CONTRACT.read_text()
        assert "shard" in content.lower()

    def test_threshold_85_in_contract_matches_code(self):
        contract_content = self.CONTRACT.read_text()
        assert "85%" in contract_content

        audit_content = (ROOT / "scripts" / "audit_coverage.py").read_text()
        assert "threshold = 85.0" in audit_content or "0.85" in audit_content.lower()

    def test_threshold_75_in_contract_matches_code(self):
        contract_content = self.CONTRACT.read_text()
        assert "75%" in contract_content

        audit_content = (ROOT / "scripts" / "audit_coverage.py").read_text()
        assert "per_file_threshold = 75.0" in audit_content

    def test_branch_coverage_definition_in_contract(self):
        content = self.CONTRACT.read_text()
        assert "branch" in content.lower()
        assert "conditional" in content.lower() or "if" in content.lower()


def _load_audit(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "audit_coverage.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
