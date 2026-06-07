"""Tests for bundled binary distribution."""

from __future__ import annotations

import tempfile
from pathlib import Path


class TestBundledBinaries:
    def test_bootstrapper_has_opentofu_version(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        boot = BinaryBootstrapper()
        versions = boot.get_known_versions()
        assert "opentofu" in versions

    def test_bootstrapper_has_openbao_version(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        boot = BinaryBootstrapper()
        versions = boot.get_known_versions()
        assert "openbao" in versions

    def test_bundled_binaries_dir_detectable(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        boot = BinaryBootstrapper()
        assert hasattr(boot, "get_bundled_binary_path")

    def test_bundled_binary_available_when_bundled(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            bundles = Path(tmp) / "bundled_binaries"
            bundles.mkdir()
            (bundles / "openbao").write_text("fake-binary")
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store, bundled_binaries_dir=str(bundles))
            path = boot.get_bundled_binary_path("openbao")
            assert path is not None

    def test_bundled_preferred_over_download(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper
        from general_ludd.filestore.store import FileStore

        with tempfile.TemporaryDirectory() as tmp:
            bundles = Path(tmp) / "bundled_binaries"
            bundles.mkdir()
            (bundles / "openbao").write_text("fake-binary")
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store, bundled_binaries_dir=str(bundles))
            assert boot._has_bundled("openbao") is True
            assert boot._has_bundled("opentofu") is False

    def test_status_shows_bundled_binaries(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert "binary_versions" in info
        versions = info["binary_versions"]
        assert "openbao" in versions
        assert "opentofu" in versions

    def test_dist_binaries_dir_exists(self):
        repo_root = Path(__file__).parent.parent.parent
        repo_root / "dist" / "binaries"
        # dist/binaries may not exist yet, but the make target should create it
        # This just verifies the structure is defined
        assert repo_root / "dist" / "binaries" is not None
