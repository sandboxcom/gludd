"""Deep source file line-count + size audit — 18 tests on src/general_ludd/."""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src" / "general_ludd"
SRC_FILES = sorted(p for p in SRC_DIR.rglob("*.py") if p.name != "__pycache__")

MAX_FILE_LINES = 5500
MAX_INIT_LINES = 400
MAX_DIR_LINES = 40000
MAX_LINE_LENGTH = 200
MAX_COMMENT_RATIO = 0.70
MIN_CODE_LINES = 5


def _read_lines(path: Path) -> list[str]:
    return path.read_text().splitlines()


def _count_code_and_comments(lines: list[str]) -> tuple[int, int]:
    code, comment = 0, 0
    in_docstring = False
    for line in lines:
        stripped = line.strip()
        if stripped == "":
            continue
        if in_docstring:
            comment += 1
            if '"""' in stripped or "'''" in stripped:
                in_docstring = False
            continue
        if stripped.startswith('"""') or stripped.startswith("'''"):
            comment += 1
            if stripped.count('"""') < 2 and stripped.count("'''") < 2:
                in_docstring = True
            continue
        if stripped.startswith("#"):
            comment += 1
            continue
        code += 1
    return code, comment


def _has_docstring(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text())
        return ast.get_docstring(tree) is not None
    except SyntaxError:
        return False


def _test_file_for_source(src_path: Path) -> Path | None:
    stem = src_path.stem
    rel = src_path.relative_to(SRC_DIR)
    names = [
        REPO_ROOT / "tests" / "unit" / f"test_{rel.with_suffix('').as_posix().replace('/', '_')}.py",
        REPO_ROOT / "tests" / "unit" / f"test_{stem}.py",
        REPO_ROOT / "tests" / "unit" / rel.parent / f"test_{stem}.py",
    ]
    for n in names:
        if n.exists():
            return n
    return None


@pytest.fixture(scope="module")
def file_sizes() -> list[tuple[Path, int, int, int, int]]:
    """Return [(path, total_lines, code_lines, comment_lines, byte_size), ...]"""
    result = []
    for p in SRC_FILES:
        if not p.exists():
            continue
        lines = _read_lines(p)
        code, comment = _count_code_and_comments(lines)
        result.append((p, len(lines), code, comment, p.stat().st_size))
    return result


@pytest.fixture(scope="module")
def file_sizes_map(file_sizes) -> dict[str, tuple[int, int, int, int]]:
    return {p.name: (total, code, comment, size) for p, total, code, comment, size in file_sizes}


# ---------------------------------------------------------------------------
# count & size ceilings
# ---------------------------------------------------------------------------


def test_at_least_one_source_file(file_sizes):
    assert len(file_sizes) >= 1, "No source files found"


def test_total_source_files_reasonable(file_sizes):
    assert 100 <= len(file_sizes) <= 1500, f"Expected 100-1500 source files, found {len(file_sizes)}"


def test_no_file_exceeds_max_lines(file_sizes):
    violators = [(p, total) for p, total, _, _, _ in file_sizes if total > MAX_FILE_LINES]
    assert not violators, f"Files exceeding {MAX_FILE_LINES} lines:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {total} lines" for p, total in violators
    )


def test_no_file_empty_or_near_empty(file_sizes):
    violators = [(p, total) for p, total, _, _, _ in file_sizes if total < MIN_CODE_LINES and p.name != "__init__.py"]
    assert not violators, f"Files with < {MIN_CODE_LINES} lines (excluding __init__.py):\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {total} lines" for p, total in violators
    )


def test_init_files_under_max(file_sizes):
    violators = [(p, total) for p, total, _, _, _ in file_sizes if p.name == "__init__.py" and total > MAX_INIT_LINES]
    assert not violators, f"__init__.py files exceeding {MAX_INIT_LINES} lines:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {total} lines" for p, total in violators
    )


def test_no_line_exceeds_max_length(file_sizes):
    violators: list[tuple[Path, int, str]] = []
    for p, _, _, _, _ in file_sizes:
        for i, line in enumerate(_read_lines(p), 1):
            if len(line) > MAX_LINE_LENGTH:
                violators.append((p, i, line[:80] + "..."))
    assert not violators, f"Lines exceeding {MAX_LINE_LENGTH} chars:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)}:{ln} — {preview}" for p, ln, preview in violators[:20]
    )


# ---------------------------------------------------------------------------
# comment ratio
# ---------------------------------------------------------------------------


def test_comment_ratio_individual(file_sizes):
    violators = []
    for p, total, _code, comment, _ in file_sizes:
        if total == 0 or p.name == "__init__.py":
            # Docstring-only __init__ files are the canonical namespace-package
            # style in this repo; their "comments" are the module docstring.
            continue
        if comment / total > MAX_COMMENT_RATIO:
            violators.append((p, comment / total))
    assert not violators, f"Files with >{MAX_COMMENT_RATIO:.0%} comment lines:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {ratio:.1%}" for p, ratio in violators
    )


def test_overall_code_to_comment_ratio_healthy(file_sizes):
    total_code = sum(c for _, _, c, _, _ in file_sizes)
    total_comment = sum(co for _, _, _, co, _ in file_sizes)
    total = total_code + total_comment
    if total == 0:
        pytest.skip("No code or comment lines")
    ratio = total_code / total
    assert ratio >= 0.50, f"Code-to-total ratio {ratio:.1%} is below 50% — possible comment bloat"


# ---------------------------------------------------------------------------
# test correspondence
# ---------------------------------------------------------------------------


def test_top5_largest_have_tests(file_sizes):
    by_size = sorted(file_sizes, key=lambda x: x[1], reverse=True)
    top5 = [(p, total) for p, total, _, _, _ in by_size[:5] if p.name != "__init__.py"]
    missing = []
    for p, total in top5:
        tf = _test_file_for_source(p)
        if tf is None:
            missing.append((p, total))
    assert not missing, "Top-5 largest source files lack a test file:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {total} lines" for p, total in missing
    )


def test_top10_largest_have_tests(file_sizes):
    by_size = sorted(file_sizes, key=lambda x: x[1], reverse=True)
    top10 = [(p, total) for p, total, _, _, _ in by_size[:10] if p.name != "__init__.py"]
    missing = [(p, total) for p, total in top10 if _test_file_for_source(p) is None]
    assert len(missing) <= 2, f"At most 2 of top-10 largest may lack tests; {len(missing)} missing:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {total} lines" for p, total in missing
    )


def test_largest_file_has_test(file_sizes):
    by_size = sorted(file_sizes, key=lambda x: x[1], reverse=True)
    if not by_size:
        pytest.skip("No source files")
    largest = by_size[0][0]
    if largest.name == "__init__.py":
        pytest.skip("Largest file is __init__.py")
    tf = _test_file_for_source(largest)
    assert tf is not None, f"Largest file {largest.relative_to(REPO_ROOT)} has NO test file"


def test_test_to_source_ratio_healthy(file_sizes):
    test_dir = REPO_ROOT / "tests" / "unit"
    test_files = list(test_dir.rglob("test_*.py"))
    non_init_src = [p for p, _, _, _, _ in file_sizes if p.name != "__init__.py"]
    if not non_init_src:
        pytest.skip("No non-init source files")
    ratio = len(test_files) / len(non_init_src)
    assert ratio >= 0.5, (
        f"Test-to-source ratio {ratio:.2f} is below 0.5 ({len(test_files)} tests for {len(non_init_src)} source files)"
    )


# ---------------------------------------------------------------------------
# per-directory balance
# ---------------------------------------------------------------------------


def test_directory_sizes_reasonable(file_sizes):
    by_dir: dict[str, int] = {}
    for p, total, _, _, _ in file_sizes:
        d = str(p.parent.relative_to(SRC_DIR))
        by_dir[d] = by_dir.get(d, 0) + total
    violators = [(d, total) for d, total in by_dir.items() if total > MAX_DIR_LINES]
    assert not violators, f"Directories exceeding {MAX_DIR_LINES} total lines:\n" + "\n".join(
        f"  {d} — {total} lines ({total // MAX_DIR_LINES}x threshold)" for d, total in violators
    )


def test_no_file_all_comment_no_code(file_sizes):
    violators = [
        (p, total, code, comment)
        for p, total, code, comment, _ in file_sizes
        if total > 10 and code == 0 and comment > 0 and p.name != "__init__.py"
    ]
    assert not violators, "Files with >10 lines, zero code, all comments:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {total} lines, {comment} comment" for p, total, _, comment in violators
    )


def test_module_docstrings_present(file_sizes):
    non_init = [p for p, _, _, _, _ in file_sizes if p.name != "__init__.py" and p.suffix == ".py"]
    missing = [p for p in non_init if not _has_docstring(p)]
    if non_init:
        ratio = len(missing) / len(non_init)
        assert ratio <= 0.15, (
            f">15% of source files lack module docstrings: {len(missing)}/{len(non_init)} ({ratio:.1%})\n"
            + "\n".join(f"  {p.relative_to(REPO_ROOT)}" for p in missing[:15])
        )


def test_total_source_lines_tracked(file_sizes):
    total = sum(line for _, line, _, _, _ in file_sizes)
    assert total > 0
    assert total < 400000, f"Total source lines {total} exceeds sanity check of 400000 — review if intentional"


def test_file_sizes_on_disk_reasonable(file_sizes):
    violators = [(p, size) for p, _, _, _, size in file_sizes if size > 300_000]
    assert not violators, "Files exceeding 300 KB on disk:\n" + "\n".join(
        f"  {p.relative_to(REPO_ROOT)} — {size / 1024:.0f} KB" for p, size in violators
    )
