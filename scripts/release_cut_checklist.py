#!/usr/bin/env python3
"""Pre-flight checklist for `make release-cut`.  Verifies every pre-release
condition so the operator knows whether a cut is safe before pushing a tag.

Exit codes:
  0 — all checks pass, release-cut is safe to run
  1 — one or more blockers found
  2 — evidence collection error (could not run a check)
"""

from __future__ import annotations

import argparse
import io
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Check:
    name: str
    passed: bool = False
    detail: str = ""


@dataclass
class Checklist:
    tag: str
    checks: list[Check] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks) and not self.errors

    @property
    def blockers(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed] + self.errors


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True, timeout=120)


def _run_make(target: str) -> subprocess.CompletedProcess[str]:
    return _run(["make", "-s", target])


def check_ci_green(cl: Checklist) -> None:
    """CI must be green for HEAD on the current branch."""
    c = Check(name="CI green")
    try:
        result = _run(["python3", "scripts/require_ci_green.py"])
        if result.returncode == 0:
            c.passed = True
            c.detail = "CI green for HEAD"
        elif result.returncode == 2:
            c.detail = "CI is PENDING — wait for it to complete"
        else:
            c.detail = f"CI is RED or no matching run found: {result.stdout.strip() or result.stderr.strip()}"
    except Exception as exc:
        cl.errors.append(f"CI check failed: {exc}")
    cl.checks.append(c)


def check_lint(cl: Checklist) -> None:
    """Ruff lint must report 0 errors."""
    c = Check(name="Lint (ruff)")
    try:
        result = _run_make("lint")
        # ruff exits 0 on clean, non-zero on findings
        if result.returncode == 0:
            c.passed = True
            c.detail = "0 lint errors"
        else:
            c.detail = f"lint errors found (exit {result.returncode})"
    except Exception as exc:
        cl.errors.append(f"Lint check failed: {exc}")
    cl.checks.append(c)


def check_typecheck(cl: Checklist) -> None:
    """Mypy must report 0 errors."""
    c = Check(name="Typecheck (mypy)")
    try:
        result = _run_make("typecheck")
        if result.returncode == 0:
            c.passed = True
            c.detail = "0 type errors"
        else:
            c.detail = "type errors found"
    except Exception as exc:
        cl.errors.append(f"Typecheck failed: {exc}")
    cl.checks.append(c)


def check_gate(cl: Checklist) -> None:
    """The local gate-status file should record a green gate (no FAIL lines)."""
    c = Check(name="Gate green")
    gate_file = ROOT / ".gate-status"
    try:
        if gate_file.exists():
            content = gate_file.read_text().strip()
            failures = [line.strip() for line in content.splitlines() if "FAIL" in line]
            if not failures:
                c.passed = True
                c.detail = ".gate-status: all phases PASS"
            else:
                c.detail = f"gate failures: {'; '.join(failures[:3])}"
        else:
            c.detail = ".gate-status file missing — run `make gate-background` first"
    except Exception as exc:
        cl.errors.append(f"Gate check failed: {exc}")
    cl.checks.append(c)


def check_readme_current(cl: Checklist) -> None:
    """README Status-as-of must match the target tag."""
    c = Check(name="README current")
    tag = cl.tag
    try:
        from check_readme_status_current import (
            _parse_args,
            _read_status_line,
            _read_toml_version,
        )

        args = _parse_args([tag])
        try:
            status_line = _read_status_line(ROOT / "README.md")
        except SystemExit:
            c.detail = f"README.md Status-as-of line not found or does not match {tag}"
            cl.checks.append(c)
            return
        toml_ver = _read_toml_version(ROOT / "pyproject.toml")
        tag_ver = tag.lstrip("v")
        if tag_ver == toml_ver and toml_ver == status_line:
            c.passed = True
            c.detail = f"README Status-as-of matches {tag}"
        else:
            c.detail = f"version mismatch: tag={tag_ver}, pyproject={toml_ver}, README={status_line}"
    except ImportError:
        result = _run_make(f"check-readme-status TAG={tag}")
        if result.returncode == 0:
            c.passed = True
            c.detail = f"README Status-as-of matches {tag}"
        else:
            c.detail = result.stdout.strip() or result.stderr.strip() or "README status check failed"
    except Exception as exc:
        cl.errors.append(f"README check failed: {exc}")
    cl.checks.append(c)


def check_changelog_updated(cl: Checklist) -> None:
    """CHANGELOG.md must contain a section for the target version."""
    c = Check(name="CHANGELOG updated")
    tag = cl.tag
    tag_ver = tag.lstrip("v")
    # strip leading zeros from semver segments: 0.1.0-beta.3 stays, 0.1.0 stays
    changelog_path = ROOT / "CHANGELOG.md"
    try:
        if not changelog_path.exists():
            c.detail = "CHANGELOG.md not found"
            cl.checks.append(c)
            return
        content = changelog_path.read_text()
        # Match headings like "## [0.1.0-beta.3]" or "## [0.1.0-beta.3] — ..."
        pattern = re.compile(rf"^##\s*\[{re.escape(tag_ver)}\]", re.MULTILINE)
        if pattern.search(content):
            c.passed = True
            c.detail = f"CHANGELOG.md has entry for [{tag_ver}]"
        else:
            c.detail = f"CHANGELOG.md has NO entry for [{tag_ver}] — add one before release"
    except Exception as exc:
        cl.errors.append(f"CHANGELOG check failed: {exc}")
    cl.checks.append(c)


def check_version_consistency(cl: Checklist) -> None:
    """All project version files must agree."""
    c = Check(name="Version consistency")
    try:
        result = _run(["python3", "scripts/check_version_consistency.py"])
        if result.returncode == 0:
            c.passed = True
            c.detail = "all version files consistent"
        else:
            c.detail = f"version inconsistency: {result.stdout.strip() or result.stderr.strip()}"
    except Exception as exc:
        cl.errors.append(f"Version check failed: {exc}")
    cl.checks.append(c)


def check_dirty_tree(cl: Checklist) -> None:
    """Working tree must be clean — no uncommitted changes."""
    c = Check(name="Clean tree")
    try:
        result = _run(["git", "status", "--porcelain"])
        if result.returncode == 0 and not result.stdout.strip():
            c.passed = True
            c.detail = "working tree clean"
        else:
            dirty_count = len([l for l in result.stdout.strip().splitlines() if l])
            c.detail = f"{dirty_count} dirty file(s) — commit or stash before release"
    except Exception as exc:
        cl.errors.append(f"Dirty-tree check failed: {exc}")
    cl.checks.append(c)


def check_release_cut_target(cl: Checklist) -> None:
    """Verify the release-cut Makefile target has the expected steps."""
    c = Check(name="release-cut target shape")
    makefile = ROOT / "Makefile"
    try:
        content = makefile.read_text()
        # Find the release-cut recipe
        in_target = False
        recipe_lines: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("release-cut:"):
                in_target = True
                continue
            if in_target:
                if stripped == "":
                    continue
                if not line.startswith("\t") and not line.startswith("    "):
                    break  # next target
                recipe_lines.append(stripped)
        expected_steps = [
            ("require-ci-green", "require-ci-green step"),
            ("check-readme-status", "check-readme-status step"),
            ("git-push-sandboxcom", "git-push-sandboxcom step"),
            ("git-tag-push", "git-tag-push step"),
        ]
        all_found = True
        for pattern, desc in expected_steps:
            if not any(pattern in rl for rl in recipe_lines):
                all_found = False
                c.detail = f"Missing {desc} in release-cut target"
                break
        if all_found:
            c.passed = True
            c.detail = "release-cut target has all 4 required steps"
    except Exception as exc:
        cl.errors.append(f"release-cut target check failed: {exc}")
    cl.checks.append(c)


def check_collect(cl: Checklist) -> None:
    """Tests must collect without errors."""
    c = Check(name="Test collection")
    try:
        result = _run_make("collect-check")
        if result.returncode == 0:
            c.passed = True
            c.detail = "0 collection errors"
        else:
            c.detail = "collection errors found"
    except Exception as exc:
        cl.errors.append(f"Collection check failed: {exc}")
    cl.checks.append(c)


ALL_CHECKS = [
    ("CI green", check_ci_green),
    ("Gate green", check_gate),
    ("Lint 0", check_lint),
    ("Typecheck 0", check_typecheck),
    ("Test collection", check_collect),
    ("README current", check_readme_current),
    ("CHANGELOG updated", check_changelog_updated),
    ("Version consistency", check_version_consistency),
    ("Clean tree", check_dirty_tree),
    ("release-cut target shape", check_release_cut_target),
]


def run_checklist(tag: str) -> Checklist:
    cl = Checklist(tag=tag)
    for _name, check_fn in ALL_CHECKS:
        check_fn(cl)
    return cl


def print_report(cl: Checklist, human: bool = False) -> str:
    lines: list[str] = []
    lines.append(f"=== Release-Cut Checklist for {cl.tag} ===")
    lines.append("")
    for c in cl.checks:
        status = "PASS" if c.passed else "FAIL"
        lines.append(f"  [{status}] {c.name}: {c.detail}")
    for err in cl.errors:
        lines.append(f"  [ERR]  {err}")
    lines.append("")
    if cl.all_passed:
        lines.append("=" * 60)
        lines.append(f"READY FOR RELEASE-CUT: make release-cut TAG={cl.tag} MSG='...'")
        lines.append("=" * 60)
    else:
        lines.append("=" * 60)
        lines.append("BLOCKERS — fix these before release-cut:")
        for blocker in cl.blockers:
            lines.append(f"  * {blocker}")
        lines.append("=" * 60)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("TAG", help="Target release tag, e.g. v0.1.0-beta.3")
    parser.add_argument("--human", action="store_true", help="human-readable report (always printed)")
    args = parser.parse_args(argv)
    cl = run_checklist(args.TAG)
    print(print_report(cl))
    return 0 if cl.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
