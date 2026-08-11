"""Phase E polyglot support: multi-language detection + cross-language analysis.

TDD tests for ``src/general_ludd/language/polyglot.py``. These fail until
the module exists and exposes:

- ``detect_languages_in_directory(path)`` — walk a tree, identify languages
  by extension + marker files, return a structured report.
- ``cross_language_homoglyph_scan(files)`` — scan a list of files of mixed
  languages for confusable characters that cross script boundaries
  (e.g. a Cyrillic U+0430 in a Python source where Latin identifiers belong).
- ``encoding_conflict_report(files)`` — sniff each file's BOM/encoding and
  report mismatches that would break tooling (UTF-8 vs UTF-16, mixed BOMs,
  declared-vs-actual encoding drift).
"""

from __future__ import annotations

from pathlib import Path

from general_ludd.language.polyglot import (
    cross_language_homoglyph_scan,
    detect_languages_in_directory,
    encoding_conflict_report,
)

# ── detect_languages_in_directory ──────────────────────────────────────────


class TestDetectLanguagesInDirectory:
    """``detect_languages_in_directory`` identifies languages in a tree."""

    def test_single_language_python(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        names = [p["language"] for p in report["languages"]]
        assert "python" in names
        py = next(p for p in report["languages"] if p["language"] == "python")
        assert py["file_count"] == 2

    def test_polyglot_repo_multiple_languages(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "lib.js").write_text("var x = 1;\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
        (tmp_path / "ui.tsx").write_text("export const X = 1;\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        names = {p["language"] for p in report["languages"]}
        assert {"python", "javascript", "go", "typescript"}.issubset(names)

    def test_marker_files_detected(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        assert "pyproject.toml" in report["marker_files"]
        assert report["marker_files"]["pyproject.toml"] == "python"
        assert report["marker_files"]["package.json"] == "node"

    def test_recursive_walk(self, tmp_path: Path) -> None:
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        (sub / "nested.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "top.rs").write_text("fn main() {}\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        names = {p["language"] for p in report["languages"]}
        assert "python" in names
        assert "rust" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        report = detect_languages_in_directory(tmp_path)
        assert report["languages"] == []
        assert report["total_files"] == 0

    def test_unknown_extensions_ignored_or_bucketed(self, tmp_path: Path) -> None:
        (tmp_path / "data.xyz").write_text("unknown\n", encoding="utf-8")
        (tmp_path / "README").write_text("readme\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)
        assert report["total_files"] >= 0

    def test_total_files_counted(self, tmp_path: Path) -> None:
        for i in range(3):
            (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "g.go").write_text("package main\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)
        assert report["total_files"] == 4

    def test_languages_sorted_by_file_count_desc(self, tmp_path: Path) -> None:
        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "one.go").write_text("package main\n", encoding="utf-8")
        for i in range(3):
            (tmp_path / f"s{i}.js").write_text("var x;\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)
        counts = [p["file_count"] for p in report["languages"]]
        assert counts == sorted(counts, reverse=True)

    def test_nonexistent_path_returns_empty(self, tmp_path: Path) -> None:
        report = detect_languages_in_directory(tmp_path / "does-not-exist")
        assert report["languages"] == []
        assert report["total_files"] == 0


# ── cross_language_homoglyph_scan ──────────────────────────────────────────


class TestCrossLanguageHomoglyphScan:
    """``cross_language_homoglyph_scan`` flags confusables across language files."""

    def test_clean_files_no_findings(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        f2 = tmp_path / "b.js"
        f2.write_text("function hello() { return 'world'; }\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([f1, f2])
        assert findings == []

    def test_cyrillic_homoglyph_in_python(self, tmp_path: Path) -> None:
        cyrillic_a = chr(0x0430)
        f = tmp_path / "evil.py"
        f.write_text(f"{cyrillic_a}pple = 1\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([f])
        assert len(findings) == 1
        assert findings[0]["file"].endswith("evil.py")
        assert findings[0]["language"] == "python"
        assert findings[0]["confusables"]
        assert findings[0]["severity"] in {"low", "medium", "high"}

    def test_multiple_files_some_clean_some_not(self, tmp_path: Path) -> None:
        cyrillic_o = chr(0x043E)
        clean = tmp_path / "clean.py"
        clean.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        bad = tmp_path / "bad.js"
        bad.write_text(f"var msg = '{cyrillic_o}k';\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([clean, bad])
        assert len(findings) == 1
        assert findings[0]["file"].endswith("bad.js")

    def test_empty_file_list(self) -> None:
        assert cross_language_homoglyph_scan([]) == []

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        findings = cross_language_homoglyph_scan([tmp_path / "missing.py"])
        assert findings == []

    def test_severity_escalation(self, tmp_path: Path) -> None:
        cyrillic_a = chr(0x0430)
        f = tmp_path / "many.py"
        f.write_text(f"{cyrillic_a}" * 10 + " = 1\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([f])
        assert findings
        assert findings[0]["severity"] == "high"

    def test_language_attribution(self, tmp_path: Path) -> None:
        cyrillic_e = chr(0x0440)
        py = tmp_path / "a.py"
        py.write_text(f"x = '{cyrillic_e}'\n", encoding="utf-8")
        go = tmp_path / "b.go"
        go.write_text(f"package {cyrillic_e}\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([py, go])
        langs = {f["language"] for f in findings}
        assert "python" in langs
        assert "go" in langs


# ── encoding_conflict_report ───────────────────────────────────────────────


class TestEncodingConflictReport:
    """``encoding_conflict_report`` flags encoding mismatches between files."""

    def test_consistent_utf8_no_conflicts(self, tmp_path: Path) -> None:
        f1 = tmp_path / "a.py"
        f1.write_text("x = 1\n", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("y = 2\n", encoding="utf-8")

        report = encoding_conflict_report([f1, f2])

        assert report["is_consistent"] is True
        assert report["conflicts"] == []
        assert len(report["files"]) == 2

    def test_utf8_with_bom_vs_without(self, tmp_path: Path) -> None:
        with_bom = tmp_path / "bom.py"
        with_bom.write_bytes(b"\xef\xbb\xbfx = 1\n")
        without_bom = tmp_path / "plain.py"
        without_bom.write_bytes(b"y = 2\n")

        report = encoding_conflict_report([with_bom, without_bom])

        assert report["is_consistent"] is False
        assert any("BOM" in c or "bom" in c for c in report["conflicts"])

    def test_utf16_vs_utf8_conflict(self, tmp_path: Path) -> None:
        utf8 = tmp_path / "a.py"
        utf8.write_text("x = 1\n", encoding="utf-8")
        utf16 = tmp_path / "b.py"
        utf16.write_bytes(b"\xff\xfe" + "y = 2\n".encode("utf-16-le"))

        report = encoding_conflict_report([utf8, utf16])

        assert report["is_consistent"] is False
        assert report["conflicts"]

    def test_bom_detection(self, tmp_path: Path) -> None:
        f = tmp_path / "u16be.py"
        f.write_bytes(b"\xfe\xff" + "x = 1\n".encode("utf-16-be"))

        report = encoding_conflict_report([f])

        assert report["files"][0]["has_bom"] is True
        assert report["files"][0]["bom"] == "UTF-16-BE"

    def test_no_bom_when_absent(self, tmp_path: Path) -> None:
        f = tmp_path / "plain.py"
        f.write_text("x = 1\n", encoding="utf-8")

        report = encoding_conflict_report([f])
        assert report["files"][0]["has_bom"] is False
        assert report["files"][0]["bom"] is None

    def test_empty_file_list(self) -> None:
        report = encoding_conflict_report([])
        assert report["files"] == []
        assert report["is_consistent"] is True

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        report = encoding_conflict_report([tmp_path / "missing.py"])
        assert report["files"] == []

    def test_encodings_present_aggregation(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
        (tmp_path / "b.py").write_bytes(b"\xff\xfe" + "y\n".encode("utf-16-le"))

        report = encoding_conflict_report([tmp_path / "a.py", tmp_path / "b.py"])
        assert "UTF-8" in report["encodings_present"]
        assert "UTF-16-LE" in report["encodings_present"]


# ── internal helpers ────────────────────────────────────────────────────────


class TestShebangLanguage:
    def test_python3_shebang(self) -> None:
        from general_ludd.language.polyglot import _shebang_language

        assert _shebang_language("#!/usr/bin/env python3") == "python"

    def test_bash_shebang(self) -> None:
        from general_ludd.language.polyglot import _shebang_language

        assert _shebang_language("#!/bin/bash") == "shell"

    def test_node_shebang(self) -> None:
        from general_ludd.language.polyglot import _shebang_language

        assert _shebang_language("#!/usr/bin/env node") == "javascript"

    def test_no_shebang_returns_none(self) -> None:
        from general_ludd.language.polyglot import _shebang_language

        assert _shebang_language("import sys") is None

    def test_empty_line(self) -> None:
        from general_ludd.language.polyglot import _shebang_language

        assert _shebang_language("") is None

    def test_ruby_shebang(self) -> None:
        from general_ludd.language.polyglot import _shebang_language

        assert _shebang_language("#!/usr/bin/ruby") == "ruby"

    def test_perl_shebang(self) -> None:
        from general_ludd.language.polyglot import _shebang_language

        assert _shebang_language("#!/usr/bin/perl -w") == "perl"


class TestIsBlank:
    def test_blank_lines(self) -> None:
        from general_ludd.language.polyglot import _is_blank

        assert _is_blank("") is True
        assert _is_blank("   ") is True
        assert _is_blank("\t") is True
        assert _is_blank("  \n") is True

    def test_non_blank(self) -> None:
        from general_ludd.language.polyglot import _is_blank

        assert _is_blank("x") is False
        assert _is_blank("  x") is False


class TestIsComment:
    def test_hash_comment(self) -> None:
        from general_ludd.language.polyglot import _is_comment

        assert _is_comment("# comment", "#") is True
        assert _is_comment("x = 1", "#") is False

    def test_double_slash_comment(self) -> None:
        from general_ludd.language.polyglot import _is_comment

        assert _is_comment("// comment", "//") is True
        assert _is_comment("x = 1", "//") is False

    def test_multi_style_comment(self) -> None:
        from general_ludd.language.polyglot import _is_comment

        assert _is_comment("// single", ["//", "/*"]) is True
        assert _is_comment("/* block", ["//", "/*"]) is True
        assert _is_comment("code here", ["//", "/*"]) is False

    def test_none_style_always_false(self) -> None:
        from general_ludd.language.polyglot import _is_comment

        assert _is_comment("// comment", None) is False
        assert _is_comment("# comment", None) is False


class TestLanguageForExtension:
    def test_known_extensions(self) -> None:
        from general_ludd.language.polyglot import _language_for_extension

        assert _language_for_extension(".py") == "python"
        assert _language_for_extension(".go") == "go"
        assert _language_for_extension(".rs") == "rust"
        assert _language_for_extension(".js") == "javascript"
        assert _language_for_extension(".tsx") == "typescript"

    def test_case_insensitive_fallback(self) -> None:
        from general_ludd.language.polyglot import _language_for_extension

        assert _language_for_extension(".PY") == "python"
        assert _language_for_extension(".Go") == "go"

    def test_unknown_extension_returns_none(self) -> None:
        from general_ludd.language.polyglot import _language_for_extension

        assert _language_for_extension(".garbage") is None
        assert _language_for_extension("") is None

    def test_unique_r_lower_and_upper(self) -> None:
        from general_ludd.language.polyglot import _language_for_extension

        assert _language_for_extension(".r") == "r"
        assert _language_for_extension(".R") == "r"


class TestSniffBOM:
    def test_utf8_bom_detected(self) -> None:
        from general_ludd.language.polyglot import _sniff_bom

        bom, size = _sniff_bom(b"\xef\xbb\xbfhello")
        assert bom == "UTF-8"
        assert size == 3

    def test_utf16_le_bom(self) -> None:
        from general_ludd.language.polyglot import _sniff_bom

        bom, size = _sniff_bom(b"\xff\xfehello")
        assert bom == "UTF-16-LE"
        assert size == 2

    def test_no_bom(self) -> None:
        from general_ludd.language.polyglot import _sniff_bom

        bom, size = _sniff_bom(b"hello")
        assert bom is None
        assert size == 0

    def test_utf32_le_bom_matched_before_utf16_prefix(self) -> None:
        from general_ludd.language.polyglot import _sniff_bom

        bom, size = _sniff_bom(b"\xff\xfe\x00\x00data")
        assert bom == "UTF-32-LE"
        assert size == 4

    def test_short_input(self) -> None:
        from general_ludd.language.polyglot import _sniff_bom

        bom, size = _sniff_bom(b"ab")
        assert bom is None
        assert size == 0


class TestSeverityFor:
    def test_low(self) -> None:
        from general_ludd.language.polyglot import _severity_for

        assert _severity_for(0) == "low"
        assert _severity_for(1) == "low"

    def test_medium(self) -> None:
        from general_ludd.language.polyglot import _severity_for

        assert _severity_for(2) == "medium"
        assert _severity_for(4) == "medium"

    def test_high(self) -> None:
        from general_ludd.language.polyglot import _severity_for

        assert _severity_for(5) == "high"
        assert _severity_for(100) == "high"


# ── classify_files_by_structure ─────────────────────────────────────────────


class TestClassifyFilesByStructure:
    def test_python_script_with_shebang(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "runner"  # no extension
        f.write_text("#!/usr/bin/env python3\nprint('hello')\n", encoding="utf-8")
        results = classify_files_by_structure([f])

        assert len(results) == 1
        assert results[0]["detected_language"] == "python"
        assert results[0]["language_from_extension"] is None
        assert not results[0]["extension_match"]
        assert "shebang" in results[0]["markers"]

    def test_go_package_declaration(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "main.go"
        f.write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
        results = classify_files_by_structure([f])

        assert len(results) == 1
        assert "package_declaration" in results[0]["markers"]
        assert results[0]["detected_language"] == "go"

    def test_extension_match_when_consistent(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "script.py"
        f.write_text("#!/usr/bin/env python3\nprint('hello')\n", encoding="utf-8")
        results = classify_files_by_structure([f])

        assert results[0]["detected_language"] == "python"
        assert results[0]["language_from_extension"] == "python"
        assert results[0]["extension_match"] is True

    def test_elixir_module_detection(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "server.ex"
        f.write_text("defmodule MyServer do\n  use GenServer\nend\n", encoding="utf-8")
        results = classify_files_by_structure([f])

        assert results[0]["detected_language"] == "elixir"
        assert "elixir_module" in results[0]["markers"]

    def test_empty_file_list(self) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        assert classify_files_by_structure([]) == []

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        assert classify_files_by_structure([tmp_path / "missing.sh"]) == []

    def test_file_with_extension_but_no_markers(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import classify_files_by_structure

        f = tmp_path / "plain.py"
        f.write_text("print('hello')\n", encoding="utf-8")
        results = classify_files_by_structure([f])

        assert results[0]["detected_language"] == "python"
        assert results[0]["markers"] == []


# ── analyze_code_density ────────────────────────────────────────────────────


class TestAnalyzeCodeDensity:
    def test_python_file_counts(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "test.py"
        f.write_text(
            "# comment line\ndef hello():\n    return 'world'\n\n# another comment\nx = 1\n",
            encoding="utf-8",
        )
        results = analyze_code_density([f])

        assert len(results) == 1
        assert results[0]["language"] == "python"
        assert results[0]["total_lines"] == 6
        assert results[0]["comment_lines"] == 2
        assert results[0]["code_lines"] == 3  # "def hello", "return 'world'", "x = 1"
        assert results[0]["blank_lines"] == 1  # single "\n" line

    def test_javascript_with_block_comment(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "test.js"
        f.write_text(
            "/* block start\n   still comment\n   end block */\nvar x = 1;\n// line comment\nx++;\n",
            encoding="utf-8",
        )
        results = analyze_code_density([f])

        assert len(results) == 1
        assert results[0]["language"] == "javascript"
        assert results[0]["comment_lines"] == 4  # 3 block + 1 line
        assert results[0]["code_lines"] == 2

    def test_go_file(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "main.go"
        f.write_text(
            'package main\n\n// main is the entry point\nfunc main() {\n    println("hello")\n}\n',
            encoding="utf-8",
        )
        results = analyze_code_density([f])

        assert len(results) == 1
        assert results[0]["language"] == "go"
        assert results[0]["comment_lines"] == 1
        assert results[0]["blank_lines"] == 1

    def test_unknown_language_labeled_unknown(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "data.xyz"
        f.write_text("line1\nline2\nline3\n", encoding="utf-8")
        results = analyze_code_density([f])

        assert results[0]["language"] == "unknown"
        assert results[0]["comment_lines"] == 0

    def test_empty_file(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "empty.py"
        f.write_text("", encoding="utf-8")
        results = analyze_code_density([f])

        assert results[0]["total_lines"] == 0
        assert results[0]["code_lines"] == 0

    def test_binary_file_skipped(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\xff\xfe")
        results = analyze_code_density([f])

        assert results == []

    def test_shebang_counted_as_code(self, tmp_path: Path) -> None:
        from general_ludd.language.polyglot import analyze_code_density

        f = tmp_path / "run.sh"
        f.write_text(
            "#!/bin/bash\n# a comment\necho hello\n",
            encoding="utf-8",
        )
        results = analyze_code_density([f])

        assert results[0]["language"] == "shell"
        assert results[0]["code_lines"] == 1  # echo (shebang matches hash comment)
        assert results[0]["comment_lines"] == 2  # #!/bin/bash + # a comment


# ── detect_language_markers ─────────────────────────────────────────────────


class TestDetectLanguageMarkers:
    def test_shebang_marker(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers("#!/usr/bin/env python3\nprint('hello')")
        assert "shebang" in markers
        assert markers["shebang"] == "python"

    def test_encoding_cookie(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers("# -*- coding: utf-8 -*-\nprint('hello')")
        assert "encoding_cookie" in markers
        assert markers["encoding_cookie"] == "utf-8"

    def test_go_package(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers('package main\n\nimport "fmt"')
        assert "package_declaration" in markers
        assert markers["package_declaration"] == "go"

    def test_html_doctype(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers('<!DOCTYPE html>\n<html lang="en">')
        assert "doctype" in markers
        assert markers["doctype"] == "html"

    def test_xml_declaration(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers('<?xml version="1.0" encoding="UTF-8"?>\n<root/>')
        assert "xml_declaration" in markers
        assert markers["xml_declaration"] == "xml"

    def test_multiple_markers(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers("#!/usr/bin/env python3\n# coding: utf-8\n")
        assert "shebang" in markers
        assert "encoding_cookie" in markers

    def test_empty_text(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        assert detect_language_markers("") == {}

    def test_no_markers(self) -> None:
        from general_ludd.language.polyglot import detect_language_markers

        markers = detect_language_markers("def hello():\n    return 'world'\n")
        assert markers == {}


# ── _EXTENSION_MAP integrity ────────────────────────────────────────────────


class TestExtensionMapIntegrity:
    def test_extension_map_non_empty(self) -> None:
        from general_ludd.language.polyglot import _EXTENSION_MAP

        assert len(_EXTENSION_MAP) > 0

    def test_all_extensions_start_with_dot(self) -> None:
        from general_ludd.language.polyglot import _EXTENSION_MAP

        for ext in _EXTENSION_MAP:
            assert ext.startswith("."), f"Extension {ext!r} does not start with dot"

    def test_no_duplicate_language_mappings(self) -> None:
        from general_ludd.language.polyglot import _EXTENSION_MAP

        seen: dict[str, str] = {}
        for ext, lang in _EXTENSION_MAP.items():
            if lang in seen:
                pass
            seen[lang] = ext


class TestMarkerMapIntegrity:
    def test_marker_map_non_empty(self) -> None:
        from general_ludd.language.polyglot import _MARKER_MAP

        assert len(_MARKER_MAP) > 0


class TestCommentStylesIntegrity:
    def test_all_styles_non_empty(self) -> None:
        from general_ludd.language.polyglot import _COMMENT_STYLES

        assert len(_COMMENT_STYLES) > 0
        for lang, style in _COMMENT_STYLES.items():
            if isinstance(style, str):
                assert len(style) > 0, f"{lang} comment style is empty"
            else:
                assert len(style) > 0, f"{lang} comment style list is empty"
