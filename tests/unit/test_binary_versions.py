"""Test that offline status shows binary versions from filestore."""

from __future__ import annotations


class TestBinaryVersionsInStatus:
    def test_binary_bootstrapper_versions(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        bins = boot.list_binaries()
        assert isinstance(bins, list)

    def test_binary_known_versions(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        versions = boot.get_known_versions()
        assert "openbao" in versions
        assert versions["openbao"] == "2.2.0"

    def test_offline_status_includes_binary_versions(self):
        from general_ludd.cli import _gather_offline_status

        info = _gather_offline_status()
        assert "binary_versions" in info
        assert isinstance(info["binary_versions"], dict)

    def test_filestore_binaries_include_version(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        bins = boot.list_binaries_with_versions()
        assert isinstance(bins, list)
        for b in bins:
            assert "binary_name" in b
            assert "version" in b
