"""Cross-language pattern detection.

Detects patterns where code in one programming language interacts with
code in another — subprocess invocations, FFI usage, polyglot build
systems, and cross-language imports.

This is the "cross-language patterns" component of the Language Expert
feature (NF.9).
"""

from __future__ import annotations

import re
from pathlib import Path

from general_ludd.language.polyglot import _language_for_extension

# ── Pattern tables ──────────────────────────────────────────────────────────

# Cross-language invocation signatures. Each pattern captures the
# language of the source file and the typical subprocess/exec commands
# that call into another language's runtime.
_SUBPROCESS_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "python": [
        re.compile(r"\bsubprocess\.(?:run|call|check_call|Popen)\b"),
        re.compile(r"\bos\.(?:system|popen)\b"),
        re.compile(r"\bexec\(|execfile\(|eval\("),
    ],
    "javascript": [
        re.compile(r"\bchild_process\b"),
        re.compile(r"\bexecSync\b|\bexec\s*\("),
    ],
    "ruby": [
        re.compile(r"\bsystem\s*\(|`.*`|\bexec\s*\("),
    ],
    "shell": [
        re.compile(r"\b(python|node|ruby|perl|php|go|rustc|cargo)\s"),
    ],
    "go": [
        re.compile(r"\bexec\.(?:Command|CommandContext)\b"),
        re.compile(r"\bos\.(?:Execute|Pipe)\b"),
    ],
}

# FFI-specific patterns. These are more targeted than subprocess:
# they indicate a deliberate foreign-function interface rather than a
# loose script invocation.
_FFI_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("cffi", re.compile(r"\bffibuilder\b|\bfrom cffi import\b")),
    ("ctypes", re.compile(r"\bctypes\.(?:CDLL|c_int|c_char_p|windll)\b")),
    ("extern_block", re.compile(r'\bextern\s+(?:"C"|"system"|"Rust")?\s*\{')),
    ("swig", re.compile(r"\%module\s+\w+")),
    ("pybind11", re.compile(r"\bpybind11\b|\bPYBIND11_MODULE\b")),
    ("napi", re.compile(r"\bnapi_\w+\b|\bNAPI_MODULE\b")),
    ("jni", re.compile(r"\bJNIEXPORT\b|\bJNICALL\b|\bSystem\.loadLibrary\b")),
    ("cgo", re.compile(r'^import\s+"C"', re.MULTILINE)),
]

# Build system markers — files that signal a project using a particular
# build tool / ecosystem.
_BUILD_MARKERS: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "requirements.txt": "python",
    "package.json": "node",
    "tsconfig.json": "typescript",
    "go.mod": "go",
    "Cargo.toml": "rust",
    "Gemfile": "ruby",
    "composer.json": "php",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "build.gradle.kts": "gradle",
    "build.sbt": "sbt",
    "Makefile": "make",
    "CMakeLists.txt": "cmake",
    "mix.exs": "elixir",
    "rebar.config": "erlang",
    "project.clj": "clojure",
    "dune-project": "ocaml",
    "meson.build": "meson",
    "BUILD": "bazel",
    "BUILD.bazel": "bazel",
    "WORKSPACE": "bazel",
}

# Common runtime executable names detected in subprocess calls.
_RUNTIME_NAMES: dict[str, str] = {
    "node": "javascript",
    "python": "python",
    "python3": "python",
    "ruby": "ruby",
    "php": "php",
    "perl": "perl",
    "go": "go",
    "lua": "lua",
    "julia": "julia",
    "Rscript": "r",
}

_READ_LIMIT = 256 * 1024  # 256 KiB per file


# ── Public API ──────────────────────────────────────────────────────────────


def detect_cross_language_imports(
    files: list[str | Path],
) -> list[dict[str, object]]:
    """Detect cross-language dependencies in *files*.

    Scans each file for patterns that indicate it calls or depends on
    code written in another language (subprocess invocations, FFI
    bindings, embedded DSLs).

    Returns
    -------
    list[dict]
        Each finding has ``file``, ``source_language``, ``patterns``
        (list of matched pattern names), and ``target_languages``
        (inferred target languages when known).
    """
    findings: list[dict[str, object]] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            continue
        language = _language_for_extension(path.suffix) or "unknown"
        try:
            with path.open("r", encoding="utf-8", errors="strict") as fh:
                text = fh.read(_READ_LIMIT)
        except (OSError, UnicodeDecodeError):
            continue

        matched: list[str] = []
        targets: set[str] = set()
        lang_patterns = _SUBPROCESS_PATTERNS.get(language, [])
        for pat in lang_patterns:
            if pat.search(text):
                matched.append(pat.pattern)
        for pattern_name, pat in _FFI_PATTERNS:
            if pat.search(text):
                matched.append(pattern_name)

        if not matched:
            continue

        findings.append(
            {
                "file": str(path),
                "source_language": language,
                "patterns": matched,
                "target_languages": sorted(targets),
            }
        )
    return findings


def detect_ffi_patterns(
    files: list[str | Path],
) -> list[dict[str, object]]:
    """Detect Foreign Function Interface usage in *files*.

    Returns one finding per file that contains an FFI pattern
    (ctypes, cffi, pybind11, extern blocks, JNI, napi, cgo, SWIG).

    Each finding includes the ``ffi_type``, ``file``, and
    ``source_language``.
    """
    findings: list[dict[str, object]] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            continue
        language = _language_for_extension(path.suffix) or "unknown"
        try:
            with path.open("r", encoding="utf-8", errors="strict") as fh:
                text = fh.read(_READ_LIMIT)
        except (OSError, UnicodeDecodeError):
            continue

        for ffi_type, pat in _FFI_PATTERNS:
            if pat.search(text):
                findings.append(
                    {
                        "file": str(path),
                        "source_language": language,
                        "ffi_type": ffi_type,
                    }
                )
                break  # one ffi_type per file
    return findings


def detect_polyglot_builds(path: str | Path) -> list[dict[str, object]]:
    """Detect build systems present in a directory.

    Scans the root of *path* for build-system marker files (e.g.
    ``pyproject.toml``, ``package.json``, ``go.mod``) and returns
    one entry per detected system.
    """
    root = Path(path)
    if not root.is_dir():
        return []

    result: list[dict[str, object]] = []
    for marker, system in _BUILD_MARKERS.items():
        candidate = root / marker
        if candidate.is_file():
            result.append(
                {
                    "file": str(candidate),
                    "build_system": system,
                }
            )
    return result


def detect_script_invocations(
    files: list[str | Path],
) -> list[dict[str, object]]:
    """Detect one scripting language invoking another via subprocess.

    Scans each file for known runtime executable names (python, node,
    ruby, etc.) in subprocess-like contexts and reports each source→
    target pair.
    """
    findings: list[dict[str, object]] = []
    for raw in files:
        path = Path(raw)
        if not path.is_file():
            continue
        source_lang = _language_for_extension(path.suffix) or "unknown"
        try:
            with path.open("r", encoding="utf-8", errors="strict") as fh:
                text = fh.read(_READ_LIMIT)
        except (OSError, UnicodeDecodeError):
            continue

        targets: set[str] = set()
        for runtime, lang in _RUNTIME_NAMES.items():
            if re.search(rf"\b{re.escape(runtime)}\b", text) and lang != source_lang:
                targets.add(lang)

        if targets:
            findings.append(
                {
                    "file": str(path),
                    "source_language": source_lang,
                    "target_languages": sorted(targets),
                }
            )
    return findings


__all__ = [
    "detect_cross_language_imports",
    "detect_ffi_patterns",
    "detect_polyglot_builds",
    "detect_script_invocations",
]
