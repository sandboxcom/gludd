"""E.9 Skip-smell cleanup — enforce skip/hide hygiene across the test suite.

Rules:
1. ``pytest.skip`` MUST NOT be used where the test should FAIL.  Skipping a
   violation ("Optional: N targets without recipe") hides real drift.  Use
   ``warnings.warn`` or a dedicated advisory test instead.
2. Every unconditional ``pytest.skip`` (not guarded by ``if``) MUST carry a
   documented reason string that references a config variable, env var, tool
   check, or AGENTS.md section.  A bare ``pytest.skip("foo")`` in a test body
   with no guard is a stale stub.
3. ``xfail(strict=True)`` markers are forbidden — every strict-xfail that passes
   becomes XPASS (a real failure), but every one that still fails masks a latent
   gap.  Replace with: (a) a real test if the feature now works, OR (b)
   ``xfail(reason=..., strict=False)`` with an honest gap note.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from typing import Any

import pytest

TESTS_ROOT = pathlib.Path(__file__).resolve().parent.parent

SKIP_COUNT_SNAPSHOT_FILE = pathlib.Path(__file__).resolve().parent / ".e9_skip_counts.json"

STALENESS_THRESHOLD_DAYS = 90

FORBIDDEN_SKIP_PATTERNS = [
    re.compile(r"pytest\.skip\s*\(\s*f['\"]Optional:", re.IGNORECASE),
]

ALLOWLIST_SKIP_FILES = frozenset({
    str(TESTS_ROOT / "e2e" / "providers" / "conftest.py"),
})


def _iter_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in str(p)
        and not p.name.startswith("conftest")  # conftest skips belong to fixtures; checked separately
        and "test_e9_skip_smell" not in p.name
    )


def _ast_skip_info(file_path: pathlib.Path) -> dict[str, Any]:
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
    calls: list[dict[str, Any]] = []
    xfails: list[dict[str, Any]] = []
    bare_marks: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            obj = node.func.value
            obj_name = ""
            if isinstance(obj, ast.Name):
                obj_name = obj.id
            elif isinstance(obj, ast.Attribute):
                obj_name = f"{obj.value.id}.{obj.attr}" if isinstance(obj.value, ast.Name) else ""

            if func_name == "skip" and obj_name in ("pytest",):
                args = []
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        args.append(arg.value)
                calls.append({
                    "line": node.lineno,
                    "col": node.col_offset,
                    "args": args,
                    "keyword": {
                        kw.arg: ast.literal_eval(kw.value) if isinstance(kw.value, ast.Constant) else None
                        for kw in node.keywords
                    },
                })
            elif func_name == "xfail" and obj_name in ("pytest.mark",):
                reason = ""
                strict_val = None
                for kw in node.keywords:
                    if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                        reason = kw.value.value
                    if kw.arg == "strict" and isinstance(kw.value, ast.Constant):
                        strict_val = kw.value.value
                xfails.append({
                    "line": node.lineno,
                    "col": node.col_offset,
                    "reason": reason,
                    "strict": strict_val,
                })

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                        and dec.func.attr == "skip" and isinstance(dec.func.value, ast.Attribute)
                        and dec.func.value.attr == "mark"):
                    reason = ""
                    for kw in dec.keywords:
                        if kw.arg == "reason" and isinstance(kw.value, ast.Constant):
                            reason = kw.value.value
                    bare_marks.append({
                        "line": node.lineno,
                        "func": node.name,
                        "reason": reason,
                    })

    return {"skips": calls, "xfails": xfails, "bare_skips": bare_marks}


class TestSkipSmellDetection:
    """Structural checks on the entire tests/ tree."""

    def test_no_forbidden_skip_patterns(self):
        forbidden: list[tuple[str, int]] = []
        for fp in _iter_python_files(TESTS_ROOT):
            if str(fp) in ALLOWLIST_SKIP_FILES:
                continue
            text = fp.read_text()
            for i, line in enumerate(text.splitlines(), 1):
                for pat in FORBIDDEN_SKIP_PATTERNS:
                    if pat.search(line):
                        forbidden.append((str(fp.relative_to(TESTS_ROOT)), i))
                        break
        assert not forbidden, (
            f"{len(forbidden)} forbidden skip patterns found "
            f"(pytest.skip used as 'Optional' advisory — use warnings.warn):\n"
            + "\n".join(f"  {f}:{ln}" for f, ln in forbidden)
        )

    def test_no_unconditional_skip_without_guard(self):
        stale: list[tuple[str, int, str]] = []
        for fp in _iter_python_files(TESTS_ROOT):
            if str(fp) in ALLOWLIST_SKIP_FILES:
                continue
            info = _ast_skip_info(fp)
            source_lines = fp.read_text().splitlines()
            for skip in info["skips"]:
                line_no = skip["line"]
                if line_no < 3:
                    continue
                guard_candidates = [
                    source_lines[line_no - 2].strip(),
                    source_lines[line_no - 3].strip() if line_no >= 3 else "",
                    source_lines[line_no - 4].strip() if line_no >= 4 else "",
                ]
                is_guarded = any(
                    g.startswith("if ")
                    or g.startswith("elif ")
                    or g.startswith("try:")
                    or g.startswith("except")
                    or "importorskip" in g
                    for g in guard_candidates
                )
                args = skip.get("args", [])
                has_reason = any("not installed" in a or "not available" in a or "absent" in a
                                 or "not set" in a or "not found" in a or "missing" in a
                                 or "deprecated" in a or "not supported" in a
                                 or "not generated" in a or "not populated" in a
                                 or "not initialized" in a or "not enforced" in a
                                 or "credentials required" in a or "required for live" in a
                                 or "set GLUDD" in a or "set OPENCODE" in a
                                 or "offline" in a or "CI" in a.lower()
                                 for a in args)
                allow_module_level = skip.get("keyword", {}).get("allow_module_level", False)
                if not is_guarded and not has_reason and not allow_module_level:
                    stale.append((str(fp.relative_to(TESTS_ROOT)), line_no, args))
        assert not stale, (
            f"{len(stale)} unconditional pytest.skip calls without documented reason:\n"
            + "\n".join(f"  {f}:{ln}  skip({args!r})" for f, ln, args in stale)
        )

    def test_no_strict_xfail(self):
        strict_xfails: list[tuple[str, int, str]] = []
        for fp in _iter_python_files(TESTS_ROOT):
            info = _ast_skip_info(fp)
            for xf in info["xfails"]:
                if xf["strict"] is True:
                    strict_xfails.append((str(fp.relative_to(TESTS_ROOT)), xf["line"], xf.get("reason", "")))
        assert not strict_xfails, (
            f"{len(strict_xfails)} strict-xfail markers found (replace with real test or strict=False):\n"
            + "\n".join(f"  {f}:{ln}  {r}" for f, ln, r in strict_xfails)
        )

    def test_skip_count_snapshot_exists(self):
        if not SKIP_COUNT_SNAPSHOT_FILE.exists():
            _write_snapshot()
        assert SKIP_COUNT_SNAPSHOT_FILE.exists(), (
            f"Skip-count snapshot file missing at {SKIP_COUNT_SNAPSHOT_FILE} "
            "and could not be written."
        )

    def test_skip_count_not_growing(self):
        if not SKIP_COUNT_SNAPSHOT_FILE.exists():
            _write_snapshot()
        with open(SKIP_COUNT_SNAPSHOT_FILE) as f:
            snapshot = json.load(f)

        current = _count_skips()
        total_skip = current["pytest_skip_total"]
        bare_skip = current["bare_mark_skip_total"]
        xfail_total = current["xfail_total"]

        snap_skip = snapshot["pytest_skip_total"]
        snap_bare = snapshot["bare_mark_skip_total"]
        snap_xfail = snapshot["xfail_total"]

        growing = []
        if total_skip > snap_skip:
            growing.append(f"pytest.skip: {snap_skip} → {total_skip} (+{total_skip - snap_skip})")
        if bare_skip > snap_bare:
            growing.append(f"@pytest.mark.skip: {snap_bare} → {bare_skip} (+{bare_skip - snap_bare})")
        if xfail_total > snap_xfail:
            growing.append(f"xfail: {snap_xfail} → {xfail_total} (+{xfail_total - snap_xfail})")

        assert not growing, (
            "Skip/xfail counts have INCREASED since last snapshot. "
            "Remove stale skips before updating the snapshot:\n"
            + "\n".join(f"  {g}" for g in growing)
        )


    def test_hook_liveness_skip_smell_in_ci(self):
        """Hook/enforcement tests MUST NOT skip based on CI environment.

        A pytest.skip('... CI ...') in a hook-liveness or enforcement test
        masks real failures in CI — the test passes green without exercising
        the hook.  The hook fixture in _hook_fixtures.py has legitimate
        node-version / harness-probe preconditions; those are allowed.
        But a test whose sole reason for skipping is "running in CI" is a
        stale stub — remove the CI guard and fix the underlying blocker.
        """
        HOOK_FILE_PATTERNS = re.compile(r"(_hook|enforce|hook_runtime|hook_live)")
        HOOK_TEST_ROOTS = frozenset({
            str(TESTS_ROOT / "unit" / "_hook_fixtures.py"),
            str(TESTS_ROOT / "unit" / "test_hook_runtime_verification.py"),
        })
        ci_skips: list[tuple[str, int, str]] = []
        for fp in _iter_python_files(TESTS_ROOT):
            rel = str(fp.relative_to(TESTS_ROOT))
            is_hook_file = (
                HOOK_FILE_PATTERNS.search(fp.name)
                or str(fp) in HOOK_TEST_ROOTS
            )
            if not is_hook_file:
                continue
            info = _ast_skip_info(fp)
            for skip in info["skips"]:
                args = skip.get("args", [])
                for a in args:
                    if "CI" in a or "ci" in a.lower():
                        ci_skips.append((rel, skip["line"], a))
                        break
        assert not ci_skips, (
            f"{len(ci_skips)} CI-conditional skips in hook/enforcement test files. "
            "Remove the CI guard and fix the underlying blocker, or if the skip is "
            "legitimate (node/harness unavailable), reference a concrete precondition "
            "rather than 'CI':\n"
            + "\n".join(f"  {f}:{ln}  skip({args!r})" for f, ln, args in ci_skips)
        )


def _write_snapshot() -> None:
    counts = _count_skips()
    with open(SKIP_COUNT_SNAPSHOT_FILE, "w") as f:
        json.dump(counts, f, indent=2)
    print(f"\nE9 skip counts written to {SKIP_COUNT_SNAPSHOT_FILE}: {json.dumps(counts)}")


def _count_skips() -> dict[str, int]:
    skip_total = 0
    bare_total = 0
    xfail_total = 0
    for fp in _iter_python_files(TESTS_ROOT):
        info = _ast_skip_info(fp)
        skip_total += len(info["skips"])
        bare_total += len(info["bare_skips"])
        xfail_total += len(info["xfails"])
    return {
        "pytest_skip_total": skip_total,
        "bare_mark_skip_total": bare_total,
        "xfail_total": xfail_total,
    }


class TestSkipSmellSelf:
    """Meta-tests — ensure this file's own machinery is correct."""

    def test_skip_count_snapshot_is_valid_json(self):
        if not SKIP_COUNT_SNAPSHOT_FILE.exists():
            pytest.skip("snapshot file does not exist yet — run the full test file to generate")
        with open(SKIP_COUNT_SNAPSHOT_FILE) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        for key in ("pytest_skip_total", "bare_mark_skip_total", "xfail_total"):
            assert key in data, f"snapshot missing key: {key}"
            assert isinstance(data[key], int), f"snapshot key {key} is not int: {type(data[key])}"


def pytest_sessionfinish(session):
    _write_snapshot()
