"""Deep audit of @pytest.mark.* usage across the entire test suite.

Verifies:
- All custom markers are registered in pyproject.toml
- No deprecated marker patterns
- xfail always carries a reason
- skip/skipif always carries a reason
- slow tests are marked
- timeout values are within sane bounds
- marker usage is internally consistent (no contradictions)
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _test_files() -> list[Path]:
    root = _repo_root()
    return list((root / "tests").rglob("test_*.py"))


def _registered_markers() -> set[str]:
    root = _repo_root()
    with open(root / "pyproject.toml", "rb") as f:
        cfg = tomllib.load(f)
    markers_raw: list[str] = cfg.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
    return {m.split(":")[0].strip() for m in markers_raw}


def _builtin_markers() -> frozenset[str]:
    return frozenset(
        {
            "skip",
            "skipif",
            "xfail",
            "parametrize",
            "usefixtures",
            "filterwarnings",
            "asyncio",
            "timeout",
            "timeout_decorator",
            "tryfirst",
            "trylast",
            "no_cover",
            "fixture",
        }
    )


def _external_plugin_markers() -> frozenset[str]:
    """Markers provided by third-party pytest plugins (not built-in, not custom)."""
    return frozenset({"anyio"})


def _read_marker_full_text(file_path: Path, start_line: int) -> str:
    """Read from start_line forward until balanced parentheses close the marker invocation."""
    lines = file_path.read_text().splitlines()
    raw = ""
    depth = 0
    started = False
    for i in range(start_line - 1, len(lines)):
        line = lines[i]
        raw += line + "\n"
        for ch in line:
            if ch == "(":
                depth += 1
                started = True
            elif ch == ")":
                depth -= 1
        if started and depth == 0:
            break
    return raw.strip()


_EQUAL_KWARG = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)", re.DOTALL)


def _extract_kwargs(marker_full_text: str) -> dict[str, str]:
    """Extract keyword arguments from a full marker invocation text."""
    m = re.search(r"@pytest\.mark\.\w+\((.*)\)\s*$", marker_full_text, re.DOTALL)
    if not m:
        return {}
    body = m.group(1).strip()
    if not body:
        return {}
    parts = _split_args(body)
    kw: dict[str, str] = {}
    for part in parts:
        em = _EQUAL_KWARG.match(part)
        if em:
            key = em.group(1)
            val = em.group(2).strip().strip("\"'")
            kw[key] = val
        else:
            kw.setdefault("_positional", part.strip())
    return kw


def _split_args(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    in_single = False
    in_double = False
    for ch in body:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "(" and not in_single and not in_double:
            depth += 1
        elif ch == ")" and not in_single and not in_double:
            depth -= 1
        elif ch == "," and depth == 0 and not in_single and not in_double:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(ch)
    remaining = "".join(current).strip()
    if remaining:
        parts.append(remaining)
    return parts


_MARKER_LINE_RE = re.compile(r"^\s*@pytest\.mark\.(\w+)\b")


def _collect() -> list[tuple[Path, int, str, str]]:
    """Return [(path, line_no, marker_name, full_marker_text)] for every marker invocation."""
    results: list[tuple[Path, int, str, str]] = []
    for tf in _test_files():
        lines = tf.read_text().splitlines()
        for line_no, line in enumerate(lines, 1):
            m = _MARKER_LINE_RE.search(line)
            if m:
                full_text = _read_marker_full_text(tf, line_no)
                results.append((tf, line_no, m.group(1), full_text))
    return results


def _decorated_node_docstring(file_path: Path, marker_line: int) -> str:
    """Return the docstring owned by the node decorated at ``marker_line``."""
    tree = ast.parse(file_path.read_text(), filename=str(file_path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if any(decorator.lineno == marker_line for decorator in node.decorator_list):
            return ast.get_docstring(node, clean=True) or ""
    return ""


# ── 1. Marker registration ─────────────────────────────────────────────────


def test_01_all_custom_markers_registered():
    """Every custom marker must be registered in pyproject.toml."""
    registered = _registered_markers()
    builtin = _builtin_markers()
    external = _external_plugin_markers()
    violations: list[str] = []
    for path, line_no, name, _full in _collect():
        if name in builtin or name in external:
            continue
        if name in registered:
            continue
        violations.append(f"{path}:{line_no}: '{name}' not registered in pyproject.toml markers")
    assert not violations, "\n".join(violations)


def test_02_marker_names_are_valid_identifiers():
    """Every marker name must be a valid Python identifier."""
    ident_re = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
    violations: list[str] = []
    for path, line_no, name, _full in _collect():
        if not ident_re.match(name):
            violations.append(f"{path}:{line_no}: invalid marker name '{name}'")
    assert not violations, "\n".join(violations)


def test_03_marker_count_is_reasonable():
    """At least 100 marker usages confirms robust scan."""
    data = _collect()
    assert len(data) > 100, f"Expected >100 marker usages, found {len(data)}"


def test_04_no_unknown_underscore_prefixed_markers():
    """Markers starting with _ are reserved for internal use."""
    violations: list[str] = []
    for path, line_no, name, _full in _collect():
        if name.startswith("_") and name not in _builtin_markers():
            violations.append(f"{path}:{line_no}: suspicious internal marker '{name}'")
    assert not violations, "\n".join(violations)


# ── 2. xfail audits ─────────────────────────────────────────────────────────


def test_05_xfail_always_has_reason():
    """Every @pytest.mark.xfail must carry a reason= parameter."""
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "xfail":
            continue
        kw = _extract_kwargs(full_text)
        if "reason" not in kw or not kw["reason"].strip():
            violations.append(f"{path}:{line_no}: xfail missing reason=")
    assert not violations, "\n".join(violations)


def test_06_xfail_reason_is_descriptive():
    """xfail reason must be at least 10 meaningful characters."""
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "xfail":
            continue
        kw = _extract_kwargs(full_text)
        reason = kw.get("reason", "")
        if len(reason.strip()) < 10:
            violations.append(f"{path}:{line_no}: xfail reason too short ({len(reason.strip())} chars): {reason!r}")
    assert not violations, "\n".join(violations)


def test_07_xfail_reason_has_spec_reference():
    """xfail reason should reference a spec section, issue, or task code."""
    patterns = [
        r"E\d+",
        r"GRC-AT-\d+",
        r"SSRF",
        r"langchain",
        r"AGENTIC_IMPLEMENTATION_SPEC",
        r"not yet wired",
        r"not yet implemented",
        r"deferred to",
        r"framework limitation",
        r"ratchet",
        r"AIML-AT-\d+",
        r"CHEM-AT-\d+",
        r"\bS\d+(?:\.\d+)+\b",
        r"(?:docs/)?[A-Za-z0-9_./-]+\.md(?:\s*[§:#][^,;)]*)?",
        r"(?:issue\s*)?#\d+",
    ]
    combined = re.compile("|".join(patterns), re.IGNORECASE)
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "xfail":
            continue
        kw = _extract_kwargs(full_text)
        reason = kw.get("reason", "")
        if not reason:
            continue
        if not combined.search(reason):
            violations.append(f"{path}:{line_no}: xfail reason lacks spec/issue reference: {reason[:60]!r}")
    assert not violations, f"Found {len(violations)} xfail markers without spec/issue reference:\n" + "\n".join(
        violations
    )


def test_08_xfail_strict_is_recommended():
    """Every xfail must explicitly state its strict pass/fail semantics."""
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "xfail":
            continue
        strict = _extract_kwargs(full_text).get("strict")
        if strict not in {"True", "False"}:
            violations.append(
                f"{path}:{line_no}: xfail must explicitly set strict=True|False"
            )
    assert not violations, "\n".join(violations)


# ── 3. skip / skipif audits ─────────────────────────────────────────────────


def test_09_skip_always_has_reason():
    """Every @pytest.mark.skip must carry a reason."""
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "skip":
            continue
        kw = _extract_kwargs(full_text)
        has_positional = "reason" in kw and len(kw["reason"].strip()) >= 3
        has_raw_positional = "_positional" in kw and len(kw["_positional"].strip("\"'")) >= 3
        if not has_positional and not has_raw_positional:
            violations.append(f"{path}:{line_no}: skip missing reason")
    assert not violations, "\n".join(violations)


def test_10_skipif_always_has_reason():
    """Every @pytest.mark.skipif must carry a reason= parameter."""
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "skipif":
            continue
        kw = _extract_kwargs(full_text)
        if "reason" not in kw or len(kw["reason"].strip()) < 2:
            violations.append(f"{path}:{line_no}: skipif missing reason=")
    assert not violations, "\n".join(violations)


def test_11_skipif_reason_is_descriptive():
    """skipif reason must be at least 5 characters."""
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "skipif":
            continue
        kw = _extract_kwargs(full_text)
        reason = kw.get("reason", "")
        if len(reason.strip()) < 5:
            violations.append(f"{path}:{line_no}: skipif reason too short ({len(reason.strip())} chars)")
    assert not violations, "\n".join(violations)


# ── 4. Slow test marking ────────────────────────────────────────────────────


def test_12_slow_marker_exists_and_registered():
    """If slow markers are used, they must be registered."""
    registered = _registered_markers()
    uses_slow = any(name == "slow" for _, _, name, _ in _collect())
    if uses_slow:
        assert "slow" in registered, "'slow' marker is used but not registered in pyproject.toml"


def test_13_slow_tests_have_explanation():
    """slow tests should have an adjacent comment or docstring explaining slowness."""
    violations: list[str] = []
    for path, line_no, name, _full in _collect():
        if name != "slow":
            continue
        lines = path.read_text().splitlines()
        idx = line_no - 1
        has_comment = False
        for i in range(max(0, idx - 3), min(len(lines), idx + 4)):
            stripped = lines[i].strip()
            if i == idx:
                continue
            if stripped.startswith("#"):
                has_comment = True
                break
        has_docstring = bool(_decorated_node_docstring(path, line_no).strip())
        if not has_comment and not has_docstring:
            violations.append(
                f"{path}:{line_no}: slow test lacks an adjacent comment or docstring"
            )
    assert not violations, "\n".join(violations)


# ── 5. timeout marker ───────────────────────────────────────────────────────


def test_14_timeout_values_are_reasonable():
    """timeout values must be positive integers <= 7200s (2h)."""
    violations: list[str] = []
    for path, line_no, name, full_text in _collect():
        if name != "timeout":
            continue
        m = re.search(r"timeout\(\s*(\d+)\s*\)", full_text)
        if m:
            val = int(m.group(1))
            if val <= 0:
                violations.append(f"{path}:{line_no}: timeout value {val} is not positive")
            elif val > 7200:
                violations.append(f"{path}:{line_no}: timeout value {val} exceeds 7200s")
    assert not violations, "\n".join(violations)


def test_15_pytest_timeout_in_dependencies():
    """pytest-timeout must be in dev dependencies."""
    with open(_repo_root() / "pyproject.toml", "rb") as f:
        cfg = tomllib.load(f)
    deps = cfg.get("dependency-groups", {}).get("dev", [])
    has_timeout = any("pytest-timeout" in d for d in deps)
    assert has_timeout, "pytest-timeout not found in dev dependencies"


# ── 6. deprecated / misused patterns ────────────────────────────────────────


def test_16_no_deprecated_marker_apis():
    """No test should use the deprecated pytest1.* or _pytest.* marker API."""
    violations: list[str] = []
    deprecated = re.compile(r"(pytest1|_pytest)\.mark")
    for tf in _test_files():
        for line_no, line in enumerate(tf.read_text().splitlines(), 1):
            if deprecated.search(line):
                violations.append(f"{tf}:{line_no}: deprecated marker API usage")
    assert not violations, "\n".join(violations)


def test_17_no_line_level_marker_on_same_line_as_def():
    """@pytest.mark.X should be on its own line, not on the same line as def."""
    violations: list[str] = []
    same_line = re.compile(r"@pytest\.mark\.\w+\(.*\)\s+def\s+")
    for tf in _test_files():
        for line_no, line in enumerate(tf.read_text().splitlines(), 1):
            if same_line.search(line):
                violations.append(f"{tf}:{line_no}: marker and def on same line — separate them")
    assert not violations, "\n".join(violations)


# ── 7. resource-skips only in appropriate test levels ───────────────────────


def test_18_requires_slurm_not_in_unit_tests():
    """requires_slurm should only be used in integration or e2e tests."""
    violations: list[str] = []
    for path, _, name, _full in _collect():
        if name != "requires_slurm":
            continue
        rel = str(path)
        if "/unit/" in rel:
            violations.append(f"{path}: requires_slurm in unit test — expected in integration/ or e2e/")
    assert not violations, "\n".join(violations)


def test_19_requires_postgres_not_in_unit_tests():
    """requires_postgres should only be used in integration or e2e tests."""
    violations: list[str] = []
    for path, _, name, _full in _collect():
        if name != "requires_postgres":
            continue
        rel = str(path)
        if "/unit/" in rel:
            violations.append(f"{path}: requires_postgres in unit test — expected in integration/ or e2e/")
    assert not violations, "\n".join(violations)


def test_20_e2e_marker_used_in_e2e_tests():
    """At least some tests in tests/e2e/ use the e2e marker."""
    e2e_dir = _repo_root() / "tests" / "e2e"
    if not e2e_dir.is_dir():
        pytest.skip("No e2e directory")
    e2e_count = 0
    for path, _, name, _full in _collect():
        if name == "e2e" and "/e2e/" in str(path):
            e2e_count += 1
    assert e2e_count > 0, "No e2e tests use the e2e marker"
