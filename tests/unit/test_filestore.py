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

    def test_get_binary_path_found(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)
            boot.store_binary("openbao", b"openbao-data")
            path = boot.get_binary_path("openbao")
            assert path is not None
            assert "openbao" in path

    def test_get_binary_path_not_found(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)
            path = boot.get_binary_path("nonexistent")
            assert path is None

    def test_is_platform_available(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        result = boot.is_platform_available("openbao")
        assert isinstance(result, bool)

    def test_check_openbao_in_store_found(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)
            boot.store_binary("openbao", b"openbao-data")
            assert boot.check_openbao_in_store() is True

    def test_check_openbao_in_store_not_found(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store, bundled_binaries_dir=None)
            assert boot.check_openbao_in_store() is False

    def test_get_platform_info_amd64(self):
        from unittest.mock import patch

        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch("general_ludd.filestore.bootstrap.platform.machine", return_value="x86_64"):
            info = boot.get_platform_info()
            assert info["arch"] == "amd64"

    def test_get_platform_info_unknown_arch(self):
        from unittest.mock import patch

        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch("general_ludd.filestore.bootstrap.platform.machine", return_value="riscv64"):
            info = boot.get_platform_info()
            assert info["arch"] == "riscv64"

    def test_sync_bundled_to_filestore(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            (bundled / "openbao").write_bytes(b"fake-bao")
            (bundled / "opentofu").write_bytes(b"fake-tofu")

            store_dir = Path(tmp) / "store"
            store_dir.mkdir()
            store = FileStore(root_path=str(store_dir))
            boot = BinaryBootstrapper(store=store, bundled_binaries_dir=str(bundled))
            synced = boot.sync_bundled_to_filestore()
            assert "openbao" in synced
            assert "opentofu" in synced
            assert store.exists("binaries/openbao")

    def test_sync_bundled_skips_existing(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            (bundled / "openbao").write_bytes(b"fake-bao")

            store_dir = Path(tmp) / "store"
            store_dir.mkdir()
            store = FileStore(root_path=str(store_dir))
            boot = BinaryBootstrapper(store=store, bundled_binaries_dir=str(bundled))
            boot.store_binary("openbao", b"existing")
            synced = boot.sync_bundled_to_filestore()
            assert "openbao" not in synced

    def test_download_uses_bundled(self):
        import asyncio

        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            bundled.mkdir()
            (bundled / "openbao").write_bytes(b"bundled-bao")

            store_dir = Path(tmp) / "store"
            store_dir.mkdir()
            store = FileStore(root_path=str(store_dir))
            boot = BinaryBootstrapper(store=store, bundled_binaries_dir=str(bundled))
            result = asyncio.run(boot.download("openbao"))
            assert result is True
            assert store.exists("binaries/openbao")

    def test_download_returns_false_when_no_url(self):
        import asyncio
        from unittest.mock import patch

        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)
            with patch.object(boot, "get_download_url", return_value=None), \
                 patch.object(boot, "get_bundled_binary_path", return_value=None):
                result = asyncio.run(boot.download("fake"))
            assert result is False

    def test_download_openbao_delegates(self):
        import asyncio
        from unittest.mock import AsyncMock, patch

        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)
            mock_dl = AsyncMock(return_value=True)
            with patch.object(boot, "download", mock_dl):
                result = asyncio.run(boot.download_openbao())
            assert result is True

    def test_download_all(self):
        import asyncio
        from unittest.mock import patch

        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)

            async def fake_download(name):
                return name == "openbao"

            with patch.object(boot, "download", side_effect=fake_download):
                results = asyncio.run(boot.download_all())
            assert results["openbao"] is True
            assert results["opentofu"] is False


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
