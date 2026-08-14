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

TESTS_ROOT = pathlib.Path(__file__).resolve().parent.parent

SKIP_COUNT_SNAPSHOT_FILE = pathlib.Path(__file__).resolve().parent / ".e9_skip_counts.json"

STALENESS_THRESHOLD_DAYS = 90

DOCUMENTED_REASON_FRAGMENTS = (
    "not installed",
    "not available",
    "absent",
    "not set",
    "not found",
    "missing",
    "deprecated",
    "not supported",
    "not generated",
    "not populated",
    "not initialized",
    "not enforced",
    "credentials required",
    "required for live",
    "set gludd",
    "set opencode",
    "offline",
    "not yet wired",
    "not yet created",
    "not yet built",
    "not inspectable",
    "known missing",
)

DOCUMENTED_REASON_REFERENCE = re.compile(
    r"\b(?:[A-Z][A-Z0-9]*-[A-Z][A-Z0-9]*-\d+|[A-Z]{1,4}\d+(?:\.\d+)*)\b"
)

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
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
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
                    args.extend(_string_fragments(arg))
                calls.append({
                    "line": node.lineno,
                    "col": node.col_offset,
                    "args": args,
                    "guarded": _has_control_flow_guard(node, parents),
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
                        value = kw.value.value
                        if isinstance(value, str):
                            reason = value
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
                            value = kw.value.value
                            if isinstance(value, str):
                                reason = value
                    bare_marks.append({
                        "line": node.lineno,
                        "func": node.name,
                        "reason": reason,
                    })

    return {"skips": calls, "xfails": xfails, "bare_skips": bare_marks}


def _string_fragments(node: ast.AST) -> list[str]:
    """Extract static text fragments from literals, f-strings, and concatenation."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.JoinedStr):
        return [
            fragment
            for value in node.values
            for fragment in _string_fragments(value)
        ]
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _string_fragments(node.left) + _string_fragments(node.right)
    return []


def _has_control_flow_guard(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    """Return whether a skip is nested under explicit conditional control flow."""
    parent = parents.get(node)
    while parent is not None:
        if isinstance(
            parent,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.Match,
                ast.Try,
                ast.TryStar,
                ast.ExceptHandler,
            ),
        ):
            return True
        parent = parents.get(parent)

    statement: ast.AST = node
    while not isinstance(statement, ast.stmt):
        next_parent = parents.get(statement)
        if next_parent is None:
            return False
        statement = next_parent
    container = parents.get(statement)
    body = getattr(container, "body", ())
    if not isinstance(body, list) or statement not in body:
        return False
    return any(
        _statement_has_terminating_path(earlier)
        for earlier in body[: body.index(statement)]
    )


def _block_terminates(statements: list[ast.stmt]) -> bool:
    """Return whether a branch ends the current execution path."""
    return bool(statements) and isinstance(
        statements[-1],
        (ast.Return, ast.Raise, ast.Break, ast.Continue),
    )


def _statement_has_terminating_path(statement: ast.stmt) -> bool:
    """Return whether an earlier branch conditionally exits the current scope."""
    if isinstance(statement, ast.If):
        return _block_terminates(statement.body) or _block_terminates(
            statement.orelse
        )
    if isinstance(statement, (ast.Try, ast.TryStar)):
        blocks = [
            statement.body,
            statement.orelse,
            *(handler.body for handler in statement.handlers),
        ]
        return any(_block_terminates(block) for block in blocks)
    return False


def _has_documented_reason(args: list[str]) -> bool:
    """Return whether literal skip arguments name a concrete precondition."""
    normalized = " ".join(args).casefold()
    return (
        any(fragment in normalized for fragment in DOCUMENTED_REASON_FRAGMENTS)
        or re.search(r"\bci\b", normalized) is not None
        or DOCUMENTED_REASON_REFERENCE.search(" ".join(args)) is not None
    )


class TestSkipSmellDetection:
    """Structural checks on the entire tests/ tree."""

    def test_no_forbidden_skip_patterns(self) -> None:
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

    def test_no_unconditional_skip_without_guard(self) -> None:
        stale: list[tuple[str, int, list[str]]] = []
        for fp in _iter_python_files(TESTS_ROOT):
            if str(fp) in ALLOWLIST_SKIP_FILES:
                continue
            info = _ast_skip_info(fp)
            for skip in info["skips"]:
                line_no = skip["line"]
                args = skip.get("args", [])
                allow_module_level = skip.get("keyword", {}).get("allow_module_level", False)
                if (
                    not skip["guarded"]
                    and not _has_documented_reason(args)
                    and not allow_module_level
                ):
                    stale.append((str(fp.relative_to(TESTS_ROOT)), line_no, args))
        assert not stale, (
            f"{len(stale)} unconditional pytest.skip calls without documented reason:\n"
            + "\n".join(f"  {f}:{ln}  skip({args!r})" for f, ln, args in stale)
        )

    def test_no_strict_xfail(self) -> None:
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

    def test_skip_count_snapshot_exists(self) -> None:
        assert SKIP_COUNT_SNAPSHOT_FILE.exists(), (
            f"Skip-count snapshot file missing at {SKIP_COUNT_SNAPSHOT_FILE} "
            "(restore the reviewed baseline; tests never generate it)."
        )

    def test_skip_count_not_growing(self) -> None:
        assert SKIP_COUNT_SNAPSHOT_FILE.exists(), (
            "Skip-count baseline is required and must be reviewed, not generated "
            "as a test side effect."
        )
        snapshot = json.loads(SKIP_COUNT_SNAPSHOT_FILE.read_text())

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


    def test_hook_liveness_skip_smell_in_ci(self) -> None:
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
                    if re.search(r"\bci\b", a, re.IGNORECASE):
                        ci_skips.append((rel, skip["line"], a))
                        break
        assert not ci_skips, (
            f"{len(ci_skips)} CI-conditional skips in hook/enforcement test files. "
            "Remove the CI guard and fix the underlying blocker, or if the skip is "
            "legitimate (node/harness unavailable), reference a concrete precondition "
            "rather than 'CI':\n"
            + "\n".join(
                f"  {file_name}:{line_no}  skip({reason!r})"
                for file_name, line_no, reason in ci_skips
            )
        )


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

    def test_ast_guard_detection_uses_ancestry(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        source = tmp_path / "test_guarded.py"
        source.write_text(
            "\n".join(
                (
                    "import pytest",
                    "def test_guarded(enabled):",
                    "    if enabled:",
                    "        first = 1",
                    "        second = first + 1",
                    "        third = second + 1",
                    '        pytest.skip("tool not available")',
                )
            )
        )

        [skip] = _ast_skip_info(source)["skips"]

        assert skip["guarded"] is True

    def test_ast_guard_detection_rejects_unconditional_skip(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        source = tmp_path / "test_unconditional.py"
        source.write_text(
            "\n".join(
                (
                    "import pytest",
                    "def test_unconditional():",
                    '    pytest.skip("unfinished placeholder")',
                )
            )
        )

        [skip] = _ast_skip_info(source)["skips"]

        assert skip["guarded"] is False
        assert not _has_documented_reason(skip["args"])

    def test_ast_guard_detection_follows_terminating_branch(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        source = tmp_path / "test_inverse_guard.py"
        source.write_text(
            "\n".join(
                (
                    "import pytest",
                    "def test_inverse_guard(available):",
                    "    if available:",
                    "        return",
                    "    detail = 'bounded setup'",
                    "    assert detail",
                    '    pytest.skip("capability unavailable")',
                )
            )
        )

        [skip] = _ast_skip_info(source)["skips"]

        assert skip["guarded"] is True

    def test_ast_guard_detection_follows_terminating_except(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        source = tmp_path / "test_try_guard.py"
        source.write_text(
            "\n".join(
                (
                    "import pytest",
                    "def test_try_guard(load):",
                    "    try:",
                    "        load()",
                    "    except LookupError:",
                    "        return",
                    '    pytest.skip("loaded capability needs a follow-up")',
                )
            )
        )

        [skip] = _ast_skip_info(source)["skips"]

        assert skip["guarded"] is True

    def test_dynamic_reason_preserves_static_spec_evidence(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        source = tmp_path / "test_dynamic_reason.py"
        source.write_text(
            "\n".join(
                (
                    "import pytest",
                    "def test_dynamic(tool):",
                    '    pytest.skip(f"S83.110: {tool} not yet wired")',
                )
            )
        )

        [skip] = _ast_skip_info(source)["skips"]

        assert skip["guarded"] is False
        assert _has_documented_reason(skip["args"])

    def test_skip_count_snapshot_is_valid_json(self) -> None:
        assert SKIP_COUNT_SNAPSHOT_FILE.exists()
        data = json.loads(SKIP_COUNT_SNAPSHOT_FILE.read_text())
        assert isinstance(data, dict)
        assert data.get("schema_version") == 2
        assert data.get("reviewed_task") == "S83.110"
        for key in ("pytest_skip_total", "bare_mark_skip_total", "xfail_total"):
            assert key in data, f"snapshot missing key: {key}"
            assert type(data[key]) is int, (
                f"snapshot key {key} is not int: {type(data[key])}"
            )
            assert data[key] >= 0, f"snapshot key {key} must be non-negative"
