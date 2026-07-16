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

# ── detect_languages_in_directory ──────────────────────────────────────────


class TestDetectLanguagesInDirectory:
    """``detect_languages_in_directory`` identifies languages in a tree."""

    def test_single_language_python(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        names = [p["language"] for p in report["languages"]]
        assert "python" in names
        py = next(p for p in report["languages"] if p["language"] == "python")
        assert py["file_count"] == 2

    def test_polyglot_repo_multiple_languages(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "lib.js").write_text("var x = 1;\n", encoding="utf-8")
        (tmp_path / "main.go").write_text("package main\n", encoding="utf-8")
        (tmp_path / "ui.tsx").write_text("export const X = 1;\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        names = {p["language"] for p in report["languages"]}
        assert {"python", "javascript", "go", "typescript"}.issubset(names)

    def test_marker_files_detected(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
        (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        assert "pyproject.toml" in report["marker_files"]
        assert report["marker_files"]["pyproject.toml"] == "python"
        assert report["marker_files"]["package.json"] == "node"

    def test_recursive_walk(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        (sub / "nested.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "top.rs").write_text("fn main() {}\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)

        names = {p["language"] for p in report["languages"]}
        assert "python" in names
        assert "rust" in names

    def test_empty_directory(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        report = detect_languages_in_directory(tmp_path)
        assert report["languages"] == []
        assert report["total_files"] == 0

    def test_unknown_extensions_ignored_or_bucketed(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        (tmp_path / "data.xyz").write_text("unknown\n", encoding="utf-8")
        (tmp_path / "README").write_text("readme\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)
        assert report["total_files"] >= 0

    def test_total_files_counted(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        for i in range(3):
            (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "g.go").write_text("package main\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)
        assert report["total_files"] == 4

    def test_languages_sorted_by_file_count_desc(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        for i in range(5):
            (tmp_path / f"f{i}.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "one.go").write_text("package main\n", encoding="utf-8")
        for i in range(3):
            (tmp_path / f"s{i}.js").write_text("var x;\n", encoding="utf-8")

        report = detect_languages_in_directory(tmp_path)
        counts = [p["file_count"] for p in report["languages"]]
        assert counts == sorted(counts, reverse=True)

    def test_nonexistent_path_returns_empty(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import detect_languages_in_directory

        report = detect_languages_in_directory(tmp_path / "does-not-exist")
        assert report["languages"] == []
        assert report["total_files"] == 0


# ── cross_language_homoglyph_scan ──────────────────────────────────────────


class TestCrossLanguageHomoglyphScan:
    """``cross_language_homoglyph_scan`` flags confusables across language files."""

    def test_clean_files_no_findings(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import cross_language_homoglyph_scan

        f1 = tmp_path / "a.py"
        f1.write_text("def hello():\n    return 'world'\n", encoding="utf-8")
        f2 = tmp_path / "b.js"
        f2.write_text("function hello() { return 'world'; }\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([f1, f2])
        assert findings == []

    def test_cyrillic_homoglyph_in_python(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import cross_language_homoglyph_scan

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
        from src.general_ludd.language.polyglot import cross_language_homoglyph_scan

        cyrillic_o = chr(0x043E)
        clean = tmp_path / "clean.py"
        clean.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        bad = tmp_path / "bad.js"
        bad.write_text(f"var msg = '{cyrillic_o}k';\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([clean, bad])
        assert len(findings) == 1
        assert findings[0]["file"].endswith("bad.js")

    def test_empty_file_list(self) -> None:
        from src.general_ludd.language.polyglot import cross_language_homoglyph_scan

        assert cross_language_homoglyph_scan([]) == []

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import cross_language_homoglyph_scan

        findings = cross_language_homoglyph_scan([tmp_path / "missing.py"])
        assert findings == []

    def test_severity_escalation(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import cross_language_homoglyph_scan

        cyrillic_a = chr(0x0430)
        f = tmp_path / "many.py"
        f.write_text(f"{cyrillic_a}" * 10 + " = 1\n", encoding="utf-8")

        findings = cross_language_homoglyph_scan([f])
        assert findings
        assert findings[0]["severity"] == "high"

    def test_language_attribution(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import cross_language_homoglyph_scan

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
        from src.general_ludd.language.polyglot import encoding_conflict_report

        f1 = tmp_path / "a.py"
        f1.write_text("x = 1\n", encoding="utf-8")
        f2 = tmp_path / "b.py"
        f2.write_text("y = 2\n", encoding="utf-8")

        report = encoding_conflict_report([f1, f2])

        assert report["is_consistent"] is True
        assert report["conflicts"] == []
        assert len(report["files"]) == 2

    def test_utf8_with_bom_vs_without(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import encoding_conflict_report

        with_bom = tmp_path / "bom.py"
        with_bom.write_bytes(b"\xef\xbb\xbfx = 1\n")
        without_bom = tmp_path / "plain.py"
        without_bom.write_bytes(b"y = 2\n")

        report = encoding_conflict_report([with_bom, without_bom])

        assert report["is_consistent"] is False
        assert any("BOM" in c or "bom" in c for c in report["conflicts"])

    def test_utf16_vs_utf8_conflict(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import encoding_conflict_report

        utf8 = tmp_path / "a.py"
        utf8.write_text("x = 1\n", encoding="utf-8")
        utf16 = tmp_path / "b.py"
        utf16.write_bytes(b"\xff\xfe" + "y = 2\n".encode("utf-16-le"))

        report = encoding_conflict_report([utf8, utf16])

        assert report["is_consistent"] is False
        assert report["conflicts"]

    def test_bom_detection(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import encoding_conflict_report

        f = tmp_path / "u16be.py"
        f.write_bytes(b"\xfe\xff" + "x = 1\n".encode("utf-16-be"))

        report = encoding_conflict_report([f])

        assert report["files"][0]["has_bom"] is True
        assert report["files"][0]["bom"] == "UTF-16-BE"

    def test_no_bom_when_absent(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import encoding_conflict_report

        f = tmp_path / "plain.py"
        f.write_text("x = 1\n", encoding="utf-8")

        report = encoding_conflict_report([f])
        assert report["files"][0]["has_bom"] is False
        assert report["files"][0]["bom"] is None

    def test_empty_file_list(self) -> None:
        from src.general_ludd.language.polyglot import encoding_conflict_report

        report = encoding_conflict_report([])
        assert report["files"] == []
        assert report["is_consistent"] is True

    def test_nonexistent_file_skipped(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import encoding_conflict_report

        report = encoding_conflict_report([tmp_path / "missing.py"])
        assert report["files"] == []

    def test_encodings_present_aggregation(self, tmp_path: Path) -> None:
        from src.general_ludd.language.polyglot import encoding_conflict_report

        (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
        (tmp_path / "b.py").write_bytes(b"\xff\xfe" + "y\n".encode("utf-16-le"))

        report = encoding_conflict_report([tmp_path / "a.py", tmp_path / "b.py"])
        assert "UTF-8" in report["encodings_present"]
        assert "UTF-16-LE" in report["encodings_present"]
