"""Benchmark tests for NF.9 Language Expert analysis functions.

Measures wall-clock latency for the four core analysis surfaces:

- Homoglyph scan (``homoglyph_data``: confusables, invisible, bidi, mixed-script,
  skeleton, suspicious)
- Encoding detection (``polyglot._sniff_bom`` / ``_detect_encoding`` /
  ``encoding_conflict_report``)
- Font analysis (``font_data``: format id, table directory, metrics, web-format,
  variable-axes, kerning)
- Polyglot detection (``polyglot.detect_languages_in_directory`` +
  ``cross_language_homoglyph_scan``)

Each test uses ``time.perf_counter()`` (no pytest-benchmark dependency, matching
the pattern in ``test_vm_sandbox_overhead.py``) and asserts that the operation
completes within the NF.9 latency target of **< 100 ms for typical input**.
"""

from __future__ import annotations

import struct
import time
from pathlib import Path

from general_ludd.language.font_data import (
    get_font_metrics,
    has_kerning,
    has_variable_axes,
    identify_font_format,
    is_web_font_format,
    list_font_tables,
)
from general_ludd.language.homoglyph_data import (
    detect_bidi_overrides,
    detect_confusables,
    detect_invisible_chars,
    detect_mixed_script,
    generate_skeleton,
    is_suspicious,
)
from general_ludd.language.polyglot import (
    cross_language_homoglyph_scan,
    detect_languages_in_directory,
    encoding_conflict_report,
)

# NF.9 latency target: every analysis function must complete typical input in
# under 100 ms. Generous headroom above the observed microsecond-range cost so
# the benchmark catches algorithmic regressions (O(n^2) accidentally introduced)
# without flapping on slow CI runners.
LATENCY_TARGET_MS = 100.0

# Typical input sizes — representative of what the language expert sees in
# practice: a source file is a few KB, a directory walk hits tens of files,
# a font header is a few hundred bytes. These are NOT stress tests.
TYPICAL_TEXT_LEN = 4 * 1024
TYPICAL_FILE_COUNT = 25


# ── Helpers ─────────────────────────────────────────────────────────────────


def _typical_source_text() -> str:
    """Return ~4 KB of source-like text with a sprinkling of confusables.

    Mostly ASCII (the common case) with one Cyrillic ``U+0430`` per ~256
    chars so ``detect_confusables`` has real work without being a
    pathological dense-confusable input.
    """
    base = "def compute(x, y):\n    return x + y\n\n"
    chunk = base * 8
    text = ""
    while len(text) < TYPICAL_TEXT_LEN:
        text += chunk
    text = text[:TYPICAL_TEXT_LEN]
    # Insert a confusable every ~256 chars.
    return text[:255] + "\u0430" + text[256:]


def _typical_invisible_text() -> str:
    """~4 KB of text containing a few zero-width / bidi characters."""
    base = "logger.info('operation completed successfully')\n"
    text = (base * 64)[:TYPICAL_TEXT_LEN]
    # Sprinkle invisible chars at known positions.
    positions = [100, 500, 1500, 3000]
    chars = ["\u200b", "\u200c", "\u200d", "\u202e"]
    result = list(text)
    for pos, ch in zip(positions, chars, strict=True):
        if pos < len(result):
            result[pos] = ch
    return "".join(result)


def _typical_mixed_script_text() -> str:
    """~4 KB mixing Latin and Cyrillic scripts."""
    latin = "The quick brown fox jumps over the lazy dog. " * 16
    cyrillic = "Съешь ещё этих мягких французских булок, да выпей чаю. " * 16
    return (latin + cyrillic)[:TYPICAL_TEXT_LEN]


def _write_minimal_ttf(path: Path, num_tables: int = 9) -> None:
    """Write a minimal valid TTF with the given number of table records."""
    header = b"\x00\x01\x00\x00" + struct.pack(">HHHH", num_tables, 0, 0, 0)
    records = b""
    table_names = [
        b"cmap", b"head", b"hhea", b"hmtx", b"maxp",
        b"name", b"OS/2", b"post", b"glyf",
    ]
    for i in range(num_tables):
        tag = table_names[i % len(table_names)]
        records += struct.pack(">4sIII", tag, 0, 28 + i * 16, 64)
    path.write_bytes(header + records)


def _write_source_tree(root: Path) -> list[Path]:
    """Write a representative multi-language source tree under ``root``.

    Returns the list of created files (for the homoglyph scan target).
    """
    files: list[Path] = []
    samples = {
        "app.py": "def main():\n    print('hello')\n",
        "app_test.py": "def test_main():\n    assert main() is None\n",
        "utils.py": "PI = 3.14159\n",
        "index.js": "console.log('hi');\n",
        "main.go": "package main\nfunc main() {}\n",
        "lib.rs": "fn main() {}\n",
        "README.md": "# project\n",
        "Dockerfile": "FROM python:3.12\n",
    }
    (root / "src").mkdir()
    (root / "src" / "sub").mkdir()
    for i, (name, content) in enumerate(samples.items()):
        target = root / "src" if i < 6 else root
        path = target / name
        path.write_text(content)
        files.append(path)
    (root / "pyproject.toml").write_text("[project]\nname = 'bench'\n")
    (root / "package.json").write_text('{"name":"bench"}\n')
    return files


# ── a. Homoglyph scan latency ──────────────────────────────────────────────


def test_homoglyph_detect_confusables_latency() -> None:
    """``detect_confusables`` on ~4 KB of typical source text."""
    text = _typical_source_text()
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        findings = detect_confusables(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert findings, "fixture should contain at least one confusable"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"detect_confusables took {per_call_ms:.3f}ms/call over {len(text)} chars — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_homoglyph_detect_invisible_chars_latency() -> None:
    """``detect_invisible_chars`` on ~4 KB with embedded zero-width chars."""
    text = _typical_invisible_text()
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        findings = detect_invisible_chars(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert findings, "fixture should contain at least one invisible char"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"detect_invisible_chars took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_homoglyph_detect_bidi_overrides_latency() -> None:
    """``detect_bidi_overrides`` on text containing CVE-2021-42574 vectors."""
    text = _typical_invisible_text()
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        findings = detect_bidi_overrides(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert findings, "fixture should contain at least one bidi override"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"detect_bidi_overrides took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_homoglyph_detect_mixed_script_latency() -> None:
    """``detect_mixed_script`` on Latin+Cyrillic mixed text."""
    text = _typical_mixed_script_text()
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        result = detect_mixed_script(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert result["is_mixed"] is True, "fixture should be detected as mixed"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"detect_mixed_script took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_homoglyph_generate_skeleton_latency() -> None:
    """``generate_skeleton`` normalization on confusable-bearing text."""
    text = _typical_source_text()
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        skeleton = generate_skeleton(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert "\u0430" not in skeleton, "skeleton should replace the Cyrillic U+0430"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"generate_skeleton took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_homoglyph_is_suspicious_latency() -> None:
    """``is_suspicious`` fast-path on typical source text."""
    text = _typical_source_text()
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        result = is_suspicious(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert result is True, "fixture should be flagged suspicious"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"is_suspicious took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


# ── b. Encoding detection latency ──────────────────────────────────────────


def test_encoding_sniff_bom_latency() -> None:
    """BOM sniffing across the common BOM signatures (charset_map lookup path).

    Exercises ``polyglot._sniff_bom`` over the UTF-8 / UTF-16-LE / UTF-32-BE
    prefixes plus the no-BOM fallback — the hot path used by every per-file
    encoding detection.
    """
    from general_ludd.language.polyglot import _sniff_bom

    samples = [
        b"\xef\xbb\xbfimport os",
        b"\xff\xfehello",
        b"\x00\x00\xfe\xffdata",
        b"plain ascii, no bom at all here",
        b"\x2b\x2f\x76utf-7-ish",
    ]
    iterations = 200

    start = time.perf_counter()
    for _ in range(iterations):
        for head in samples:
            _sniff_bom(head)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / (iterations * len(samples))) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"_sniff_bom took {per_call_ms:.4f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_encoding_detect_per_file_latency(tmp_path: Path) -> None:
    """``polyglot._detect_encoding`` per-file read+sniff."""
    from general_ludd.language.polyglot import _detect_encoding

    files: list[Path] = []
    for i in range(TYPICAL_FILE_COUNT):
        path = tmp_path / f"file_{i}.py"
        if i % 3 == 0:
            path.write_bytes(b"\xef\xbb\xbf# coding: utf-8\nprint('hi')\n")
        else:
            path.write_bytes(b"print('plain')\n")
        files.append(path)

    start = time.perf_counter()
    for path in files:
        _detect_encoding(path)
    elapsed = time.perf_counter() - start

    per_file_ms = (elapsed / len(files)) * 1000
    assert per_file_ms < LATENCY_TARGET_MS, (
        f"_detect_encoding took {per_file_ms:.3f}ms/file over "
        f"{len(files)} files — target <{LATENCY_TARGET_MS}ms"
    )


def test_encoding_conflict_report_latency(tmp_path: Path) -> None:
    """``encoding_conflict_report`` across a typical set of files."""
    files: list[Path] = []
    for i in range(TYPICAL_FILE_COUNT):
        path = tmp_path / f"f_{i}.py"
        if i % 4 == 0:
            path.write_bytes(b"\xef\xbb\xbf# bom\n")
        elif i % 4 == 1:
            path.write_bytes(b"\xff\xfex")  # utf-16-le
        else:
            path.write_bytes(b"plain\n")
        files.append(path)

    iterations = 10
    start = time.perf_counter()
    for _ in range(iterations):
        report = encoding_conflict_report(files)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert len(report["files"]) == TYPICAL_FILE_COUNT
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"encoding_conflict_report took {per_call_ms:.3f}ms/call over "
        f"{TYPICAL_FILE_COUNT} files — target <{LATENCY_TARGET_MS}ms"
    )


# ── c. Font analysis latency ───────────────────────────────────────────────


def test_font_identify_format_latency() -> None:
    """``identify_font_format`` magic-byte dispatch (no I/O)."""
    headers = [
        b"\x00\x01\x00\x00",
        b"OTTO",
        b"wOFF",
        b"wOF2",
        b"ttcf",
        b"XXXXunknown",
    ]
    iterations = 500

    start = time.perf_counter()
    for _ in range(iterations):
        for header in headers:
            identify_font_format(header)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / (iterations * len(headers))) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"identify_font_format took {per_call_ms:.5f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_font_list_tables_latency(tmp_path: Path) -> None:
    """``list_font_tables`` parses a minimal TTF table directory."""
    font = tmp_path / "bench.ttf"
    _write_minimal_ttf(font, num_tables=9)
    iterations = 100

    start = time.perf_counter()
    for _ in range(iterations):
        tables = list_font_tables(str(font))
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert len(tables) == 9
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"list_font_tables took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_font_get_metrics_latency(tmp_path: Path) -> None:
    """``get_font_metrics`` extracts head/hhea fields from a minimal TTF."""
    font = tmp_path / "metrics.ttf"
    _write_minimal_ttf(font, num_tables=9)
    iterations = 100

    start = time.perf_counter()
    for _ in range(iterations):
        result = get_font_metrics(str(font))
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert "error" not in result or "format" in result, (
        f"get_font_metrics returned {result}"
    )
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"get_font_metrics took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_font_has_variable_axes_latency(tmp_path: Path) -> None:
    """``has_variable_axes`` checks for the fvar table."""
    font = tmp_path / "var.ttf"
    _write_minimal_ttf(font, num_tables=9)
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        has_variable_axes(str(font))
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"has_variable_axes took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_font_has_kerning_latency(tmp_path: Path) -> None:
    """``has_kerning`` checks for kern/GPOS tables."""
    font = tmp_path / "kern.ttf"
    _write_minimal_ttf(font, num_tables=9)
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        has_kerning(str(font))
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"has_kerning took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_font_is_web_format_latency(tmp_path: Path) -> None:
    """``is_web_font_format`` header read for woff/woff2 vs ttf."""
    woff = tmp_path / "sample.woff"
    woff.write_bytes(b"wOFF" + b"\x00" * 64)
    ttf = tmp_path / "sample.ttf"
    _write_minimal_ttf(ttf, num_tables=4)
    iterations = 100

    start = time.perf_counter()
    for _ in range(iterations):
        is_web_font_format(str(woff))
        is_web_font_format(str(ttf))
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / (iterations * 2)) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"is_web_font_format took {per_call_ms:.4f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


# ── d. Polyglot detection latency ──────────────────────────────────────────


def test_polyglot_detect_languages_latency(tmp_path: Path) -> None:
    """``detect_languages_in_directory`` walks a typical multi-language tree."""
    _write_source_tree(tmp_path)
    iterations = 10

    start = time.perf_counter()
    for _ in range(iterations):
        report = detect_languages_in_directory(tmp_path)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert report["languages"], "tree should contain at least one language"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"detect_languages_in_directory took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_polyglot_cross_language_homoglyph_scan_latency(tmp_path: Path) -> None:
    """``cross_language_homoglyph_scan`` across a typical file set."""
    files = _write_source_tree(tmp_path)
    # Give one file a confusable so the scan produces at least one finding.
    py_file = tmp_path / "src" / "app.py"
    py_file.write_bytes(
        (py_file.read_text() + "\n# \u0430 inline cyrillic a\n").encode("utf-8")
    )
    iterations = 10

    start = time.perf_counter()
    for _ in range(iterations):
        findings = cross_language_homoglyph_scan(files)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert findings, "scan should detect at least one confusable"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"cross_language_homoglyph_scan took {per_call_ms:.3f}ms/call over "
        f"{len(files)} files — target <{LATENCY_TARGET_MS}ms"
    )
