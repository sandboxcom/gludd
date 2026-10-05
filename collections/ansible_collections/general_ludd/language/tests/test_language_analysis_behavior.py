"""Behavioral coverage for the collection-level language analysis APIs.

These tests live with the Ansible collection tests because
``make test-language-expert`` includes this directory as its public acceptance
surface.  They exercise the application APIs through real files and a
deterministic PATH probe so that newly added analysis modules cannot silently
fall outside that gate.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from general_ludd.language.corpus import CorpusAnalyzer
from general_ludd.language.cross_patterns import (
    detect_cross_language_imports,
    detect_ffi_patterns,
    detect_polyglot_builds,
    detect_script_invocations,
)
from general_ludd.language.polyglot import (
    _detect_encoding,
    _language_for_extension,
    _sniff_bom,
    analyze_code_density,
    classify_files_by_structure,
    cross_language_homoglyph_scan,
    detect_language_markers,
    detect_languages_in_directory,
    encoding_conflict_report,
)
from general_ludd.language.tooling import (
    LANGUAGE_TOOLS,
    all_supported_languages,
    detect_available_tools,
    get_cross_language_compat,
    get_language_tools,
)


def test_corpus_analysis_handles_text_binary_language_and_encoding_mix(
    tmp_path: Path,
) -> None:
    python_file = tmp_path / "worker.py"
    python_file.write_text("alpha beta alpha\n", encoding="utf-8")
    javascript_file = tmp_path / "worker.js"
    javascript_file.write_text("beta gamma\n", encoding="utf-8")
    unknown_file = tmp_path / "notes.custom"
    unknown_file.write_text("alpha_delta 8\n", encoding="utf-8")
    utf16_file = tmp_path / "legacy.txt"
    utf16_file.write_bytes(b"\xff\xfe" + "legacy\n".encode("utf-16-le"))

    analyzer = CorpusAnalyzer(
        [python_file, javascript_file, unknown_file, utf16_file, tmp_path / "missing.py"]
    )

    complete = analyzer.frequency_analysis()
    limited = analyzer.frequency_analysis(top_n=1)
    word_counts = cast(dict[str, int], complete["word_counts"])
    top_words = cast(list[object], limited["top_words"])
    assert word_counts["alpha"] == 2
    assert complete["total_words"] == 7
    assert len(top_words) == 1
    assert analyzer.extract_ngrams(2, "char")["al"] == 3
    assert analyzer.extract_ngrams(2, "word")["alpha beta"] == 1
    assert analyzer.language_distribution() == {
        "python": 1,
        "javascript": 1,
        "unknown": 2,
    }

    encodings = analyzer.encoding_statistics()
    assert encodings["by_encoding"] == {"UTF-8": 3, "UTF-16-LE": 1}
    assert encodings["files_with_bom"] == 1
    assert encodings["is_consistent"] is False

    with pytest.raises(ValueError, match="unit must be"):
        analyzer.extract_ngrams(2, "byte")
    with pytest.raises(ValueError, match="n must be"):
        analyzer.extract_ngrams(0, "word")


def test_cross_language_detectors_cover_invocation_ffi_and_build_markers(
    tmp_path: Path,
) -> None:
    python_file = tmp_path / "bridge.py"
    python_file.write_text(
        "import subprocess\n"
        "from cffi import FFI\n"
        "ffibuilder = FFI()\n"
        "subprocess.run(['node', 'app.js'])\n",
        encoding="utf-8",
    )
    rust_file = tmp_path / "bridge.rs"
    rust_file.write_text('extern "C" { fn run(); }\n', encoding="utf-8")
    clean_file = tmp_path / "clean.py"
    clean_file.write_text("answer = 42\n", encoding="utf-8")
    binary_file = tmp_path / "binary.py"
    binary_file.write_bytes(b"\xff\xfe\x00\x00")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='bridge'\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")

    files: list[str | Path] = [
        python_file,
        rust_file,
        clean_file,
        binary_file,
        tmp_path / "missing.py",
    ]
    imports = detect_cross_language_imports(files)
    assert {finding["source_language"] for finding in imports} == {"python", "rust"}
    assert any(
        "cffi" in cast(list[str], finding["patterns"])
        for finding in imports
    )

    ffi = detect_ffi_patterns(files)
    assert {finding["ffi_type"] for finding in ffi} == {"cffi", "extern_block"}

    builds = detect_polyglot_builds(tmp_path)
    assert {finding["build_system"] for finding in builds} == {"python", "node"}
    assert detect_polyglot_builds(tmp_path / "missing") == []

    invocations = detect_script_invocations(files)
    assert invocations == [
        {
            "file": str(python_file),
            "source_language": "python",
            "target_languages": ["javascript"],
        }
    ]


def test_polyglot_directory_walk_is_deterministic_and_skips_vendored_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.PY").write_text("answer = 42\n", encoding="utf-8")
    (tmp_path / "web.js").write_text("export const answer = 42;\n", encoding="utf-8")
    (tmp_path / "README").write_text("polyglot\n", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}\n", encoding="utf-8")
    vendored = tmp_path / "node_modules"
    vendored.mkdir()
    (vendored / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    (tmp_path / "linked.py").symlink_to(tmp_path / "app.PY")

    report = detect_languages_in_directory(tmp_path)
    assert report["total_files"] == 4
    assert report["marker_files"] == {"package.json": "node"}
    assert report["languages"] == [
        {
            "language": "javascript",
            "file_count": 1,
            "extensions": [".js"],
            "marker_files": [],
        },
        {
            "language": "python",
            "file_count": 1,
            "extensions": [".PY"],
            "marker_files": [],
        },
    ]
    assert _language_for_extension(".UNKNOWN") is None
    assert detect_languages_in_directory(tmp_path / "missing")["languages"] == []


def test_polyglot_security_and_encoding_reports_include_edge_conditions(
    tmp_path: Path,
) -> None:
    low = tmp_path / "low.py"
    low.write_text("name = '\u0430'\n", encoding="utf-8")
    medium = tmp_path / "medium.js"
    medium.write_text("const name = '\u0430\u0430';\n", encoding="utf-8")
    high = tmp_path / "high.custom"
    high.write_text("\u0430" * 5, encoding="utf-8")
    clean = tmp_path / "clean.go"
    clean.write_text("package main\n", encoding="utf-8")
    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff\xfe\x00\x00")

    findings = cross_language_homoglyph_scan(
        [low, medium, high, clean, binary, tmp_path / "missing.py"]
    )
    assert [finding["severity"] for finding in findings] == ["low", "medium", "high"]
    assert findings[-1]["language"] == "unknown"

    utf8_bom = tmp_path / "bom.py"
    utf8_bom.write_bytes(b"\xef\xbb\xbfvalue = 1\n")
    utf8_plain = tmp_path / "plain.py"
    utf8_plain.write_text("value = 2\n", encoding="utf-8")
    utf16 = tmp_path / "utf16.py"
    utf16.write_bytes(b"\xff\xfe" + "value = 3\n".encode("utf-16-le"))

    report = encoding_conflict_report(
        [utf8_bom, utf8_plain, utf16, tmp_path / "missing.py"]
    )
    boms = cast(list[str], report["boms_present"])
    conflicts = cast(list[str], report["conflicts"])
    assert report["is_consistent"] is False
    assert set(boms) == {"UTF-8", "UTF-16-LE"}
    assert any("Multiple encodings" in conflict for conflict in conflicts)
    assert any("Inconsistent UTF-8 BOM" in conflict for conflict in conflicts)
    assert any("Mixed BOM types" in conflict for conflict in conflicts)

    assert _sniff_bom(b"") == (None, 0)
    assert _sniff_bom(b"text") == (None, 0)
    assert _detect_encoding(tmp_path / "removed.py")["encoding"] == "UTF-8"


def test_structure_classification_prefers_content_and_handles_bad_input(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "worker"
    executable.write_text("#!/usr/bin/env python3\nprint('ok')\n", encoding="utf-8")
    go_file = tmp_path / "ambiguous.txt"
    go_file.write_text("package main\nfunc main() {}\n", encoding="utf-8")
    elixir_file = tmp_path / "server"
    elixir_file.write_text("defmodule Server do\n  use GenServer\nend\n", encoding="utf-8")
    extension_only = tmp_path / "query.sql"
    extension_only.write_text("SELECT 1;\n", encoding="utf-8")
    unknown = tmp_path / "notes"
    unknown.write_text("plain text\n", encoding="utf-8")
    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff\xfe\x00\x00")

    rows = classify_files_by_structure(
        [executable, go_file, elixir_file, extension_only, unknown, binary, tmp_path / "missing"]
    )
    by_name = {Path(str(row["file"])).name: row for row in rows}
    assert by_name["worker"]["detected_language"] == "python"
    assert by_name["worker"]["markers"] == ["shebang"]
    assert by_name["ambiguous.txt"]["detected_language"] == "go"
    assert by_name["server"]["detected_language"] == "elixir"
    assert by_name["query.sql"]["extension_match"] is True
    assert by_name["notes"]["detected_language"] is None
    assert "binary.py" not in by_name


def test_code_density_counts_hash_line_and_block_comments(tmp_path: Path) -> None:
    python_file = tmp_path / "worker.py"
    python_file.write_text("# heading\n\nanswer = 42\n", encoding="utf-8")
    javascript_file = tmp_path / "worker.js"
    javascript_file.write_text(
        "/* heading\ncontinued */\n// detail\nconst answer = 42;\n\n",
        encoding="utf-8",
    )
    unknown = tmp_path / "README"
    unknown.write_text("plain\n", encoding="utf-8")
    binary = tmp_path / "binary.py"
    binary.write_bytes(b"\xff\xfe\x00\x00")

    reports = analyze_code_density(
        [python_file, javascript_file, unknown, binary, tmp_path / "missing.py"]
    )
    by_name = {Path(str(report["file"])).name: report for report in reports}
    assert by_name["worker.py"] == {
        "file": str(python_file),
        "language": "python",
        "total_lines": 3,
        "comment_lines": 1,
        "code_lines": 1,
        "blank_lines": 1,
    }
    assert by_name["worker.js"]["comment_lines"] == 3
    assert by_name["worker.js"]["code_lines"] == 1
    assert by_name["README"]["language"] == "unknown"
    assert by_name["README"]["code_lines"] == 1


def test_language_marker_detection_covers_supported_content_signatures() -> None:
    assert detect_language_markers("") == {}
    assert detect_language_markers("#!/usr/bin/env python3\n# coding: latin-1\n") == {
        "shebang": "python",
        "encoding_cookie": "latin-1",
    }
    assert detect_language_markers("package main\n") == {"package_declaration": "go"}
    assert detect_language_markers("  <!DOCTYPE html>\n<html></html>\n") == {
        "doctype": "html"
    }
    assert detect_language_markers("\n<?xml version='1.0'?>\n") == {
        "xml_declaration": "xml"
    }


def test_tooling_queries_are_case_insensitive_and_path_probes_are_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_which(command: str) -> str | None:
        if command in {"ruff", "pytest", "uv"}:
            return f"/tools/{command}"
        return None

    monkeypatch.setattr("general_ludd.language.tooling.shutil.which", fake_which)

    assert get_language_tools("PYTHON") == LANGUAGE_TOOLS["python"]
    assert get_language_tools("unknown") is None
    assert all_supported_languages() == frozenset(LANGUAGE_TOOLS)

    selected = detect_available_tools(["python", "go", "unknown"])
    assert selected["python"] == {
        "lint": "ruff",
        "test": "pytest",
        "build": None,
        "format": "ruff",
        "package_manager": "uv",
    }
    assert selected["go"] == {
        "lint": None,
        "test": None,
        "build": None,
        "format": None,
        "package_manager": None,
    }
    assert selected["unknown"] == {
        "lint": None,
        "test": None,
        "build": None,
        "format": None,
        "package_manager": None,
    }
    assert set(detect_available_tools()) == set(LANGUAGE_TOOLS)
    assert get_cross_language_compat("JavaScript") == ["typescript"]
    assert get_cross_language_compat("unknown") == []
