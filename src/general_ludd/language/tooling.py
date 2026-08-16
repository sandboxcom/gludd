"""Language-specific tooling integration.

Maps every supported programming language to its canonical lint, test, build,
format, and package-manager tools. Provides PATH-based availability detection
and cross-language toolchain compatibility queries.

This is the "language-specific tooling integration" component of the Language
Expert feature (NF.9).
"""

from __future__ import annotations

import shutil

# Canonical language → tools mapping. Each tool is the de-facto standard or
# the most-widely-adopted option for that language in open-source projects.
# None means "no universal standard / rarely used standalone" (e.g. many
# languages don't have a dedicated build command separate from the
# package manager).

LANGUAGE_TOOLS: dict[str, dict[str, str | None]] = {
    "python": {
        "lint": "ruff",
        "test": "pytest",
        "build": None,
        "format": "ruff",
        "package_manager": "uv",
    },
    "javascript": {
        "lint": "eslint",
        "test": "jest",
        "build": None,
        "format": "prettier",
        "package_manager": "npm",
    },
    "typescript": {
        "lint": "eslint",
        "test": "jest",
        "build": "tsc",
        "format": "prettier",
        "package_manager": "npm",
    },
    "go": {
        "lint": "golangci-lint",
        "test": "go test",
        "build": "go build",
        "format": "gofmt",
        "package_manager": "go mod",
    },
    "rust": {
        "lint": "clippy",
        "test": "cargo test",
        "build": "cargo build",
        "format": "rustfmt",
        "package_manager": "cargo",
    },
    "java": {
        "lint": "checkstyle",
        "test": "mvn test",
        "build": "mvn package",
        "format": "google-java-format",
        "package_manager": "maven",
    },
    "kotlin": {
        "lint": "ktlint",
        "test": "gradle test",
        "build": "gradle build",
        "format": "ktlint",
        "package_manager": "gradle",
    },
    "ruby": {
        "lint": "rubocop",
        "test": "rspec",
        "build": None,
        "format": "rubocop",
        "package_manager": "bundler",
    },
    "php": {
        "lint": "phpcs",
        "test": "phpunit",
        "build": None,
        "format": "phpcbf",
        "package_manager": "composer",
    },
    "c": {
        "lint": "cppcheck",
        "test": None,
        "build": "make",
        "format": "clang-format",
        "package_manager": None,
    },
    "cpp": {
        "lint": "cppcheck",
        "test": None,
        "build": "cmake",
        "format": "clang-format",
        "package_manager": None,
    },
    "csharp": {
        "lint": "dotnet format",
        "test": "dotnet test",
        "build": "dotnet build",
        "format": "dotnet format",
        "package_manager": "dotnet",
    },
    "swift": {
        "lint": "swiftlint",
        "test": "swift test",
        "build": "swift build",
        "format": "swift-format",
        "package_manager": "swift pm",
    },
    "scala": {
        "lint": "scalafix",
        "test": "sbt test",
        "build": "sbt assembly",
        "format": "scalafmt",
        "package_manager": "sbt",
    },
    "elixir": {
        "lint": "credo",
        "test": "mix test",
        "build": "mix compile",
        "format": "mix format",
        "package_manager": "mix",
    },
    "haskell": {
        "lint": "hlint",
        "test": "cabal test",
        "build": "cabal build",
        "format": "fourmolu",
        "package_manager": "cabal",
    },
    "dart": {
        "lint": "dart analyze",
        "test": "dart test",
        "build": "dart compile",
        "format": "dart format",
        "package_manager": "dart pub",
    },
    "lua": {
        "lint": "luacheck",
        "test": "busted",
        "build": None,
        "format": "stylua",
        "package_manager": "luarocks",
    },
    "julia": {
        "lint": None,
        "test": "julia --project=. -e 'using Test'",
        "build": None,
        "format": "JuliaFormatter",
        "package_manager": "Pkg",
    },
    "zig": {
        "lint": None,
        "test": "zig test",
        "build": "zig build",
        "format": "zig fmt",
        "package_manager": "zig build",
    },
    "r": {
        "lint": "lintr",
        "test": "testthat",
        "build": None,
        "format": "styler",
        "package_manager": "renv",
    },
    "sql": {
        "lint": "sqlfluff",
        "test": None,
        "build": None,
        "format": "sqlfluff",
        "package_manager": None,
    },
}

# Language groups that share toolchains. Used by get_cross_language_compat
# to report which other languages can use the same tools as the query
# language.
_TOOLCHAIN_GROUPS: dict[str, frozenset[str]] = {
    "llvm": frozenset({"c", "cpp", "objective-c", "swift"}),
    "jvm": frozenset({"java", "kotlin", "scala", "clojure"}),
    "dotnet": frozenset({"csharp", "fsharp", "vb"}),
    "node": frozenset({"javascript", "typescript"}),
    "erlang_beam": frozenset({"elixir", "erlang"}),
}

# Derive group index: language → set of compatible languages (excluding self).
_COMPAT: dict[str, list[str]] = {}
for _group_name, _members in _TOOLCHAIN_GROUPS.items():
    for _lang in _members:
        _COMPAT.setdefault(_lang, []).extend(sorted(m for m in _members if m != _lang))


def get_language_tools(language: str) -> dict[str, str | None] | None:
    """Return the tool record for *language* (case-insensitive)."""
    lowered = language.lower()
    return LANGUAGE_TOOLS.get(lowered)


def all_supported_languages() -> frozenset[str]:
    """Return the set of languages with tooling entries."""
    return frozenset(LANGUAGE_TOOLS.keys())


def detect_available_tools(
    languages: list[str] | None = None,
) -> dict[str, dict[str, str | None]]:
    """Check PATH for each tool and return which are actually installed.

    Parameters
    ----------
    languages:
        If given, only probe these languages. When None, probe all.

    Returns:
        dict
        Language → {category: tool_name | None}.  A tool_name of None
        means "no standard tool for this category"; a tool that is on PATH
        keeps its name; a tool that is NOT on PATH also keeps its name
        (we report the canonical name regardless — callers filter via
        :func:`shutil.which` themselves if they need runtime yes/no).
    """
    if languages is None:
        languages = sorted(LANGUAGE_TOOLS.keys())
    result: dict[str, dict[str, str | None]] = {}
    for lang in languages:
        tools = get_language_tools(lang)
        if tools is None:
            # Unknown language: keep the key so callers see an entry for
            # every requested language, with each tool category unresolved.
            result[lang] = {cat: None for cat in ("lint", "test", "build", "format", "package_manager")}
            continue
        resolved: dict[str, str | None] = {}
        for cat, tool in tools.items():
            if tool is None or shutil.which(tool.split()[0]) is not None:
                resolved[cat] = tool
            else:
                resolved[cat] = None
        result[lang] = resolved
    return result


def get_cross_language_compat(language: str) -> list[str]:
    """Return languages sharing toolchain compatibility with *language*."""
    lowered = language.lower()
    return list(_COMPAT.get(lowered, []))


__all__ = [
    "LANGUAGE_TOOLS",
    "all_supported_languages",
    "detect_available_tools",
    "get_cross_language_compat",
    "get_language_tools",
]
