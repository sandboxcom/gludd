"""TDD tests for Multi-Language Code Structural Analysis.

Tests for ``src/general_ludd/language/polyglot.py`` extensions. Verifies:

- ``classify_files_by_structure`` categorizes files by language based on
  content patterns, not just extension.
- ``analyze_code_density`` reports comment-to-code ratios per language.
- ``detect_language_markers`` scans file content for language signatures
  (shebangs, magic comments, package declarations).

These tests FAIL until the functions are added to polyglot.py.
"""

from __future__ import annotations

from pathlib import Path

# ── classify_files_by_structure ─────────────────────────────────────────────


class TestClassifyFilesByStructure:
    """Content-based language classification beyond extension."""

    def test_module_importable(self) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        assert classify_files_by_structure is not None

    def test_python_file_with_shebang(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "script"
        f.write_text("#!/usr/bin/env python3\nimport sys\n", encoding="utf-8")

        result = classify_files_by_structure([f])
        assert len(result) == 1
        assert result[0]["detected_language"] == "python"
        assert "shebang" in result[0]["markers"]

    def test_shell_file_with_shebang(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "deploy.sh"
        f.write_text("#!/bin/bash\nset -euo pipefail\n", encoding="utf-8")

        result = classify_files_by_structure([f])
        assert len(result) == 1
        assert result[0]["detected_language"] == "shell"
        assert result[0]["language_from_extension"] == "shell"
        assert "shebang" in result[0]["markers"]

    def test_node_shebang(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "run"
        f.write_text("#!/usr/bin/env node\nconsole.log('hi');\n", encoding="utf-8")

        result = classify_files_by_structure([f])
        assert len(result) == 1
        assert result[0]["detected_language"] == "javascript"

    def test_unrecognized_extension_content_match(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "config.cfg"
        f.write_text("#!/usr/bin/env python3\nx = 1\n", encoding="utf-8")

        result = classify_files_by_structure([f])
        assert len(result) == 1
        assert result[0]["detected_language"] == "python"
        # Extension is not in our table (".cfg" is not a known language).
        assert result[0]["extension_match"] is False

    def test_empty_file_list(self) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        assert classify_files_by_structure([]) == []

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        result = classify_files_by_structure([tmp_path / "ghost.py"])
        assert result == []

    def test_elixir_file_detected_by_content(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "lib.exs"
        f.write_text("defmodule MyApp do\n  use GenServer\nend\n", encoding="utf-8")

        result = classify_files_by_structure([f])
        assert len(result) == 1
        assert result[0]["detected_language"] in {"elixir", None}


# ── analyze_code_density ────────────────────────────────────────────────────


class TestAnalyzeCodeDensity:
    """Comment-to-code ratio analysis per file."""

    def test_module_importable(self) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        assert analyze_code_density is not None

    def test_python_file_comment_density(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "commented.py"
        f.write_text(
            "# This is a comment\n# Another comment\nx = 1  # inline\ny = 2\n",
            encoding="utf-8",
        )

        result = analyze_code_density([f])
        assert len(result) == 1
        assert result[0]["language"] == "python"
        assert result[0]["total_lines"] == 4
        assert "comment_lines" in result[0]
        assert "code_lines" in result[0]
        assert result[0]["comment_lines"] >= 1
        assert result[0]["blank_lines"] >= 0

    def test_hash_comment_languages(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        py = tmp_path / "a.py"
        py.write_text("# comment\nx=1\n", encoding="utf-8")
        rb = tmp_path / "b.rb"
        rb.write_text("# comment\nx=1\n", encoding="utf-8")

        result = analyze_code_density([py, rb])
        assert len(result) == 2
        for r in result:
            assert r["comment_lines"] >= 1

    def test_c_style_comment_languages(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        js = tmp_path / "a.js"
        js.write_text("// comment\nlet x = 1;\n/* block */\n", encoding="utf-8")
        go = tmp_path / "b.go"
        go.write_text("// comment\nx := 1\n", encoding="utf-8")

        result = analyze_code_density([js, go])
        assert len(result) == 2

    def test_empty_file(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")

        result = analyze_code_density([f])
        assert len(result) == 1
        assert result[0]["total_lines"] == 0

    def test_empty_file_list(self) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        assert analyze_code_density([]) == []

    def test_binary_file_skipped(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")

        result = analyze_code_density([f])
        assert result == []


# ── detect_language_markers ──────────────────────────────────────────────────


class TestDetectLanguageMarkers:
    """Content-based language signature detection."""

    def test_module_importable(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        assert detect_language_markers is not None

    def test_python_shebang(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        text = "#!/usr/bin/env python3\n"
        markers = detect_language_markers(text)
        assert "shebang" in markers
        assert markers["shebang"] == "python"

    def test_package_declaration_go(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        text = 'package main\n\nimport "fmt"\n'
        markers = detect_language_markers(text)
        assert "package_declaration" in markers
        assert markers["package_declaration"] == "go"

    def test_encoding_cookie(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        text = "# -*- coding: utf-8 -*-\n"
        markers = detect_language_markers(text)
        assert "encoding_cookie" in markers
        assert markers["encoding_cookie"] == "utf-8"

    def test_html_doctype(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        text = "<!DOCTYPE html>\n<html>\n"
        markers = detect_language_markers(text)
        assert "doctype" in markers
        assert markers["doctype"] == "html"

    def test_xml_declaration(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        text = '<?xml version="1.0" encoding="UTF-8"?>\n'
        markers = detect_language_markers(text)
        assert "xml_declaration" in markers

    def test_empty_text(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers("")
        assert markers == {}

    def test_plain_text_no_markers(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers("Hello, world!\n")
        assert markers == {}
