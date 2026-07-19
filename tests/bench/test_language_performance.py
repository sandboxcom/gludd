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
from general_ludd.language.i18n_data import (
    extract_icu_placeholders,
    find_untranslated_strings,
    parse_po,
    pseudolocalize,
    serialize_po,
)
from general_ludd.language.phonetic_data import (
    compute_double_metaphone,
    compute_metaphone,
    compute_soundex,
    transcribe_to_arpabet,
    transcribe_to_ipa,
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


def _typical_i18n_source() -> str:
    """~4 KB of Python source mixing gettext-wrapped and hardcoded strings.

    Roughly half the user-facing strings are wrapped in ``_()`` (correct) and
    half are bare literals (the lint target) so ``find_untranslated_strings``
    has real work without being a pathological all-hardcoded input.
    """
    lines: list[str] = [
        "import logging",
        "from gettext import gettext as _",
        "",
        "log = logging.getLogger(__name__)",
        "",
        "def greet(name):",
        "    message = _('Welcome back to the application dashboard')",
        "    log.info('Processed request for user session successfully')",
        "    return message",
        "",
        "class Handler:",
        "    title = 'Manage Account Settings and User Preferences'",
        "    error = _('An unexpected error occurred during processing')",
        "    hint = 'Please contact support for further assistance'",
        "",
        "    def render(self):",
        "        return _('Rendering interface components for display')",
        "",
    ]
    text = "\n".join(lines)
    while len(text) < TYPICAL_TEXT_LEN:
        text = text + "\n" + text
    return text[:TYPICAL_TEXT_LEN]


def _typical_icu_message() -> str:
    """A representative ICU MessageFormat string with mixed placeholder forms."""
    return (
        "{count, plural, =0 {No items found} "
        "=1 {Found one item in {location}} "
        "other {Found # items in {location}}}"
    ) * 4


def _typical_po_content() -> str:
    """A representative gettext .po file body with ~30 entries."""
    lines = [
        'msgid ""',
        'msgstr ""',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        "",
    ]
    for i in range(30):
        lines.append(f"#: src/module_{i}.py:42")
        lines.append(f'msgid "String number {i} for translation catalog"')
        lines.append(f'msgstr "Translated version of string {i}"')
        lines.append("")
    return "\n".join(lines)


def _typical_phonetic_text() -> str:
    """~1 KB of English text for phonetic transcription benchmarks.

    Uses words present in the CMU dict subset plus common out-of-vocab words
    so both the dictionary-hit and fallback paths are exercised.
    """
    in_dict = "hello world data unicode language font phonetic encoding"
    oov = "benchmark latency transcription measurement algorithm"
    sentence = f"{in_dict} {oov} "
    return (sentence * 16)[:1024]


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


# ── e. i18n extraction latency ──────────────────────────────────────────────


def test_i18n_find_untranslated_strings_latency() -> None:
    """``find_untranslated_strings`` lints ~4 KB of mixed gettext/hardcoded source."""
    source = _typical_i18n_source()
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        findings = find_untranslated_strings(source)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert findings, "fixture should contain at least one hardcoded string"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"find_untranslated_strings took {per_call_ms:.3f}ms/call over "
        f"{len(source)} chars — target <{LATENCY_TARGET_MS}ms"
    )


def test_i18n_extract_icu_placeholders_latency() -> None:
    """``extract_icu_placeholders`` over a representative ICU MessageFormat."""
    message = _typical_icu_message()
    iterations = 200

    start = time.perf_counter()
    for _ in range(iterations):
        placeholders = extract_icu_placeholders(message)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert placeholders, "fixture should yield at least one placeholder"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"extract_icu_placeholders took {per_call_ms:.4f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_i18n_parse_po_latency() -> None:
    """``parse_po`` parses a ~30-entry gettext catalog."""
    content = _typical_po_content()
    iterations = 100

    start = time.perf_counter()
    for _ in range(iterations):
        entries = parse_po(content)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert len(entries) == 30, f"expected 30 entries, got {len(entries)}"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"parse_po took {per_call_ms:.3f}ms/call over 30 entries — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_i18n_serialize_po_latency() -> None:
    """``serialize_po`` round-trips a 30-entry catalog back to text."""
    content = _typical_po_content()
    entries = parse_po(content)
    iterations = 100

    start = time.perf_counter()
    for _ in range(iterations):
        text = serialize_po(entries)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert "msgid" in text, "serialized output should contain msgid markers"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"serialize_po took {per_call_ms:.3f}ms/call over 30 entries — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_i18n_pseudolocalize_latency() -> None:
    """``pseudolocalize`` accent substitution on ~4 KB of UI-like text."""
    text = (
        "Welcome to the application dashboard. "
        "Please review your account settings before continuing. "
    ) * 64
    iterations = 100

    start = time.perf_counter()
    for _ in range(iterations):
        result = pseudolocalize(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert result != text, "pseudolocalize should change the text"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"pseudolocalize took {per_call_ms:.3f}ms/call over "
        f"{len(text)} chars — target <{LATENCY_TARGET_MS}ms"
    )


# ── f. Phonetic transcription latency ──────────────────────────────────────


def test_phonetic_transcribe_arpabet_latency() -> None:
    """``transcribe_to_arpabet`` over ~1 KB of mixed dict/OOV English text."""
    text = _typical_phonetic_text()
    iterations = 200

    start = time.perf_counter()
    for _ in range(iterations):
        result = transcribe_to_arpabet(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert result, "transcription should be non-empty"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"transcribe_to_arpabet took {per_call_ms:.4f}ms/call over "
        f"{len(text)} chars — target <{LATENCY_TARGET_MS}ms"
    )


def test_phonetic_transcribe_ipa_latency() -> None:
    """``transcribe_to_ipa`` maps ARPABET→IPA via the CMU dict subset."""
    text = _typical_phonetic_text()
    iterations = 200

    start = time.perf_counter()
    for _ in range(iterations):
        result = transcribe_to_ipa(text)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert result, "transcription should be non-empty"
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"transcribe_to_ipa took {per_call_ms:.4f}ms/call over "
        f"{len(text)} chars — target <{LATENCY_TARGET_MS}ms"
    )


def test_phonetic_soundex_latency() -> None:
    """``compute_soundex`` over a representative word list."""
    words = [
        "Robert", "Rupert", "Ashcraft", "Tymczak", "Pfister",
        "Honeyman", "Davis", "Béliveau", "Smith", "Schmidt",
    ]
    iterations = 500

    start = time.perf_counter()
    for _ in range(iterations):
        for word in words:
            compute_soundex(word)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / (iterations * len(words))) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"compute_soundex took {per_call_ms:.5f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_phonetic_metaphone_latency() -> None:
    """``compute_metaphone`` primary code over a representative word list."""
    words = [
        "Smith", "Schmidt", "Johnson", "Thompson", "O'Connor",
        "Knuth", "Pneumonia", "Gnome", "Write", "Aesthetic",
    ]
    iterations = 500

    start = time.perf_counter()
    for _ in range(iterations):
        for word in words:
            compute_metaphone(word)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / (iterations * len(words))) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"compute_metaphone took {per_call_ms:.5f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_phonetic_double_metaphone_latency() -> None:
    """``compute_double_metaphone`` (primary + alternate) over a word list."""
    words = [
        "Cavier", "Smith", "Schmidt", "Xavier", "Cancer",
        "Richter", "Garcia", "Black", "Smith", "Zhang",
    ]
    iterations = 500

    start = time.perf_counter()
    for _ in range(iterations):
        for word in words:
            compute_double_metaphone(word)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / (iterations * len(words))) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"compute_double_metaphone took {per_call_ms:.5f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


# ── g. Expanded BOM detection latency ───────────────────────────────────────


def test_bom_detection_all_signatures_latency() -> None:
    """BOM sniffing across EVERY BOM signature plus common no-BOM inputs.

    Complements ``test_encoding_sniff_bom_latency`` by exercising the full
    BOM-signature matrix (UTF-7, UTF-8, UTF-16 LE/BE, UTF-32 LE/BE) plus
    ASCII and partial-prefix edge cases in a single timed loop.
    """
    from general_ludd.language.polyglot import _sniff_bom

    matrix = [
        b"\xef\xbb\xbfpayload",
        b"\xff\xfepayload",
        b"\xfe\xffpayload",
        b"\x00\x00\xff\xfepayload",
        b"\xff\xfe\x00\x00payload",
        b"\x2b\x2f\x76payload",
        b"\x2b\x2f\x38payload",
        b"\x2b\x2f\x39payload",
        b"\x2b\x2f\x2bpayload",
        b"plain ascii no bom whatsoever",
        b"",
        b"\xef",  # truncated UTF-8 BOM
        b"\xff",  # truncated UTF-16 BOM
    ]
    iterations = 200

    start = time.perf_counter()
    for _ in range(iterations):
        for head in matrix:
            _sniff_bom(head)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / (iterations * len(matrix))) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"_sniff_bom matrix took {per_call_ms:.5f}ms/call over "
        f"{len(matrix)} variants — target <{LATENCY_TARGET_MS}ms"
    )


def test_bom_detection_real_files_latency(tmp_path: Path) -> None:
    """BOM detection via ``encoding_conflict_report`` over real BOM-bearing files.

    Writes one file per BOM family then measures the end-to-end report cost,
    which includes the BOM sniff plus per-file encoding classification.
    """
    files = [
        (tmp_path / "utf8.py", b"\xef\xbb\xbf# coding: utf-8\n"),
        (tmp_path / "utf16le.py", b"\xff\xfe# coding: utf-16\n"),
        (tmp_path / "utf16be.py", b"\xfe\xff# coding: utf-16\n"),
        (tmp_path / "utf32le.py", b"\x00\x00\xff\xfe# coding\n"),
        (tmp_path / "utf32be.py", b"\xff\xfe\x00\x00# coding\n"),
        (tmp_path / "plain.py", b"print('no bom')\n"),
        (tmp_path / "utf7.py", b"\x2b\x2f\x76print\n"),
    ]
    for path, payload in files:
        path.write_bytes(payload)

    iterations = 50
    start = time.perf_counter()
    for _ in range(iterations):
        report = encoding_conflict_report([p for p, _ in files])
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert len(report["files"]) == len(files)
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"encoding_conflict_report (BOM matrix) took {per_call_ms:.3f}ms/call "
        f"over {len(files)} files — target <{LATENCY_TARGET_MS}ms"
    )


# ── h. Expanded font metrics latency ────────────────────────────────────────


def test_font_metrics_full_pipeline_latency(tmp_path: Path) -> None:
    """Full font-metrics pipeline: format + tables + metrics + axes + kerning.

    Measures the combined cost of running ALL five font-analysis functions on
    a single TTF in sequence — the realistic "analyze this font" hot path.
    """
    font = tmp_path / "full.ttf"
    _write_minimal_ttf(font, num_tables=9)
    iterations = 50

    start = time.perf_counter()
    for _ in range(iterations):
        identify_font_format(font.read_bytes()[:4])
        list_font_tables(str(font))
        get_font_metrics(str(font))
        has_variable_axes(str(font))
        has_kerning(str(font))
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert per_call_ms < LATENCY_TARGET_MS, (
        f"font full-pipeline took {per_call_ms:.3f}ms/call — "
        f"target <{LATENCY_TARGET_MS}ms"
    )


def test_font_metrics_many_fonts_latency(tmp_path: Path) -> None:
    """``get_font_metrics`` across a batch of 25 fonts (typical project scan)."""
    fonts: list[Path] = []
    for i in range(TYPICAL_FILE_COUNT):
        path = tmp_path / f"font_{i}.ttf"
        _write_minimal_ttf(path, num_tables=9)
        fonts.append(path)

    start = time.perf_counter()
    for font in fonts:
        get_font_metrics(str(font))
    elapsed = time.perf_counter() - start

    per_font_ms = (elapsed / len(fonts)) * 1000
    assert per_font_ms < LATENCY_TARGET_MS, (
        f"get_font_metrics took {per_font_ms:.3f}ms/font over "
        f"{len(fonts)} fonts — target <{LATENCY_TARGET_MS}ms"
    )
