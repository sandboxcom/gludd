"""Clean scoped Gludd artifacts and pytest-owned garbage.

The default ``pytest-of-<user>`` root is shared by every checkout and pytest
process for that user.  It is never an ownership boundary, so this cleaner must
not remove it (or any live ``pytest-N`` child) wholesale.  Pytest's atomically
renamed ``garbage-*`` children are the only safe reclaimable entries there.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import stat
from pathlib import Path

TMP_GLOBS = (
    "gludd-audit-e2e-*",
    "gludd-collect-output.txt",
    "gludd-gate-refresh-test.log",
    "gludd-iso-*",
    "gludd-winfix*-gate.log",
    "gludd-test-gate.txt",
    "gludd-stop-state.json",
)
TMP_EXACT: tuple[Path, ...] = ()


def _resolve(path: Path) -> Path:
    return path.resolve(strict=False)


def _allowed_roots() -> list[Path]:
    roots = [_resolve(Path("/tmp"))]
    home = os.environ.get("HOME")
    if home:
        roots.append(_resolve(Path(home) / "tmp"))
    return roots


def _within_allowed_root(path: Path) -> bool:
    resolved = _resolve(path)
    return any(resolved == root or root in resolved.parents for root in _allowed_roots())


def _pytest_roots() -> list[Path]:
    roots = [Path("/tmp")]
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        roots.append(Path(tmpdir))
    home = os.environ.get("HOME")
    if home:
        roots.append(Path(home) / "tmp")
    return roots


def _candidates() -> list[Path]:
    found: list[Path] = []
    for path in TMP_EXACT:
        found.append(path)
    for pattern in TMP_GLOBS:
        found.extend(Path("/tmp").glob(pattern))
    for root in _pytest_roots():
        if not _within_allowed_root(root):
            continue
        for pytest_root in root.glob("pytest-of-*"):
            if pytest_root.is_symlink() or not pytest_root.is_dir():
                continue
            found.extend(pytest_root.glob("garbage-*"))
    return found


def _make_writable_tree(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, stat.S_IRWXU)
    for root, dirs, files in os.walk(path):
        root_path = Path(root)
        with contextlib.suppress(OSError):
            os.chmod(root_path, stat.S_IRWXU)
        for name in dirs:
            child = root_path / name
            if child.is_symlink():
                continue
            with contextlib.suppress(OSError):
                os.chmod(child, stat.S_IRWXU)
        for name in files:
            child = root_path / name
            if child.is_symlink():
                continue
            with contextlib.suppress(OSError):
                os.chmod(child, stat.S_IRUSR | stat.S_IWUSR)


def _retry_with_chmod(func, path_str: str, _exc_info) -> None:
    path = Path(path_str)
    if not path.is_symlink():
        with contextlib.suppress(OSError):
            os.chmod(path, stat.S_IRWXU)
    func(path_str)


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        _make_writable_tree(path)
        shutil.rmtree(path, onerror=_retry_with_chmod)


def main() -> int:
    removed = 0
    skipped = 0
    failed = 0
    seen: set[Path] = set()
    for candidate in _candidates():
        resolved = _resolve(candidate)
        if resolved in seen:
            continue
        seen.add(resolved)
        if not _within_allowed_root(candidate):
            skipped += 1
            continue
        if not candidate.exists() and not candidate.is_symlink():
            continue
        try:
            _remove(candidate)
            removed += 1
        except OSError as exc:
            failed += 1
            print(f"clean-tmp failed path={candidate} error={exc}")
    print(f"clean-tmp removed={removed} skipped={skipped} failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
