from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from general_ludd.filestore.store import FileStore


class TestOverlayReadWriteConsistency:
    def test_write_then_read_is_not_shadowed_by_overlay(self):
        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            shadow = Path(overlay_tmp) / "config" / "secrets.yml"
            shadow.parent.mkdir(parents=True, exist_ok=True)
            shadow.write_text("token: ATTACKER_CONTROLLED")
            store.write_text("config/secrets.yml", "token: REAL_VALUE")
            assert store.read_text("config/secrets.yml") == "token: REAL_VALUE"


class TestOverlayRemoveConsistency:
    def test_remove_of_exists_true_path_does_not_raise(self):
        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as overlay_tmp:
            store = FileStore(root_path=store_tmp, overlay_path=overlay_tmp)
            overlay_only = Path(overlay_tmp) / "overlay_only.txt"
            overlay_only.write_text("x")
            assert store.exists("overlay_only.txt") is True
            try:
                store.remove("overlay_only.txt")
            except FileNotFoundError:
                pytest.fail("remove() raised on a path exists() reported True (Finding 3)")
            assert store.exists("overlay_only.txt") is False


class TestSymlinkConfinement:
    @pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
    def test_read_does_not_follow_symlink_escaping_root(self):
        with tempfile.TemporaryDirectory() as store_tmp, tempfile.TemporaryDirectory() as secret_dir:
            secret = Path(secret_dir) / "private.txt"
            secret.write_text("TOP-SECRET")
            store = FileStore(root_path=store_tmp)
            os.symlink(secret, Path(store_tmp) / "innocent.txt")
            with pytest.raises((PermissionError, FileNotFoundError, ValueError)):
                store.read_text("innocent.txt")
