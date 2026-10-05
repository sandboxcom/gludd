"""TDD tests for the `test-language-expert` E2E Makefile target.

Per docs/specs/FEATURE_LANGUAGE_EXPERT.md Section 8 (Test Plan):
    E2E: `make test-language-expert` — runs collection schema check + unit tests

This target bundles ALL language collection tests into a single E2E entry point:
  1. Collection schema validation (galaxy.yml + role scaffolding)
  2. All unit tests for the 7 knowledge modules
  3. Integration tests (cross-module workflows)
  4. Coverage gate >=85% per the spec's gate requirement

These tests prove the target exists, references the spec-required components,
and enforces the coverage threshold.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
MAKEFILE = ROOT / "Makefile"


def _recipe(target: str) -> str:
    """Extract the full recipe body for a make target. Assert target exists."""
    content = MAKEFILE.read_text()
    marker = f"\n{target}:"
    assert marker in content, f"Makefile target '{target}' not found"
    start = content.index(marker) + len(marker)
    next_target = content.find("\n\n", start)
    if next_target == -1:
        return content[start:]
    return content[start:next_target]


class TestLanguageExpertE2eTarget:
    """The spec's E2E target exists and is wired to the required components."""

    def test_target_exists(self) -> None:
        assert "\ntest-language-expert:" in MAKEFILE.read_text(), (
            "Makefile missing 'test-language-expert:' target — "
            "required by FEATURE_LANGUAGE_EXPERT.md Section 8"
        )

    def test_target_in_help(self) -> None:
        content = MAKEFILE.read_text()
        assert "test-language-expert" in content, (
            "test-language-expert not referenced anywhere in Makefile"
        )

    def test_target_runs_schema_check(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "test_language_expert_collection.py" in recipe, (
            "test-language-expert must run the collection schema check "
            "(test_language_expert_collection.py) per spec Section 8"
        )

    def test_target_runs_unit_tests(self) -> None:
        recipe = _recipe("test-language-expert")
        unit_files = [
            "test_language_phase_c.py",
            "test_language_phase_d.py",
            "test_language_phase_e.py",
            "test_language_phase_f.py",
            "test_language_font_data.py",
            "test_language_i18n_data.py",
        ]
        missing = [f for f in unit_files if f not in recipe]
        assert not missing, (
            f"test-language-expert recipe missing unit test files: {missing}"
        )

    def test_target_runs_integration_tests(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "test_language_expert_integration.py" in recipe, (
            "test-language-expert must run integration tests per spec Section 8"
        )

    def test_target_runs_collection_tests(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "collections/ansible_collections/general_ludd/language/tests/" in recipe, (
            "test-language-expert must run the collection's own tests/ dir"
        )

    def test_target_enforces_coverage_threshold(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "--cov-fail-under=85" in recipe, (
            "test-language-expert must enforce >=85% coverage per spec Section 8 "
            "('Gate: knowledge modules >=85% coverage per file')"
        )

    def test_target_covers_language_source(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "--cov=src/general_ludd/language" in recipe, (
            "test-language-expert must measure coverage of src/general_ludd/language/"
        )

    def test_target_streams_output(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "-v" in recipe, (
            "test-language-expert must stream verbose output (observability invariant)"
        )

    def test_target_isolates_pytest_temporary_files(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "scripts/adaptive_test.py" in recipe, (
            "test-language-expert must use the adaptive runner's process-unique "
            "pytest basetemp so nested or concurrent pytest cannot delete live fixtures"
        )

    def test_target_caps_adaptive_workers_without_disabling_oom_retry(self) -> None:
        recipe = _recipe("test-language-expert")
        assert "GLUDD_XDIST_WORKERS=2" in recipe
        assert "-n 2" not in recipe
        assert "--maxprocesses=2" not in recipe
