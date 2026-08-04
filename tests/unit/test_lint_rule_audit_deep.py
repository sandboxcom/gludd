"""Deep audit of ruff lint rule configuration in pyproject.toml.

Validates: rule codes, conflicts, per-file-ignores justification,
isort consistency, severity defaults, and structural integrity.
"""

import tomllib
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

RULE_PREFIXES: dict[str, str] = {
    "E": "pycodestyle errors",
    "F": "pyflakes",
    "I": "isort",
    "W": "pycodestyle warnings",
    "UP": "pyupgrade",
    "B": "flake8-bugbear",
    "SIM": "flake8-simplify",
    "RUF": "ruff-specific rules",
}

KNOWN_CONFLICTING_PAIRS: list[tuple[frozenset[str], str]] = [
    (frozenset({"B904", "TRY200"}), "Both enforce raise-from-inside-except; pick one"),
]

DEPRECATED_RULES: dict[str, str] = {
    "RUF200": "removed in ruff 0.3.0",
}


def _load_pyproject() -> dict:
    path = PROJECT_ROOT / "pyproject.toml"
    with open(path, "rb") as f:
        return tomllib.load(f)


class TestRuffLintRuleAudit:
    @pytest.fixture(autouse=True)
    def cfg(self):
        return _load_pyproject()

    @pytest.fixture
    def ruff(self, cfg):
        return cfg.get("tool", {}).get("ruff", {})

    @pytest.fixture
    def lint(self, ruff):
        return ruff.get("lint", {})

    @pytest.fixture
    def selected(self, lint) -> set[str]:
        return set(lint.get("select", []))

    # 1 — all selected rule prefixes are valid ruff lint codes
    def test_all_selected_prefixes_valid(self, selected):
        prefixes = {s for s in selected if s.isalpha()}
        invalid = prefixes - set(RULE_PREFIXES)
        assert not invalid, f"Unknown rule prefixes in select: {invalid}"

    # 2 — no deprecated rules in select
    def test_no_deprecated_rules_in_select(self, selected):
        for sel in selected:
            assert sel not in DEPRECATED_RULES, f"Deprecated rule {sel} in select — {DEPRECATED_RULES.get(sel)}"

    # 3 — no known conflicting rule pairs both selected
    def test_no_known_conflicting_pairs_selected(self, selected):
        for pair, reason in KNOWN_CONFLICTING_PAIRS:
            active = pair & selected
            assert len(active) <= 1, f"Conflicting rules selected: {active} — {reason}"

    # 4 — target-version matches project requires-python
    def test_target_version_matches_project_python(self, ruff, cfg):
        target = ruff.get("target-version", "")
        py_req = cfg.get("project", {}).get("requires-python", "")
        assert target == "py311", f"target-version={target}, expected py311"
        assert "3.11" in py_req, f"requires-python missing 3.11: {py_req}"

    # 5 — line-length within sensible bounds
    def test_line_length_sensible(self, ruff):
        ll = ruff.get("line-length", 0)
        assert 79 <= ll <= 200, f"line-length={ll} out of [79, 200]"

    # 6 — src directories exist on disk
    def test_src_directories_exist(self, ruff):
        for d in ruff.get("src", []):
            assert (PROJECT_ROOT / d).is_dir(), f"src dir {d} does not exist"

    # 7 — per-file-ignores target existing files
    def test_per_file_ignores_target_existing_files(self, lint):
        for filepath in lint.get("per-file-ignores", {}):
            full = PROJECT_ROOT / filepath
            assert full.is_file(), f"per-file-ignores target missing: {filepath}"

    # 8 — RUF001 ignores only on language/transliteration/detection files
    def test_ruf001_ignores_language_related_only(self, lint):
        for filepath, rules in lint.get("per-file-ignores", {}).items():
            if "RUF001" in rules:
                assert any(kw in filepath for kw in ("language", "transliteration", "detection")), (
                    f"RUF001 on non-language file: {filepath}"
                )

    # 9 — E402 ignore only on ansible module_utils (import before path setup)
    def test_e402_ignore_ansible_module_utils_only(self, lint):
        for filepath, rules in lint.get("per-file-ignores", {}).items():
            if "E402" in rules:
                assert "ansible" in filepath or "module_utils" in filepath, f"E402 on non-ansible file: {filepath}"

    # 10 — per-file-ignored rules descend from selected prefixes
    def test_per_file_ignored_rules_covered_by_select(self, lint, selected):
        for filepath, rules in lint.get("per-file-ignores", {}).items():
            for rule in rules:
                prefix = "".join(c for c in rule if c.isalpha())
                assert prefix in selected, f"Rule {rule} on {filepath} not covered by select; prefix {prefix} missing"

    # 11 — isort known-first-party includes general_ludd
    def test_isort_known_first_party_matches_package(self, lint):
        isort_cfg = lint.get("isort", {})
        kfp = isort_cfg.get("known-first-party", [])
        assert "general_ludd" in kfp, f"known-first-party={kfp} missing general_ludd"

    # 12 — isort known-third-party is non-empty
    def test_isort_known_third_party_non_empty(self, lint):
        isort_cfg = lint.get("isort", {})
        ktp = isort_cfg.get("known-third-party", [])
        assert len(ktp) >= 1, "known-third-party is empty"

    # 13 — isort config has both required keys
    def test_isort_config_has_required_keys(self, lint):
        isort_cfg = lint.get("isort", {})
        for key in ("known-first-party", "known-third-party"):
            assert key in isort_cfg, f"isort missing {key}"

    # 14 — select list is non-empty
    def test_select_is_non_empty(self, selected):
        assert len(selected) > 0, "lint.select is empty"

    # 15 — [tool.ruff.lint] section exists
    def test_lint_section_exists(self, lint):
        assert lint, "[tool.ruff.lint] section missing"

    # 16 — every numeric rule has a valid prefix in RULE_PREFIXES
    def test_all_numeric_rules_have_valid_prefixes(self, selected):
        for sel in selected:
            if not sel.isalpha():
                prefix = "".join(c for c in sel if c.isalpha())
                assert prefix in RULE_PREFIXES, f"{sel}: prefix {prefix} unknown"

    # 17 — [tool.ruff] has required keys
    def test_ruff_section_has_required_keys(self, ruff):
        for key in ("target-version", "line-length", "src"):
            assert key in ruff, f"[tool.ruff] missing {key}"

    # 18 — no mutually exclusive select and external rule overlap
    def test_no_external_rules_overlap_select(self, lint, selected):
        external = set(lint.get("external", []))
        overlap = external & selected
        assert not overlap, f"Rules in both select and external: {overlap}"
