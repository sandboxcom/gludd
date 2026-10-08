#!/usr/bin/env python3
"""Verify hot-reload JS modules are fresh and valid.

Checks each /tmp/gludd-hot-<plugin>.js against its corresponding
.opencode/plugin/<plugin>.ts source:
  1. If a hot module exists, its mtime must be >= source .ts mtime (STALE otherwise)
  2. If a hot module exists, it must contain valid JS (no bare TS artifacts)

Every proxy plugin (a source containing ``defaultImpl``) must have a generated
module. Missing, stale, syntactically invalid, or unloadable output is a gate
failure; silent fallback would make a successful hot-reload claim false.

Exit 0: all existing hot modules are fresh + valid (or none exist).
Exit 1: at least one existing hot module is stale or broken.

Path overrides (for testing / isolated environments):
  GLUDD_PLUGIN_DIR  — source .ts directory (default: .opencode/plugin)
  GLUDD_HOT_OUT_DIR — hot-module output directory (default: /tmp)
"""

import os
import re
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(
    os.environ.get(
        "GLUDD_PLUGIN_DIR",
        Path(__file__).resolve().parent.parent / ".opencode" / "plugin",
    )
)
OUT_DIR = Path(os.environ.get("GLUDD_HOT_OUT_DIR", "/tmp"))

DEFAULT_PLUGINS = [
    "enforce-deadline",
    "enforce-enhancement-ratio",
    "enforce-floor",
    "enforce-delegate",
    "enforce-make",
    "enforce-multitask",
    "enforce-no-suppressions",
    "enforce-no-wait",
    "enforce-session-start",
    "enforce-stop",
    "enforce-verified-claims",
    "enforce-clean-tree",
    "enforce-deletion-gate",
]

STALE_ARTIFACT_PATTERNS = [
    re.compile(r"\bimport\s*\{[^}]*}\s*from\s*[\"']"),
    re.compile(r"\bexport\s+(default|const|function|interface)\b"),
    re.compile(
        r"\bfunction\s+[A-Za-z_$][\w$]*\s*\([^)]*"
        r"\b[A-Za-z_$][\w$]*\s*:\s*[^,)]+"
    ),
    re.compile(r"ReferenceError\s*(is\s+not|:)"),  # only actual runtime errors, not comments
]


COMMENT_LINE_RE = re.compile(r"(?m)^\s*//.*$")
TRAILING_COMMENT_RE = re.compile(r"(?m)(?<![:\"'])//(?![\"']).*$")
STRING_LITERAL_RE = re.compile(r"(['\"])(?:\\.|(?!\1).)*\1")
REGEX_LITERAL_RE = re.compile(r"/(?:\\.|[^/\\\n])+/[gimsuy]*")
TEMPLATE_LITERAL_RE = re.compile(r"`(?:\\.|[^`\\])*`")


def strip_line_comments(content: str) -> str:
    """Remove JS line comments so inert comment text never trips the
    stale-artifact patterns (CI run 29449765249: 'ReferenceError' inside a
    comment in the generated enforce-stop hot module was flagged as stale).
    Protocol-relative markers like https:// are preserved by the negative
    lookbehind on ':'."""
    without_full_lines = COMMENT_LINE_RE.sub("", content)
    return TRAILING_COMMENT_RE.sub("", without_full_lines)


def is_stale_content(content: str) -> list[str]:
    issues = []
    # Generated modules legitimately contain regex patterns as string values;
    # mask literals before looking for TypeScript syntax so those do not trip
    # the stale-artifact detector.
    scannable = STRING_LITERAL_RE.sub('""', strip_line_comments(content))
    scannable = REGEX_LITERAL_RE.sub("//", scannable)
    scannable = TEMPLATE_LITERAL_RE.sub("``", scannable)
    for pat in STALE_ARTIFACT_PATTERNS:
        if pat.search(scannable):
            issues.append(f"  stale artifact: {pat.pattern}")
    return issues


def has_default_impl(src: Path) -> bool:
    try:
        return "defaultImpl" in src.read_text(encoding="utf-8")
    except OSError:
        return False


def hot_lookup_name(src: Path) -> str | None:
    """Return the sole runtime key passed to loadHotModule by ``src``."""
    try:
        names = set(
            re.findall(
                r"loadHotModule\(\s*[\"']([^\"']+)[\"']",
                src.read_text(encoding="utf-8"),
            )
        )
    except OSError:
        return None
    return next(iter(names)) if len(names) == 1 else None


def hot_module_name(src: Path, fallback: str) -> str:
    """Return the hot-module lookup key: the sole loadHotModule key in *src*,
    else the fallback stem with any 'enforce-' prefix stripped."""
    lookup = hot_lookup_name(src)
    if lookup is not None:
        return lookup
    stem = Path(fallback).stem
    return stem[len("enforce-") :] if stem.startswith("enforce-") else stem


def implementation_source(wrapper: Path) -> Path:
    """Resolve the implementation file a thin proxy re-exports, e.g.
    ``import impl from "./impl/enforce_example_impl.ts"``."""
    match = re.search(
        r"import\s+impl\s+from\s+[\"'](\./[^\"']+)[\"']",
        wrapper.read_text(encoding="utf-8"),
    )
    if match is None:
        raise FileNotFoundError(f"{wrapper}: no relative implementation import found")
    return wrapper.parent / match.group(1)


def find_stale(
    plugin_dir: Path,
    out_dir: Path,
    plugins: list[str] | None = None,
) -> list[str]:
    """Return a list of problem strings for stale or broken hot modules.

    Missing modules are failures for proxy plugins: the build target runs before
    this checker, so absence means generation silently skipped a claimed proxy.
    """
    if plugins is None:
        plugins = sorted(path.stem for path in plugin_dir.glob("enforce-*.ts"))

    problems: list[str] = []

    for name in plugins:
        src = plugin_dir / f"{name}.ts"

        if not src.exists():
            continue

        if not has_default_impl(src):
            continue

        lookup_name = hot_lookup_name(src)
        if lookup_name is None:
            problems.append(f"{name}: expected exactly one loadHotModule lookup key")
            continue
        hot = out_dir / f"gludd-hot-{lookup_name}.js"

        if not hot.exists():
            problems.append(f"{name}: expected hot module is missing")
            continue

        src_mtime = src.stat().st_mtime
        hot_mtime = hot.stat().st_mtime
        if hot_mtime < src_mtime:
            problems.append(f"{name}: hot module stale (source newer by {int(src_mtime - hot_mtime)}s)")
            continue

        content = hot.read_text(encoding="utf-8")
        if len(content) < 50:
            problems.append(f"{name}: hot module too short ({len(content)} bytes)")
            continue

        stale_issues = is_stale_content(content)
        if stale_issues:
            for si in stale_issues:
                problems.append(f"{name}: {si}")
            continue

        try:
            checked = subprocess.run(
                ["node", "--check", str(hot)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            problems.append(f"{name}: JavaScript validation unavailable ({exc})")
            continue
        if checked.returncode != 0:
            detail = next(
                (line.strip() for line in checked.stderr.splitlines() if line.strip()),
                "syntax check failed",
            )
            problems.append(f"{name}: invalid JavaScript ({detail})")
            continue

    return problems


def main(argv: list[str] | None = None) -> int:
    del argv  # no argparse flags; path overrides come from env vars

    plugin_dir = PLUGIN_DIR
    out_dir = OUT_DIR
    plugins = sorted(path.stem for path in plugin_dir.glob("enforce-*.ts"))

    problems = find_stale(plugin_dir, out_dir, plugins)

    expected = 0
    for name in plugins:
        src = plugin_dir / f"{name}.ts"
        if src.exists() and has_default_impl(src):
            expected += 1

    problem_plugins = {problem.split(":", 1)[0] for problem in problems}
    fresh = max(0, expected - len(problem_plugins))

    if problems:
        print("=== HOT-RELOAD FRESHNESS FAILED ===\n")
        for p in problems:
            print(f"  FAIL  {p}")
        print(f"\n{fresh}/{expected} hot modules fresh, {len(problems)} problem(s)")
        print("\nFix: make hot-reload-plugins")
        return 1

    print(f"=== HOT-RELOAD FRESHNESS: {fresh}/{expected} modules fresh + valid ===")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
