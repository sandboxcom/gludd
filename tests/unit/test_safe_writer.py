"""Unit tests for ``AtomicSafeWriter`` — concrete ``SafeWriter`` for self-update.

The writer performs the filesystem writes that ``UpdateApplier`` delegates
out via the ``SafeWriter`` Protocol (``self_update.applier``). It must:

* be atomic (temp-file + ``os.replace`` on POSIX) so a partial write is
  never observable,
* confine every target to a configured ``workspace_root`` (escape attempts
  via ``../``, absolute paths, or symlinks must raise ``ValueError``),
* create parent directories when missing,
* return the resolved written path as a string,
* structurally satisfy the ``SafeWriter`` Protocol.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from general_ludd.self_update.applier import SafeWriter
from general_ludd.self_update.safe_writer import AtomicSafeWriter

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_write_creates_file_with_content(tmp_path: Path) -> None:
    writer = AtomicSafeWriter(tmp_path)

    returned = writer.write("config.yml", "key: value\n")

    written = tmp_path / "config.yml"
    assert written.read_text() == "key: value\n"
    assert returned == str(written.resolve())


def test_write_returns_resolved_path(tmp_path: Path) -> None:
    writer = AtomicSafeWriter(tmp_path)

    returned = writer.write("a/b/c.txt", "x")

    # Returned path is absolute and resolved (no '..' remaining).
    assert os.path.isabs(returned)
    assert ".." not in returned
    assert returned == str((tmp_path / "a" / "b" / "c.txt").resolve())


def test_write_creates_parent_directories(tmp_path: Path) -> None:
    writer = AtomicSafeWriter(tmp_path)

    target_rel = "deep/nested/dir/roles.yml"
    writer.write(target_rel, "---\n")

    assert (tmp_path / target_rel).read_text() == "---\n"


def test_write_overwrites_existing_file_atomically(tmp_path: Path) -> None:
    target = tmp_path / "existing.yml"
    target.write_text("old: data\n")

    writer = AtomicSafeWriter(tmp_path)
    writer.write("existing.yml", "new: data\n")

    assert target.read_text() == "new: data\n"


def test_write_accepts_absolute_path_within_root(tmp_path: Path) -> None:
    writer = AtomicSafeWriter(tmp_path)
    abs_target = tmp_path / "inside.yml"

    returned = writer.write(str(abs_target), "v: 1\n")

    assert abs_target.read_text() == "v: 1\n"
    assert returned == str(abs_target.resolve())


# ---------------------------------------------------------------------------
# Confinement — escape attempts must raise
# ---------------------------------------------------------------------------


def test_write_rejects_dotdot_traversal(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    writer = AtomicSafeWriter(root)

    with pytest.raises(ValueError, match="workspace root"):
        writer.write("../outside/evil.yml", "x")

    # Nothing was written.
    assert not list(outside.iterdir())


def test_write_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    writer = AtomicSafeWriter(root)

    with pytest.raises(ValueError, match="workspace root"):
        writer.write(str(outside / "evil.yml"), "x")

    assert not list(outside.iterdir())


def test_write_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    # Symlink inside root pointing at the outside file.
    link = root / "link.yml"
    link.symlink_to(outside)
    writer = AtomicSafeWriter(root)

    with pytest.raises(ValueError, match="workspace root"):
        writer.write("link.yml", "rewritten")

    # Original outside file untouched.
    assert outside.read_text() == "secret"


# ---------------------------------------------------------------------------
# Atomicity — partial/temp content must never be observable
# ---------------------------------------------------------------------------


def test_write_leaves_no_temp_file_on_success(tmp_path: Path) -> None:
    writer = AtomicSafeWriter(tmp_path)
    writer.write("done.yml", "k: v\n")

    # No stray temp files in the directory.
    names = [p.name for p in tmp_path.iterdir()]
    assert names == ["done.yml"]


def test_write_cleans_up_temp_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = AtomicSafeWriter(tmp_path)

    # Force os.replace to blow up so we exercise the cleanup branch.
    def boom(src: str, dst: str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("general_ludd.self_update.safe_writer.os.replace", boom)

    with pytest.raises(OSError):
        writer.write("will_fail.yml", "k: v\n")

    # Neither the final file nor a leftover temp file should exist.
    names = [p.name for p in tmp_path.iterdir()]
    assert "will_fail.yml" not in names
    assert not any(n.endswith(".tmp") for n in names)


# ---------------------------------------------------------------------------
# Protocol conformance — must structurally satisfy SafeWriter
# ---------------------------------------------------------------------------


def test_atomic_safe_writer_satisfies_safewriter_protocol() -> None:
    # SafeWriter is @runtime_checkable; isinstance verifies the write method
    # exists with the right name. (Static structural match is also asserted
    # at typecheck time when UpdateApplier accepts it.)
    assert isinstance(AtomicSafeWriter(Path("/tmp")), SafeWriter)


def test_writer_constructable_from_string_root(tmp_path: Path) -> None:
    # Constructor must accept str for ergonomic daemon wiring.
    writer = AtomicSafeWriter(str(tmp_path))
    writer.write("ok.yml", "k: v\n")
    assert (tmp_path / "ok.yml").read_text() == "k: v\n"
