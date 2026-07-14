"""Structural tests for integrity/fim_excludes.py — canonical FIM exclude patterns."""

from __future__ import annotations

import re

from general_ludd.integrity.fim_excludes import FIM_EXCLUDE_PATTERNS


class TestFimExcludePatterns:
    def test_is_tuple(self) -> None:
        assert isinstance(FIM_EXCLUDE_PATTERNS, tuple)

    def test_contains_four_patterns(self) -> None:
        assert len(FIM_EXCLUDE_PATTERNS) == 4

    def test_all_elements_are_strings(self) -> None:
        for pat in FIM_EXCLUDE_PATTERNS:
            assert isinstance(pat, str)

    def test_all_patterns_are_compilable(self) -> None:
        for pat in FIM_EXCLUDE_PATTERNS:
            compiled = re.compile(pat)
            assert compiled is not None

    def test_pyc_pattern_matches_pyc_file(self) -> None:
        assert re.search(r"\.pyc$", "src/module.pyc") is not None

    def test_pyc_pattern_does_not_match_py_file(self) -> None:
        assert re.search(r"\.pyc$", "src/module.py") is None

    def test_pycache_pattern_matches_path_with_pycache(self) -> None:
        assert re.search(r"__pycache__", "src/__pycache__/module.pyc") is not None

    def test_pycache_pattern_does_not_match_normal_path(self) -> None:
        assert re.search(r"__pycache__", "src/module.py") is None

    def test_git_pattern_matches_git_directory(self) -> None:
        assert re.search(r"\.git/", "project/.git/config") is not None

    def test_git_pattern_does_not_match_regular_file(self) -> None:
        assert re.search(r"\.git/", "project/main.py") is None

    def test_db_pattern_matches_db_file(self) -> None:
        assert re.search(r"\.db$", "data/mydatabase.db") is not None

    def test_db_pattern_does_not_match_other_files(self) -> None:
        assert re.search(r"\.db$", "data/mydatabase.sql") is None


class TestFimExcludesIntegration:
    def test_all_compile_and_match(self) -> None:
        patterns = [re.compile(p) for p in FIM_EXCLUDE_PATTERNS]
        test_cases = [
            ("src/cache/module.cpython-311.pyc", True),
            ("src/cache/__pycache__/module.pyc", True),
            ("repo/.git/HEAD", True),
            ("db/state.db", True),
            ("src/module.py", False),
            ("config/settings.yml", False),
            ("scripts/runner.sh", False),
        ]
        for path, should_match in test_cases:
            matched = any(p.search(path) for p in patterns)
            assert matched == should_match, (
                f"Path {path!r}: expected match={should_match}, got match={matched}"
            )

    def test_exclude_patterns_are_immutable_static(self) -> None:
        assert FIM_EXCLUDE_PATTERNS[0] == r"\.pyc$"
        assert FIM_EXCLUDE_PATTERNS[1] == r"__pycache__"
        assert FIM_EXCLUDE_PATTERNS[2] == r"\.git/"
        assert FIM_EXCLUDE_PATTERNS[3] == r"\.db$"
