"""Polyglot language support: multi-language detection + cross-language analysis.

Phase E of the Language Expert. Provides three capabilities for repositories
that mix programming languages:

- :func:`detect_languages_in_directory` — walk a tree and identify the
  programming languages present via file-extension matching plus ecosystem
  marker files (``pyproject.toml``, ``package.json``, ``go.mod`` ...).
- :func:`cross_language_homoglyph_scan` — scan a list of source files of
  mixed languages for confusable (homoglyph) characters that cross script
  boundaries (e.g. a Cyrillic U+0430 substituting for Latin ``a`` in a Python
  identifier). Reuses the skeleton table from
  :mod:`general_ludd.language.homoglyph_data`.
- :func:`encoding_conflict_report` — sniff each file's BOM/encoding and
  report mismatches that break tooling (UTF-8 vs UTF-16, mixed BOMs,
  inconsistent BOM presence within the same encoding family).

The module is dependency-light on purpose: it walks the filesystem, reads
at most a small prefix of each file, and leverages the existing
``charset_map.BOM_SIGNATURES`` and ``homoglyph_data.detect_confusables``
rather than re-implementing them.
"""

from __future__ import annotations

import re as _re
from pathlib import Path
from typing import TypedDict, cast

from general_ludd.language.charset_map import BOM_BY_SEQUENCE
from general_ludd.language.homoglyph_data import (
    detect_confusables,
)

# ── Extension → language table ─────────────────────────────────────────────
# Single source of truth for "what language is this file?". Order does not
# matter; the longest extension wins (so ``.tsx`` is typescript, not the
# ``.tsx``-is-xml corner case). Marker files below handle the rest.

_EXTENSION_MAP: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".cs": "csharp",
    ".fs": "fsharp",
    ".swift": "swift",
    ".m": "objective-c",
    ".mm": "objective-cpp",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".ps1": "powershell",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".r": "r",
    ".R": "r",
    ".sql": "sql",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".vue": "vue",
    ".svelte": "svelte",
    ".dart": "dart",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".edn": "clojure",
    ".hs": "haskell",
    ".elm": "elm",
    ".jl": "julia",
    ".nim": "nim",
    ".v": "verilog",
    ".sv": "systemverilog",
    ".zig": "zig",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".f90": "fortran",
    ".f95": "fortran",
    ".f03": "fortran",
    ".asm": "assembly",
    ".s": "assembly",
}

# Ecosystem marker files (project-runner/detect.py is the authoritative source
# for the python/node/go/rust/make set; we mirror plus add a few more for
# cross-language coverage). Map marker filename → language/ecosystem label.
_MARKER_MAP: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "poetry.lock": "python",
    "package.json": "node",
    "package-lock.json": "node",
    "yarn.lock": "node",
    "pnpm-lock.yaml": "node",
    "tsconfig.json": "typescript",
    "go.mod": "go",
    "go.sum": "go",
    "Cargo.toml": "rust",
    "Cargo.lock": "rust",
    "Gemfile": "ruby",
    "Gemfile.lock": "ruby",
    "composer.json": "php",
    "pom.xml": "java",
    "build.gradle": "java",
    "build.gradle.kts": "kotlin",
    "build.sbt": "scala",
    "Mix.exs": "elixir",
    "rebar.config": "erlang",
    "project.clj": "clojure",
    "Makefile": "make",
    "CMakeLists.txt": "cmake",
    "Dockerfile": "docker",
}

# Files we skip during the directory walk. ``.git`` is universal; the rest
# are common vendored/build directories whose inclusion would inflate counts
# and mis-attribute languages (e.g. ``node_modules`` has its own .js files).
_SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        "target",
        ".tox",
        ".eggs",
    }
)

# How many bytes of a file to read for homoglyph scanning. Source files are
# typically small; capping at 1 MiB keeps the scan cheap on huge files and
# avoids loading binaries wholesale.
_HOMOGLYPH_SCAN_LIMIT = 1 * 1024 * 1024

# Severity thresholds for cumulative confusable counts in a single file.
# A single homoglyph is suspicious but could be a legitimate transliteration;
# 3+ in one file is almost certainly an attack vector.
_SEVERITY_LOW_MAX = 1
_SEVERITY_MEDIUM_MAX = 4


# ── Typed result shapes ────────────────────────────────────────────────────


class FileEncoding(TypedDict):
    """Per-file encoding info, as surfaced by :func:`encoding_conflict_report`."""

    file: str
    bom: str | None
    encoding: str
    has_bom: bool


# ── Helpers ────────────────────────────────────────────────────────────────


def _language_for_extension(suffix: str) -> str | None:
    """Return the language label for ``suffix`` (case-sensitive where it matters).

    ``.R`` is R, ``.r`` is also R; ``.v`` is Verilog. We keep the table
    case-sensitive to avoid classifying ``.PY`` as a new language distinct
    from ``.py``, but lowercase the input first and re-check for the common
    all-caps Windows variants.
    """
    if suffix in _EXTENSION_MAP:
        return _EXTENSION_MAP[suffix]
    lowered = suffix.lower()
    if lowered != suffix and lowered in _EXTENSION_MAP:
        return _EXTENSION_MAP[lowered]
    return None


def _sniff_bom(head: bytes) -> tuple[str | None, int]:
    """Return (BOM name, byte length consumed) for the byte prefix ``head``.

    ``head`` should be the first ≥4 bytes of the file. We check the longest
    BOMs first so UTF-32-LE (which starts with the UTF-16-LE prefix
    ``\\xff\\xfe``) is matched correctly rather than as UTF-16.
    """
    # Check 4-byte BOMs first to avoid false-matching the UTF-16 prefix.
    for length in (4, 3, 2):
        prefix = head[:length]
        if len(prefix) < length:
            continue
        name = BOM_BY_SEQUENCE.get(prefix)
        if name is not None:
            return name, length
    return None, 0


def _detect_encoding(path: Path) -> FileEncoding:
    """Read enough of ``path`` to determine BOM/encoding and return the row.

    Falls back to UTF-8 when no BOM is present (the modern repo default).
    Read errors are surfaced as a UTF-8 row with ``has_bom=False`` so the
    caller still gets a per-file entry; we don't drop files from the report.
    """
    try:
        with path.open("rb") as fh:
            head = fh.read(4)
    except OSError:
        return {
            "file": str(path),
            "bom": None,
            "encoding": "UTF-8",
            "has_bom": False,
        }

    bom_name, _consumed = _sniff_bom(head)
    encoding = bom_name if bom_name is not None else "UTF-8"
    return {
        "file": str(path),
        "bom": bom_name,
        "encoding": encoding,
        "has_bom": bom_name is not None,
    }


def _severity_for(count: int) -> str:
    """Map a confusable count to a severity bucket."""
    if count <= _SEVERITY_LOW_MAX:
        return "low"
    if count <= _SEVERITY_MEDIUM_MAX:
        return "medium"
    return "high"


# ── Public API ─────────────────────────────────────────────────────────────


def detect_languages_in_directory(path: str | Path) -> dict[str, object]:
    """Walk ``path`` and identify the programming languages present.

    Counts files per language via extension lookup (:data:`_EXTENSION_MAP`)
    and records ecosystem marker files (:data:`_MARKER_MAP`). Common
    vendored/build directories (``.git``, ``node_modules``, ``target`` ...)
    are skipped to keep counts honest.

    Returns a :class:`PolyglotReport` with ``languages`` sorted by file
    count descending (ties broken alphabetically by language name for
    deterministic output). A non-existent or non-directory ``path`` yields
    an empty report rather than raising.
    """
    root = Path(path)
    if not root.is_dir():
        return {
            "path": str(path),
            "languages": [],
            "total_files": 0,
            "marker_files": {},
        }

    counts: dict[str, int] = {}
    extensions_seen: dict[str, set[str]] = {}
    marker_hits: dict[str, str] = {}

    total_files = 0
    for entry in root.rglob("*"):
        if any(part in _SKIP_DIRS for part in entry.relative_to(root).parts[:-1]):
            continue
        if entry.is_symlink() or not entry.is_file():
            continue

        name = entry.name
        if name in _MARKER_MAP:
            marker_hits[name] = _MARKER_MAP[name]
            total_files += 1
            continue

        suffix = entry.suffix
        if not suffix:
            total_files += 1
            continue
        language = _language_for_extension(suffix)
        total_files += 1
        if language is None:
            continue
        counts[language] = counts.get(language, 0) + 1
        extensions_seen.setdefault(language, set()).add(suffix)

    languages = []
    for language, count in counts.items():
        languages.append(
            {
                "language": language,
                "file_count": count,
                "extensions": sorted(extensions_seen.get(language, set())),
                "marker_files": sorted(m for m, lang in marker_hits.items() if lang == language),
            }
        )
    languages.sort(key=lambda p: (-cast(int, p["file_count"]), p["language"]))

    return {
        "path": str(root),
        "languages": languages,
        "total_files": total_files,
        "marker_files": marker_hits,
    }


def cross_language_homoglyph_scan(
    files: list[str | Path],
) -> list[dict[str, object]]:
    """Scan ``files`` for confusable (homoglyph) characters per file.

    Returns one :class:`CrossLanguageFinding` per file that contains at
    least one confusable character. The ``language`` field is derived from
    the file's extension via :data:`_EXTENSION_MAP`; when unknown it is
    reported as ``"unknown"``. Each finding carries the full per-character
    confusable list from :func:`homoglyph_data.detect_confusables`.

    Non-existent files are silently skipped (they contribute no finding);
    files that cannot be decoded as UTF-8 are also skipped (homoglyphs are
    a Unicode phenomenon — a file we can't decode is an encoding problem,
    not a homoglyph problem, and is better surfaced by
    :func:`encoding_conflict_report`).
    """
    findings: list[dict[str, object]] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="strict") as fh:
                text = fh.read(_HOMOGLYPH_SCAN_LIMIT)
        except (OSError, UnicodeDecodeError):
            continue

        confusables = detect_confusables(text)
        if not confusables:
            continue

        language = _language_for_extension(path.suffix) or "unknown"
        findings.append(
            {
                "file": str(path),
                "language": language,
                "confusables": confusables,
                "severity": _severity_for(len(confusables)),
            }
        )
    return findings


def encoding_conflict_report(
    files: list[str | Path],
) -> dict[str, object]:
    """Identify encoding mismatches across ``files``.

    Sniffs each file's BOM via :data:`charset_map.BOM_SIGNATURES` and
    reports:

    - whether files are split across multiple encodings (UTF-8 vs UTF-16
      etc.) — ``is_consistent`` is ``False`` when >1 encoding is present.
    - whether BOM presence is inconsistent within a single encoding family
      (some UTF-8 files with BOM, others without) — surfaced as a conflict
      string but does NOT alone flip ``is_consistent`` (mixed-BOM-within-
      UTF-8 is a style issue, not an interop blocker).

    Non-existent files are skipped.
    """
    rows: list[FileEncoding] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            continue
        rows.append(_detect_encoding(path))

    encodings_present: list[str] = []
    for row in rows:
        if row["encoding"] not in encodings_present:
            encodings_present.append(row["encoding"])

    boms_present: list[str] = []
    for row in rows:
        if row["has_bom"] and row["bom"] not in boms_present:
            assert row["bom"] is not None  # for the type checker
            boms_present.append(row["bom"])

    conflicts: list[str] = []
    if len(encodings_present) > 1:
        conflicts.append("Multiple encodings present: " + ", ".join(encodings_present))

    # Mixed BOM presence within UTF-8 specifically is a common repo smell.
    utf8_rows = [r for r in rows if r["encoding"] == "UTF-8"]
    if utf8_rows:
        bom_set = {r["has_bom"] for r in utf8_rows}
        if len(bom_set) > 1:
            with_bom = sum(1 for r in utf8_rows if r["has_bom"])
            without_bom = len(utf8_rows) - with_bom
            conflicts.append(f"Inconsistent UTF-8 BOM: {with_bom} file(s) with BOM, {without_bom} without")

    # Multiple distinct BOMs always indicate a real conflict.
    if len(boms_present) > 1:
        conflicts.append("Mixed BOM types: " + ", ".join(boms_present))

    is_consistent = len(encodings_present) <= 1 and not conflicts

    return {
        "files": rows,
        "encodings_present": encodings_present,
        "boms_present": boms_present,
        "conflicts": conflicts,
        "is_consistent": is_consistent,
    }


# ── Shebang → language mapping ──────────────────────────────────────────────


_SHEBANG_MAP: dict[str, str] = {
    "python": "python",
    "python3": "python",
    "python2": "python",
    "node": "javascript",
    "nodejs": "javascript",
    "bash": "shell",
    "sh": "shell",
    "zsh": "shell",
    "ruby": "ruby",
    "perl": "perl",
    "php": "php",
    "lua": "lua",
    "julia": "julia",
}

# Comment syntax per language. Used by analyze_code_density to classify
# lines as comment vs code vs blank. Languages in the same group share
# syntax (e.g. hash-comment languages: python, ruby, perl, shell).
_COMMENT_STYLES: dict[str, str | list[str]] = {
    "python": "#",
    "ruby": "#",
    "shell": "#",
    "perl": "#",
    "r": "#",
    "php": "#",
    "elixir": "#",
    "haskell": "--",
    "lua": "--",
    "sql": "--",
    "javascript": ["//", "/*"],
    "typescript": ["//", "/*"],
    "go": "//",
    "rust": "//",
    "c": "//",
    "cpp": "//",
    "csharp": "//",
    "java": "//",
    "kotlin": "//",
    "scala": "//",
    "swift": "//",
    "dart": "//",
    "zig": "//",
    "julia": "#",
}


def _shebang_language(first_line: str) -> str | None:
    if not first_line.startswith("#!"):
        return None
    path = first_line[2:].strip().rpartition("/")[2]
    for token in path.split():
        token = token.strip()
        if token in _SHEBANG_MAP:
            return _SHEBANG_MAP[token]
    return None


def _is_blank(line: str) -> bool:
    return line.strip() == ""


def _is_comment(line: str, style: str | list[str] | None) -> bool:
    if style is None:
        return False
    stripped = line.strip()
    if isinstance(style, str):
        return stripped.startswith(style)
    return any(stripped.startswith(s) for s in style)


# ── classify_files_by_structure ─────────────────────────────────────────────


def classify_files_by_structure(
    files: list[str | Path],
) -> list[dict[str, object]]:
    """Classify files by content-based language markers beyond extension.

    For each file, checks the shebang line, magic comments, and key
    syntax patterns to determine the language. This is useful when a
    file lacks a standard extension (e.g. an executable script with
    no ``.py`` suffix) or has an ambiguous one.

    Returns a list of :class:`FileClassification` entries, one per
    file successfully classified. Files that cannot be read are
    silently skipped; files with no detectable markers are included
    with ``detected_language=None`` and an empty ``markers`` list.
    """
    results: list[dict[str, object]] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8", errors="strict") as fh:
                text = fh.read(4096)
        except (OSError, UnicodeDecodeError):
            continue

        extension_lang = _language_for_extension(path.suffix)
        markers: list[str] = []
        detected: str | None = None

        first_line = text.split("\n", 1)[0] if text else ""

        shebang_lang = _shebang_language(first_line)
        if shebang_lang is not None:
            markers.append("shebang")
            detected = shebang_lang

        # Go package declaration is a strong signal.
        if _re.match(r"^package\s+\w+", text, _re.MULTILINE):
            markers.append("package_declaration")
            if detected is None:
                detected = "go"

        if text.lstrip().startswith("defmodule ") or "use GenServer" in text:
            markers.append("elixir_module")
            if detected is None:
                detected = "elixir"

        if detected is None:
            detected = extension_lang

        results.append(
            {
                "file": str(path),
                "detected_language": detected,
                "language_from_extension": extension_lang,
                "markers": markers,
                "extension_match": extension_lang == detected if extension_lang is not None else False,
            }
        )
    return results


# ── analyze_code_density ────────────────────────────────────────────────────


def analyze_code_density(
    files: list[str | Path],
) -> list[dict[str, object]]:
    """Compute comment-to-code ratios per file.

    For each file, determines the language (via extension), applies
    that language's comment syntax, and reports total / code / comment
    / blank line counts. Binary files and files that cannot be decoded
    as UTF-8 are skipped.

    Returns a list of :class:`CodeDensityReport` entries.
    """
    reports: list[dict[str, object]] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            continue
        language = _language_for_extension(path.suffix) or "unknown"
        try:
            with path.open("r", encoding="utf-8", errors="strict") as fh:
                lines = fh.readlines()
        except (OSError, UnicodeDecodeError):
            continue

        comment_style = _COMMENT_STYLES.get(language)
        total = len(lines)
        blank = 0
        comment = 0
        code = 0

        in_block: bool = False
        for line in lines:
            if _is_blank(line):
                blank += 1
                in_block = False
            elif comment_style and "/*" in str(comment_style):
                if in_block:
                    comment += 1
                    if "*/" in line:
                        in_block = False
                elif line.strip().startswith("/*"):
                    comment += 1
                    if "*/" not in line:
                        in_block = True
                elif _is_comment(line, comment_style):
                    comment += 1
                else:
                    code += 1
            elif _is_comment(line, comment_style):
                comment += 1
            else:
                code += 1

        reports.append(
            {
                "file": str(path),
                "language": language,
                "total_lines": total,
                "comment_lines": comment,
                "code_lines": code,
                "blank_lines": blank,
            }
        )
    return reports


# ── detect_language_markers ─────────────────────────────────────────────────


def detect_language_markers(text: str) -> dict[str, str]:
    """Detect content-based language signatures in *text*.

    Scans the first few lines of ``text`` for well-known markers like
    shebangs, encoding cookies, doctype declarations, and XML
    declarations. Returns a dict of ``{marker_name: value}`` with
    only the markers that were found.

    This is a pure-text function — no filesystem access needed.
    """
    markers: dict[str, str] = {}
    if not text:
        return markers

    first_line = text.split("\n", 1)[0]

    shebang_lang = _shebang_language(first_line)
    if shebang_lang is not None:
        markers["shebang"] = shebang_lang

    m = _re.search(r"coding[:=]\s*([-\w.]+)", text[:200])
    if m:
        markers["encoding_cookie"] = m.group(1)

    if _re.match(r"^package\s+(\w+)", first_line):
        markers["package_declaration"] = "go"

    if text.lstrip().startswith("<!DOCTYPE html"):
        markers["doctype"] = "html"

    if text.lstrip().startswith("<?xml"):
        markers["xml_declaration"] = "xml"

    return markers


__all__ = [
    "FileEncoding",
    "analyze_code_density",
    "classify_files_by_structure",
    "cross_language_homoglyph_scan",
    "detect_language_markers",
    "detect_languages_in_directory",
    "encoding_conflict_report",
]
