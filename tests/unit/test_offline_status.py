"""Tests for offline gludd status — works without a running daemon."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


class TestOfflineStatus:
    def test_gather_local_environment(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert "version" in info
        assert isinstance(info["version"], str)
        assert "python_version" in info
        assert "platform" in info
        assert "cwd" in info

    def test_gather_config_directory(self):
        from general_ludd.cli import _gather_offline_status

        with tempfile.TemporaryDirectory() as tmp:
            os.environ["GL_CONFIG_DIR"] = tmp
            cf = Path(tmp) / "general-ludd.yml"
            cf.write_text("version: 1")
            cf2 = Path(tmp) / "other.yml"
            cf2.write_text("key: val")
            try:
                info = _gather_offline_status(config_dir=tmp)
                assert "config_dir" in info
                assert info["config_dir"] == tmp
                assert "config_files" in info
                assert len(info["config_files"]) >= 2
                for f in info["config_files"]:
                    assert "name" in f
                    assert "path" in f
                    assert "size_bytes" in f
            finally:
                del os.environ["GL_CONFIG_DIR"]

    def test_gather_filestore_info(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert "filestore_root" in info
        assert "filestore_exists" in info
        assert isinstance(info["filestore_exists"], bool)
        assert "filestore_binaries" in info
        assert isinstance(info["filestore_binaries"], list)

    def test_gather_database_info(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert "db_path" in info
        assert "db_exists" in info
        assert isinstance(info["db_exists"], bool)
        if info["db_exists"]:
            assert "db_size_bytes" in info
            assert isinstance(info["db_size_bytes"], int)
        assert "db_engine" in info

    def test_gather_system_info(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert "binary_paths" in info
        assert isinstance(info["binary_paths"], dict)

    def test_offline_status_does_not_require_daemon(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert "version" in info
        assert len(info["version"]) > 0

    def test_offline_status_top_level_keys(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        required = [
            "version",
            "python_version",
            "platform",
            "cwd",
            "config_dir",
            "config_files",
            "filestore_root",
            "filestore_exists",
            "filestore_binaries",
            "db_path",
            "db_exists",
            "db_engine",
            "binary_paths",
        ]
        for key in required:
            assert key in info, f"Missing key '{key}' in offline status"
