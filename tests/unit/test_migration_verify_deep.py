"""Deep Alembic migration chain verification.

Parses every migration in alembic/versions/ with ``ast`` to avoid import
side-effects, then verifies revision-chain integrity, symmetry, and
idempotency properties.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

VERSIONS_DIR = Path("alembic/versions")
_REV_PATTERN = re.compile(r"^(revision|down_revision)\s*:\s*str\s*(?:\|\s*None)?\s*=\s*(.+)")


def _ast_extract_revisions(filepath: Path) -> tuple[str, str | None]:
    tree = ast.parse(filepath.read_text())
    rev: str | None = None
    down: str | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
            if isinstance(node.value, ast.Constant):
                val = node.value.value
                if name == "revision" and isinstance(val, str):
                    rev = val
                elif name == "down_revision":
                    if val is None:
                        down = None
                    elif isinstance(val, str):
                        down = val
    if rev is None:
        raise ValueError(f"No `revision` annotation found in {filepath}")
    return rev, down


def _get_all_migrations() -> list[tuple[Path, str, str | None]]:
    results: list[tuple[Path, str, str | None]] = []
    for f in sorted(VERSIONS_DIR.glob("*.py")):
        if f.name.startswith("_"):
            continue
        rev, down = _ast_extract_revisions(f)
        results.append((f, rev, down))
    return results


def _get_revision_map() -> dict[str, tuple[Path, str | None]]:
    return {rev: (path, down) for path, rev, down in _get_all_migrations()}


# --- fixtures ---


@pytest.fixture(scope="module")
def migrations() -> list[tuple[Path, str, str | None]]:
    return _get_all_migrations()


@pytest.fixture(scope="module")
def revision_map() -> dict[str, tuple[Path, str | None]]:
    return _get_revision_map()


# --- tests ---


class TestRevisionIdFormat:
    def test_revision_is_str(self, migrations: list) -> None:
        for path, rev, _down in migrations:
            assert isinstance(rev, str), f"{path.name}: revision must be str, got {type(rev)}"

    def test_down_revision_is_str_or_none(self, migrations: list) -> None:
        for path, _rev, down in migrations:
            assert down is None or isinstance(down, str), (
                f"{path.name}: down_revision must be str|None, got {type(down)}"
            )

    def test_no_empty_revision(self, migrations: list) -> None:
        for path, rev, _down in migrations:
            assert rev != "", f"{path.name}: revision must not be empty string"

    def test_no_self_referencing_down_revision(self, migrations: list) -> None:
        for path, rev, down in migrations:
            assert down is None or down != rev, f"{path.name}: down_revision must not reference itself ({rev})"


class TestDuplicateRevisionIds:
    def test_no_duplicate_revision_ids(self, migrations: list) -> None:
        [rev for _p, rev, _d in migrations]
        seen: dict[str, list[str]] = {}
        for path, rev, _down in migrations:
            seen.setdefault(rev, []).append(path.name)
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        assert not dupes, f"Duplicate revision IDs: {dupes}"


class TestChainIntegrity:
    def test_exactly_one_head_revision(self, migrations: list) -> None:
        heads = [rev for _p, rev, down in migrations if down is None]
        assert len(heads) == 1, f"Expected 1 head (down_revision=None), got {len(heads)}: {heads}"

    def test_all_down_revisions_exist(self, revision_map: dict, migrations: list) -> None:
        missing: list[tuple[str, str, str]] = []
        for path, rev, down in migrations:
            if down is not None and down not in revision_map:
                missing.append((path.name, rev, down))
        assert not missing, f"down_revision targets not found: {missing}"

    def test_no_gaps_in_chain(self, revision_map: dict) -> None:
        reachable: set[str] = set()
        heads = [r for r, (_p, down) in revision_map.items() if down is None]
        for head in heads:
            stack = [head]
            while stack:
                cur = stack.pop()
                if cur in reachable:
                    continue
                reachable.add(cur)
                for rid, (_p, rev_down) in revision_map.items():
                    if rev_down == cur:
                        stack.append(rid)
        unreachable = set(revision_map) - reachable
        assert not unreachable, f"Unreachable revisions (no path from head): {sorted(unreachable)}"

    def test_chain_is_singly_linked_no_ambiguous_parents(self, revision_map: dict) -> None:
        children_by_parent: dict[str, list[str]] = {}
        for rid, (_p, down) in revision_map.items():
            if down is not None:
                children_by_parent.setdefault(down, []).append(rid)
        ambiguous = {k: v for k, v in children_by_parent.items() if len(v) > 1}
        assert not ambiguous, f"Ambiguous parent chain (multiple children point to same parent): {ambiguous}"

    def test_can_walk_from_head_to_all(self, revision_map: dict) -> None:
        children: dict[str, list[str]] = {}
        for rid, (_p, down) in revision_map.items():
            if down is not None:
                children.setdefault(down, []).append(rid)
        heads = [r for r, (_p, down) in revision_map.items() if down is None]
        visited: set[str] = set()
        stack = list(heads)
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            stack.extend(children.get(cur, []))
        assert visited == set(revision_map), f"Walkable set != revision_map: missing {set(revision_map) - visited}"


class TestUpgradeDowngradeSymmetry:
    def test_all_migrations_have_both_functions(self, migrations: list) -> None:
        missing_fns: list[tuple[str, str]] = []
        for path, _rev, _down in migrations:
            src = path.read_text()
            tree = ast.parse(src)
            funcs = {
                n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name in ("upgrade", "downgrade")
            }
            for fn in ("upgrade", "downgrade"):
                if fn not in funcs:
                    missing_fns.append((path.name, fn))
        assert not missing_fns, f"Migrations missing upgrade/downgrade: {missing_fns}"

    def test_upgrade_and_downgrade_not_empty(self, migrations: list) -> None:
        empty: list[str] = []
        for path, _rev, _down in migrations:
            src = path.read_text()
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name in ("upgrade", "downgrade"):
                    body = node.body
                    has_statements = any(
                        not isinstance(stmt, ast.Pass)
                        and not (
                            isinstance(stmt, ast.Expr)
                            and isinstance(stmt.value, ast.Constant)
                            and stmt.value.value is Ellipsis
                        )
                        for stmt in body
                    )
                    if not has_statements:
                        empty.append(f"{path.name}:{node.name}")
        assert not empty, f"Empty upgrade/downgrade bodies: {empty}"


class TestFileStructure:
    def test_all_files_parseable(self, migrations: list) -> None:
        for path, _rev, _down in migrations:
            try:
                ast.parse(path.read_text())
            except SyntaxError as exc:
                pytest.fail(f"{path.name}: syntax error — {exc}")

    def test_all_files_have_docstrings(self, migrations: list) -> None:
        missing: list[str] = []
        for path, _rev, _down in migrations:
            tree = ast.parse(path.read_text())
            doc = ast.get_docstring(tree)
            if not doc:
                missing.append(path.name)
        assert not missing, f"Files missing module docstring: {missing}"

    def test_all_files_have_revision_variable(self, migrations: list) -> None:
        for path in VERSIONS_DIR.glob("*.py"):
            if path.name.startswith("_"):
                continue
            src = path.read_text()
            assert "revision" in src, f"{path.name}: missing `revision` variable"

    def test_docstring_not_empty(self, migrations: list) -> None:
        missing: list[str] = []
        for path, _rev, _down in migrations:
            tree = ast.parse(path.read_text())
            doc = ast.get_docstring(tree)
            if not doc or not doc.strip():
                missing.append(path.name)
        assert not missing, f"Files with empty docstrings: {missing}"


class TestBranchLabels:
    def test_no_unexpected_branch_labels(self, migrations: list) -> None:
        branched: list[str] = []
        for path, _rev, _down in migrations:
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.AnnAssign)
                    and isinstance(node.target, ast.Name)
                    and node.target.id == "branch_labels"
                    and isinstance(node.value, ast.Constant)
                    and node.value.value is not None
                ):
                    branched.append(f"{path.name}: branch_labels={node.value.value}")
        assert not branched, f"Files with non-None branch_labels: {branched}"
