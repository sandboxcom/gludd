"""Structural tests for filestore/store.py — FileStore class."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from general_ludd.filestore.store import FileStore


class TestFileStoreInit:
    def test_import_is_clean_with_deprecations_as_errors(self) -> None:
        script = (
            "import tempfile\n"
            "from general_ludd.filestore.store import FileStore\n"
            "with tempfile.TemporaryDirectory() as root:\n"
            "    FileStore(root_path=root).close()\n"
        )
        result = subprocess.run(
            [sys.executable, "-W", "error::DeprecationWarning", "-c", script],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    def test_init_with_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            assert store.root_path == tmp
            assert store.exists("/")

    def test_init_default_root(self) -> None:
        store = FileStore()
        assert ".local/share/general-ludd/filestore" in store.root_path

    def test_init_with_overlay_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp, overlay_path=tmp)
            assert store._overlay_fs is not None

    def test_init_overlay_nonexistent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            overlay = Path(tmp) / "nope"
            store = FileStore(root_path=tmp, overlay_path=str(overlay))
            assert store._overlay_fs is None


class TestFileStoreIsBinaryPath:
    def test_binaries_root(self) -> None:
        assert FileStore._is_binary_path("binaries") is True

    def test_binaries_subpath(self) -> None:
        assert FileStore._is_binary_path("binaries/openbao") is True

    def test_non_binary_path(self) -> None:
        assert FileStore._is_binary_path("config/app.yml") is False

    def test_leading_slash_normalized(self) -> None:
        assert FileStore._is_binary_path("/binaries/tool") is True

    def test_similar_but_not_binary(self) -> None:
        assert FileStore._is_binary_path("binaries_archive") is False


class TestFileStoreOverlayOwns:
    def test_no_overlay_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            assert store._overlay_owns("any/path") is False

    def test_binary_path_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp, overlay_path=tmp)
            store.write_text("binaries/tool", b"data".decode())
            assert store._overlay_owns("binaries/tool") is False

    def test_main_store_has_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("existing.txt", "hello")
            assert store._overlay_owns("existing.txt") is False


class TestFileStoreResolvePath:
    def test_resolves_to_main_store_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            fs, path = store._resolve_path("data.txt")
            assert fs is store._fs
            assert path == "data.txt"


class TestFileStoreWriteReadRoundTrip:
    def test_text_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("nested/deep/file.txt", "content")
            assert store.read_text("nested/deep/file.txt") == "content"

    def test_bytes_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            data = b"\x00\x01\x02\x03\xff\xfe"
            store.write_bytes("raw.bin", data)
            assert store.read_bytes("raw.bin") == data

    def test_write_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("f.txt", "v1")
            store.write_text("f.txt", "v2")
            assert store.read_text("f.txt") == "v2"


class TestFileStoreExistsIsDir:
    def test_exists_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("f.txt", "x")
            assert store.exists("f.txt") is True

    def test_exists_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            assert store.exists("nope.txt") is False

    def test_is_dir_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("mydir")
            assert store.is_dir("mydir") is True

    def test_is_dir_false_for_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("f.txt", "x")
            assert store.is_dir("f.txt") is False


class TestFileStoreRemove:
    def test_remove_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("rm.txt", "bye")
            store.remove("rm.txt")
            assert not store.exists("rm.txt")

    def test_remove_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            with pytest.raises(FileNotFoundError):
                store.remove("nope.txt")

    def test_remove_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("a/b")
            store.write_text("a/b/c.txt", "x")
            store.remove("a")
            assert not store.exists("a")


class TestFileStoreTree:
    def test_tree_includes_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("a.txt", "a")
            tree = store.tree()
            names = {e["name"] for e in tree}
            assert "a.txt" in names

    def test_tree_includes_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("subdir")
            tree = store.tree()
            names = {e["name"] for e in tree}
            assert "subdir" in names

    def test_tree_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("d1/d2/f.txt", "x")
            tree = store.tree()
            flattened = {e["path"] for e in tree}
            assert len(flattened) >= 2


class TestFileStoreGetInfo:
    def test_get_info_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("info.txt", "data")
            info = store.get_info("info.txt")
            assert info["name"] == "info.txt"
            assert info["is_dir"] is False
            assert "size" in info
            assert "modified" in info


class TestFileStoreCopyMove:
    def test_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("src.txt", "original")
            store.copy("src.txt", "dst.txt")
            assert store.read_text("dst.txt") == "original"

    def test_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("old.txt", "data")
            store.move("old.txt", "new.txt")
            assert not store.exists("old.txt")
            assert store.read_text("new.txt") == "data"


class TestFileStoreListDir:
    def test_list_dir_sorts_dirs_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("z_dir")
            store.write_text("a_file.txt", "x")
            entries = store.list_dir("/")
            types = ["dir" if e["is_dir"] else "file" for e in entries]
            first_dir = next((i for i, t in enumerate(types) if t == "dir"), None)
            first_file = next((i for i, t in enumerate(types) if t == "file"), None)
            if first_dir is not None and first_file is not None:
                assert first_dir < first_file

    def test_list_dir_empty_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            entries = store.list_dir("/")
            assert entries == []


class TestFileStoreMakedirs:
    def test_makedirs_creates_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("x/y/z")
            assert store.is_dir("x/y/z")

    def test_makedirs_recreate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("adir")
            store.makedirs("adir")


class TestFileStoreClose:
    def test_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("f.txt", "x")
            store.close()
