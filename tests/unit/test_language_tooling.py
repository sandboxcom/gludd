"""TDD tests for Language Expert tooling integration.

Tests for ``src/general_ludd/language/tooling.py``. Verifies:

- LANGUAGE_TOOLS dict maps every supported language to its lint/test/build tools.
- ``get_language_tools`` returns complete tool records per language.
- ``all_supported_languages`` returns a non-empty frozenset.
- ``detect_available_tools`` checks PATH for actual tool presence.
- ``get_cross_language_compat`` reports toolchain compatibility groups.
- Edge cases: unknown language returns None, empty input returns empty.

These tests FAIL until the module is implemented.
"""

from __future__ import annotations

import shutil

import pytest

TOOL_CATEGORIES = {"lint", "test", "build", "format", "package_manager"}


class TestLanguageToolsDict:
    """The static LANGUAGE_TOOLS mapping is complete and well-formed."""

    def test_module_importable(self) -> None:
        from general_ludd.language import tooling

        assert tooling is not None

    def test_language_tools_dict_is_nonempty(self) -> None:
        from general_ludd.language.tooling import LANGUAGE_TOOLS

        assert isinstance(LANGUAGE_TOOLS, dict)
        assert len(LANGUAGE_TOOLS) > 0

    def test_core_languages_all_present(self) -> None:
        from general_ludd.language.tooling import LANGUAGE_TOOLS

        expected = {
            "python",
            "javascript",
            "typescript",
            "go",
            "rust",
            "java",
            "ruby",
            "php",
            "c",
            "cpp",
            "csharp",
        }
        missing = expected - set(LANGUAGE_TOOLS)
        assert not missing, f"Missing core languages: {missing}"

    def test_every_language_has_required_tool_categories(self) -> None:
        from general_ludd.language.tooling import LANGUAGE_TOOLS

        required = {"lint", "test", "build", "format", "package_manager"}
        for lang, tools in LANGUAGE_TOOLS.items():
            for cat in required:
                assert cat in tools, f"{lang} missing category {cat}"

    def test_language_tools_values_are_strings_or_none(self) -> None:
        from general_ludd.language.tooling import LANGUAGE_TOOLS

        for lang, tools in LANGUAGE_TOOLS.items():
            for cat, tool in tools.items():
                assert tool is None or isinstance(tool, str), f"{lang}.{cat} = {tool!r} is not str|None"


class TestGetLanguageTools:
    """``get_language_tools`` returns the tool record for a given language."""

    def test_python_tools(self) -> None:
        from general_ludd.language.tooling import get_language_tools

        tools = get_language_tools("python")
        assert tools is not None
        assert tools["lint"] == "ruff"
        assert tools["test"] == "pytest"
        assert tools["format"] == "ruff"
        assert tools["package_manager"] == "uv"

    def test_javascript_tools(self) -> None:
        from general_ludd.language.tooling import get_language_tools

        tools = get_language_tools("javascript")
        assert tools is not None
        assert tools["lint"] == "eslint"
        assert tools["test"] == "jest"
        assert tools["package_manager"] == "npm"

    def test_go_tools(self) -> None:
        from general_ludd.language.tooling import get_language_tools

        tools = get_language_tools("go")
        assert tools is not None
        assert tools["lint"] == "golangci-lint"
        assert tools["test"] == "go test"
        assert tools["build"] == "go build"
        assert tools["format"] == "gofmt"

    def test_rust_tools(self) -> None:
        from general_ludd.language.tooling import get_language_tools

        tools = get_language_tools("rust")
        assert tools is not None
        assert tools["lint"] == "clippy"
        assert tools["test"] == "cargo test"
        assert tools["build"] == "cargo build"
        assert tools["format"] == "rustfmt"
        assert tools["package_manager"] == "cargo"

    def test_unknown_language_returns_none(self) -> None:
        from general_ludd.language.tooling import get_language_tools

        assert get_language_tools("brainfuck") is None
        assert get_language_tools("") is None

    def test_case_insensitive_lookup(self) -> None:
        from general_ludd.language.tooling import get_language_tools

        assert get_language_tools("Python") is not None
        assert get_language_tools("PYTHON") is not None
        assert get_language_tools("TypeScript") is not None


class TestAllSupportedLanguages:
    """``all_supported_languages`` returns the full set of known languages."""

    def test_returns_frozenset(self) -> None:
        from general_ludd.language.tooling import all_supported_languages

        result = all_supported_languages()
        assert isinstance(result, frozenset)
        assert len(result) > 5

    def test_is_consistent_with_language_tools(self) -> None:
        from general_ludd.language.tooling import (
            LANGUAGE_TOOLS,
            all_supported_languages,
        )

        langs = all_supported_languages()
        assert langs == frozenset(LANGUAGE_TOOLS.keys())


class TestDetectAvailableTools:
    """``detect_available_tools`` probes PATH for tool presence."""

    def test_returns_dict_for_all_languages(self) -> None:
        from general_ludd.language.tooling import detect_available_tools

        result = detect_available_tools()
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_tool_not_on_path_returns_none(self) -> None:
        from general_ludd.language.tooling import detect_available_tools

        # A language name that can never resolve to a tool on any PATH.
        result = detect_available_tools(["definitely-not-a-real-lang-xyz"])
        assert isinstance(result, dict)
        for _cat, val in result["definitely-not-a-real-lang-xyz"].items():
            assert val is None, f"absent language must resolve to None, got {val!r}"

    def test_unknown_language_preserves_complete_fail_closed_schema(self) -> None:
        from general_ludd.language.tooling import detect_available_tools

        requested = ["unknown-one", "PYTHON", "unknown-two"]
        result = detect_available_tools(requested)

        assert list(result) == requested
        assert set(result["unknown-one"]) == TOOL_CATEGORIES
        assert set(result["unknown-two"]) == TOOL_CATEGORIES
        assert all(value is None for value in result["unknown-one"].values())
        assert all(value is None for value in result["unknown-two"].values())

    def test_path_probe_error_fails_closed_per_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from general_ludd.language.tooling import detect_available_tools

        def unavailable(_command: str) -> None:
            raise OSError("PATH temporarily unavailable")

        monkeypatch.setattr("general_ludd.language.tooling.shutil.which", unavailable)

        result = detect_available_tools(["python"])
        assert set(result["python"]) == TOOL_CATEGORIES
        assert all(value is None for value in result["python"].values())

    def test_python_present_when_pytest_on_path(self) -> None:
        from general_ludd.language.tooling import detect_available_tools

        pytest_path = shutil.which("pytest")
        result = detect_available_tools(["python"])
        assert "python" in result
        if pytest_path is not None:
            assert result["python"]["test"] == "pytest"
        # When pytest is on PATH, format/lint should resolve too (ruff).
        ruff_path = shutil.which("ruff")
        if ruff_path is not None:
            assert result["python"]["lint"] == "ruff"

    def test_empty_languages_returns_empty_dict(self) -> None:
        from general_ludd.language.tooling import detect_available_tools

        assert detect_available_tools([]) == {}

    def test_each_language_entry_has_all_categories(self) -> None:
        from general_ludd.language.tooling import detect_available_tools

        result = detect_available_tools(["python", "go"])
        required = {"lint", "test", "build", "format", "package_manager"}
        for lang in result:
            for cat in required:
                assert cat in result[lang], f"{lang} missing {cat}"


class TestCrossLanguageCompat:
    """``get_cross_language_compat`` reports toolchain compatibility groups."""

    def test_returns_list_of_compatible_languages(self) -> None:
        from general_ludd.language.tooling import get_cross_language_compat

        compat = get_cross_language_compat("python")
        assert isinstance(compat, list)
        # Python shares linter/formatter with Jupyter, etc.
        assert "python" not in compat  # should not include itself

    def test_unknown_language_returns_empty(self) -> None:
        from general_ludd.language.tooling import get_cross_language_compat

        assert get_cross_language_compat("nope") == []

    def test_rust_and_c_share_llvm_tools(self) -> None:
        from general_ludd.language.tooling import get_cross_language_compat

        c_compat = get_cross_language_compat("c")
        assert isinstance(c_compat, list)
        # C and C++ share compilers and linters
        assert "cpp" in c_compat
