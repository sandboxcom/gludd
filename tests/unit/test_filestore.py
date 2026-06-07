"""Tests for FileStore — virtual filesystem-based artifact and binary storage."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest


class TestFileStore:
    def test_init_creates_store_dir(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            assert Path(tmp).is_dir()
            assert store.exists("/")

    def test_write_and_read_text(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("config/test.yml", "key: value")
            content = store.read_text("config/test.yml")
            assert content == "key: value"

    def test_write_and_read_bytes(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            data = b"\x00\x01\x02\x03"
            store.write_bytes("binaries/tool", data)
            result = store.read_bytes("binaries/tool")
            assert result == data

    def test_list_directory(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("artifacts/a.txt", "a")
            store.write_text("artifacts/b.txt", "b")
            store.write_text("config/c.yml", "c")

            entries = store.list_dir("artifacts")
            names = {e["name"] for e in entries}
            assert "a.txt" in names
            assert "b.txt" in names

            entries = store.list_dir("/")
            dirs = {e["name"] for e in entries if e["is_dir"]}
            assert "artifacts" in dirs
            assert "config" in dirs

    def test_list_empty_directory(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("empty")
            entries = store.list_dir("empty")
            assert entries == []

    def test_exists(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("data.txt", "hello")
            assert store.exists("data.txt")
            assert not store.exists("nonexistent.txt")

    def test_is_dir(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("mydir")
            store.write_text("mydir/file.txt", "content")
            assert store.is_dir("mydir")
            assert not store.is_dir("mydir/file.txt")

    def test_remove_file(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("remove_me.txt", "bye")
            assert store.exists("remove_me.txt")
            store.remove("remove_me.txt")
            assert not store.exists("remove_me.txt")

    def test_remove_directory(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("remove_dir")
            store.write_text("remove_dir/f.txt", "x")
            store.remove("remove_dir")
            assert not store.exists("remove_dir")

    def test_remove_nonexistent_raises(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            with pytest.raises(FileNotFoundError):
                store.remove("nonexistent")

    def test_makedirs_creates_nested(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.makedirs("a/b/c")
            assert store.is_dir("a/b/c")

    def test_get_info(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("info.txt", "data")
            info = store.get_info("info.txt")
            assert info["name"] == "info.txt"
            assert info["is_dir"] is False
            assert "size" in info
            assert "modified" in info

    def test_copy_file(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("src.txt", "original")
            store.copy("src.txt", "dst.txt")
            assert store.read_text("dst.txt") == "original"

    def test_move_file(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("old.txt", "data")
            store.move("old.txt", "new.txt")
            assert not store.exists("old.txt")
            assert store.read_text("new.txt") == "data"

    def test_tree_returns_full_listing(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("a.txt", "a")
            store.write_text("dir/b.txt", "b")
            store.makedirs("empty")

            tree = store.tree()
            assert len(tree) >= 2
            paths = {e["path"] for e in tree}
            assert "a.txt" in paths or "/a.txt" in paths

    def test_root_defaults_to_xdg(self):
        from general_ludd.filestore.store import FileStore

        store = FileStore()
        assert ".local/share/general-ludd/filestore" in store.root_path.replace("\\", "/")

    def test_write_text_overwrites(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            store.write_text("f.txt", "v1")
            store.write_text("f.txt", "v2")
            assert store.read_text("f.txt") == "v2"


class TestBinaryBootstrapper:
    def test_detect_podman_on_path(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        result = boot.detect_binary("podman")
        assert result in (True, False)

    def test_detect_nonexistent_binary(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        result = boot.detect_binary("nonexistent_binary_xyz")
        assert result is False

    def test_bootstrap_checks_os(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        info = boot.get_platform_info()
        assert "os" in info
        assert "arch" in info
        assert len(info["os"]) > 0
        assert len(info["arch"]) > 0

    def test_openbao_download_url(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        url = boot.get_download_url("openbao")
        if url is not None:
            assert "openbao" in url.lower() or "bao" in url.lower()

    def test_store_binary(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)
            boot.store_binary("test-bin", b"fake binary data")
            assert store.exists("binaries/test-bin")

    def test_list_stored_binaries(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)
            boot.store_binary("podman", b"podman-data")
            boot.store_binary("openbao", b"openbao-data")

            bins = boot.list_binaries()
            names = {b["name"] for b in bins}
            assert "podman" in names
            assert "openbao" in names


class TestFilestoreCLI:
    def test_cli_parsing(self):
        import sys
        from unittest.mock import patch

        with patch.object(sys, "argv", ["gludd", "filestore", "list"]):
            try:
                import general_ludd.cli as cli_mod

                cli_mod.main()
            except SystemExit:
                pass
