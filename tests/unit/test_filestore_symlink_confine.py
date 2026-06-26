"""Tests for FileStore symlink confinement.

write_text/write_bytes/remove now confine the FULL path (not just its dirname),
so a symlink planted inside the store root that points OUTSIDE it cannot be used
to write to / delete a file outside the store. The read path already did this;
these tests pin that writes/removes match, and that normal in-store ops still
work.
"""

from __future__ import annotations

import os

import pytest

from general_ludd.filestore.store import FileStore


def test_write_text_through_escape_symlink_raises(tmp_path):
    store = FileStore(root_path=str(tmp_path))
    outside_target = tmp_path.parent / "outside_file_wt.txt"
    outside_target.write_text("untouched_original")
    os.symlink(str(outside_target), str(tmp_path / "escape_link"))

    with pytest.raises(PermissionError, match="escapes store root"):
        store.write_text("escape_link", "malicious_write")

    assert outside_target.read_text() == "untouched_original"
    outside_target.unlink()


def test_write_bytes_through_escape_symlink_raises(tmp_path):
    store = FileStore(root_path=str(tmp_path))
    outside_target = tmp_path.parent / "outside_file_wb.bin"
    outside_target.write_bytes(b"protected_original_data")
    os.symlink(str(outside_target), str(tmp_path / "escape_bin"))

    with pytest.raises(PermissionError, match="escapes store root"):
        store.write_bytes("escape_bin", b"malicious_bytes")

    assert outside_target.read_bytes() == b"protected_original_data"
    outside_target.unlink()


def test_remove_through_escape_symlink_raises(tmp_path):
    store = FileStore(root_path=str(tmp_path))
    outside_target = tmp_path.parent / "outside_file_rm.txt"
    outside_target.write_text("precious_content")
    os.symlink(str(outside_target), str(tmp_path / "escape_remove"))

    with pytest.raises(PermissionError, match="escapes store root"):
        store.remove("escape_remove")

    assert outside_target.exists()
    assert outside_target.read_text() == "precious_content"
    outside_target.unlink()


def test_normal_in_store_write_read_remove_not_blocked(tmp_path):
    store = FileStore(root_path=str(tmp_path))

    store.write_text("normal/file.txt", "hello world")
    assert store.read_text("normal/file.txt") == "hello world"

    store.write_bytes("normal/binary.bin", b"binary_data_123")
    assert store.read_bytes("normal/binary.bin") == b"binary_data_123"

    store.remove("normal/file.txt")
    assert not store.exists("normal/file.txt")
    store.remove("normal/binary.bin")
    assert not store.exists("normal/binary.bin")
