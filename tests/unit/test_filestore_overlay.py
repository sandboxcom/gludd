"""Tests for FileStore config overlay — ~/.config/gludd/fs/ overlays filestore."""

from __future__ import annotations

import tempfile
from pathlib import Path


class TestConfigOverlay:
    def test_overlay_reads_from_config_first(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            store.write_text("base/file.txt", "from_store")

            overlay_file = Path(overlay_tmp) / "base" / "file.txt"
            overlay_file.parent.mkdir(parents=True, exist_ok=True)
            overlay_file.write_text("from_overlay")

            content = store.read_text("base/file.txt")
            assert content == "from_overlay"

    def test_overlay_falls_back_to_store(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            store.write_text("only_in_store.txt", "store_data")

            content = store.read_text("only_in_store.txt")
            assert content == "store_data"

    def test_overlay_only_file_not_in_store(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)

            overlay_file = Path(overlay_tmp) / "overlay_only.txt"
            overlay_file.write_text("overlay_data")

            content = store.read_text("overlay_only.txt")
            assert content == "overlay_data"

    def test_overlay_exists(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            store.write_text("store_file.txt", "x")

            overlay_file = Path(overlay_tmp) / "overlay_file.txt"
            overlay_file.write_text("y")

            assert store.exists("store_file.txt")
            assert store.exists("overlay_file.txt")
            assert not store.exists("nonexistent.txt")

    def test_overlay_list_dir_merges(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            store.write_text("mydir/store_file.txt", "s")
            store.makedirs("mydir")

            overlay_dir = Path(overlay_tmp) / "mydir"
            overlay_dir.mkdir(parents=True, exist_ok=True)
            (overlay_dir / "overlay_file.txt").write_text("o")

            entries = store.list_dir("mydir")
            names = {e["name"] for e in entries}
            assert "store_file.txt" in names
            assert "overlay_file.txt" in names

    def test_overlay_list_dir_dedup(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            store.write_text("dup/file.txt", "store_version")

            overlay_dir = Path(overlay_tmp) / "dup"
            overlay_dir.mkdir(parents=True, exist_ok=True)
            (overlay_dir / "file.txt").write_text("overlay_version")

            entries = store.list_dir("dup")
            names = [e["name"] for e in entries]
            assert names.count("file.txt") == 1

    def test_overlay_is_dir(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            store.makedirs("store_dir")

            overlay_dir = Path(overlay_tmp) / "overlay_dir"
            overlay_dir.mkdir(parents=True)

            assert store.is_dir("store_dir")
            assert store.is_dir("overlay_dir")

    def test_overlay_default_path(self):
        from general_ludd.filestore.store import FileStore

        store = FileStore()
        assert "config/gludd/fs" in store._overlay_path.replace("\\", "/")

    def test_overlay_custom_path(self):
        from general_ludd.filestore.store import FileStore

        store = FileStore(overlay_path="/custom/overlay")
        assert "/custom/overlay" in store._overlay_path

    def test_overlay_write_goes_to_store_not_overlay(self):
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            store.write_text("new_file.txt", "written")

            overlay_file = Path(overlay_tmp) / "new_file.txt"
            assert not overlay_file.exists()

            store_file = Path(store_tmp) / "new_file.txt"
            assert store_file.read_text() == "written"
