from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.root_path = "/fake/store"
    store.exists.return_value = False
    store.list_dir.return_value = []
    return store


@pytest.fixture
def bootstrapper(mock_store):
    from general_ludd.filestore.bootstrap import BinaryBootstrapper

    return BinaryBootstrapper(store=mock_store, bundled_binaries_dir=None)


class TestBinaryBootstrapperInit:
    def test_creates_binaries_dir(self, mock_store):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        BinaryBootstrapper(store=mock_store)
        mock_store.makedirs.assert_called_with("binaries")

    def test_known_versions_populated(self, bootstrapper):
        assert "openbao" in bootstrapper.KNOWN_VERSIONS
        assert "opentofu" in bootstrapper.KNOWN_VERSIONS

    def test_bundled_dir_stored(self, mock_store):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper(store=mock_store, bundled_binaries_dir="/tmp/bundled")
        assert boot._bundled_dir == "/tmp/bundled"

    def test_default_store_created(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        with patch("general_ludd.filestore.bootstrap.FileStore") as MockFS:
            mock_fs = MagicMock()
            MockFS.return_value = mock_fs
            boot = BinaryBootstrapper()
            MockFS.assert_called_once()
            assert boot._store is mock_fs


class TestDetectBinary:
    @patch("general_ludd.filestore.bootstrap.shutil.which", return_value="/usr/bin/python3")
    def test_detect_found(self, mock_which, bootstrapper):
        assert bootstrapper.detect_binary("python3") is True

    @patch("general_ludd.filestore.bootstrap.shutil.which", return_value=None)
    def test_detect_not_found(self, mock_which, bootstrapper):
        assert bootstrapper.detect_binary("nonexistent_binary") is False


class TestGetPlatformInfo:
    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="x86_64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Darwin")
    def test_macos_amd64(self, mock_sys, mock_mach, bootstrapper):
        info = bootstrapper.get_platform_info()
        assert info["os"] == "darwin"
        assert info["arch"] == "amd64"

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="aarch64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Linux")
    def test_linux_arm64(self, mock_sys, mock_mach, bootstrapper):
        info = bootstrapper.get_platform_info()
        assert info["os"] == "linux"
        assert info["arch"] == "arm64"

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="arm64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Darwin")
    def test_macos_arm64(self, mock_sys, mock_mach, bootstrapper):
        info = bootstrapper.get_platform_info()
        assert info["arch"] == "arm64"

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="amd64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Windows")
    def test_windows_amd64(self, mock_sys, mock_mach, bootstrapper):
        info = bootstrapper.get_platform_info()
        assert info["os"] == "windows"
        assert info["arch"] == "amd64"

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="riscv64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Linux")
    def test_unknown_arch(self, mock_sys, mock_mach, bootstrapper):
        info = bootstrapper.get_platform_info()
        assert info["arch"] == "riscv64"


class TestStoreBinary:
    def test_stores_data(self, bootstrapper, mock_store):
        data = b"\x00\x01\x02"
        bootstrapper.store_binary("testbin", data)
        mock_store.write_bytes.assert_called_once_with("binaries/testbin", data)


class TestGetKnownVersions:
    def test_returns_copy(self, bootstrapper):
        v = bootstrapper.get_known_versions()
        assert v is not bootstrapper.KNOWN_VERSIONS
        assert v == bootstrapper.KNOWN_VERSIONS


class TestListBinaries:
    def test_adds_binary_name_and_version(self, bootstrapper, mock_store):
        mock_store.list_dir.return_value = [{"name": "openbao", "path": "binaries/openbao", "is_dir": False}]
        result = bootstrapper.list_binaries()
        assert len(result) == 1
        assert result[0]["binary_name"] == "openbao"
        assert result[0]["version"] != "unknown"

    def test_unknown_version(self, bootstrapper, mock_store):
        mock_store.list_dir.return_value = [{"name": "custom", "path": "binaries/custom", "is_dir": False}]
        result = bootstrapper.list_binaries()
        assert result[0]["version"] == "unknown"

    def test_empty_dir(self, bootstrapper, mock_store):
        mock_store.list_dir.return_value = []
        assert bootstrapper.list_binaries() == []


class TestListBinariesWithVersions:
    def test_delegates(self, bootstrapper, mock_store):
        mock_store.list_dir.return_value = [{"name": "openbao", "path": "binaries/openbao", "is_dir": False}]
        result = bootstrapper.list_binaries_with_versions()
        assert len(result) == 1


class TestGetBinaryPath:
    def test_returns_path_when_exists(self, bootstrapper, mock_store):
        mock_store.exists.return_value = True
        path = bootstrapper.get_binary_path("openbao")
        assert path is not None
        assert "binaries/openbao" in path

    def test_returns_none_when_missing(self, bootstrapper, mock_store):
        mock_store.exists.return_value = False
        assert bootstrapper.get_binary_path("openbao") is None


class TestGetBundledBinaryPath:
    @patch("general_ludd.filestore.bootstrap.Path.is_file", return_value=True)
    def test_bundled_dir_found(self, mock_is_file, mock_store):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper(store=mock_store, bundled_binaries_dir="/bundled")
        result = boot.get_bundled_binary_path("openbao")
        assert result is not None
        assert "/bundled/openbao" in result

    @patch("general_ludd.filestore.bootstrap.Path.is_file", return_value=False)
    @patch("general_ludd.filestore.bootstrap.BinaryBootstrapper._find_dist_bundled_dir", return_value=None)
    def test_no_bundled_no_dist(self, mock_dist, mock_is_file, mock_store):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper(store=mock_store, bundled_binaries_dir="/bundled")
        assert boot.get_bundled_binary_path("openbao") is None

    @patch("general_ludd.filestore.bootstrap.Path.is_file", return_value=True)
    @patch("general_ludd.filestore.bootstrap.BinaryBootstrapper._find_dist_bundled_dir", return_value="/dist/binaries")
    def test_falls_back_to_dist(self, mock_dist, mock_is_file, mock_store):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper(store=mock_store, bundled_binaries_dir=None)
        result = boot.get_bundled_binary_path("openbao")
        assert result is not None

    def test_no_bundled_dir(self, bootstrapper):
        with patch.object(bootstrapper, "_find_dist_bundled_dir", return_value=None), \
             patch("general_ludd.filestore.bootstrap.Path.is_file", return_value=False):
            assert bootstrapper.get_bundled_binary_path("openbao") is None


class TestHasBundled:
    def test_true(self, bootstrapper):
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value="/foo/openbao"):
            assert bootstrapper._has_bundled("openbao") is True

    def test_false(self, bootstrapper):
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value=None):
            assert bootstrapper._has_bundled("openbao") is False


class TestIsPlatformAvailable:
    def test_available(self, bootstrapper):
        with patch.object(bootstrapper, "get_download_url", return_value="http://example.com"):
            assert bootstrapper.is_platform_available("openbao") is True

    def test_not_available(self, bootstrapper):
        with patch.object(bootstrapper, "get_download_url", return_value=None):
            assert bootstrapper.is_platform_available("openbao") is False


class TestFindDistBundledDir:
    @patch("general_ludd.filestore.bootstrap.os.path.isdir", return_value=True)
    def test_found(self, mock_isdir):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        result = BinaryBootstrapper._find_dist_bundled_dir()
        assert result is not None

    @patch("general_ludd.filestore.bootstrap.os.path.isdir", return_value=False)
    def test_not_found(self, mock_isdir):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        assert BinaryBootstrapper._find_dist_bundled_dir() is None


class TestSyncBundledToFilestore:
    def test_syncs_missing(self, bootstrapper, mock_store):
        mock_store.exists.return_value = False
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value="/bundled/openbao"), \
             patch("general_ludd.filestore.bootstrap.Path.read_bytes", return_value=b"\x00"), \
             patch.object(bootstrapper, "KNOWN_SHA256", {"openbao": "00" * 32}):
            synced = bootstrapper.sync_bundled_to_filestore()
            assert "openbao" in synced

    def test_skips_existing(self, bootstrapper, mock_store):
        mock_store.exists.return_value = True
        synced = bootstrapper.sync_bundled_to_filestore()
        assert "openbao" not in synced

    def test_handles_read_error(self, bootstrapper, mock_store):
        mock_store.exists.return_value = False
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value="/bundled/openbao"), \
             patch("general_ludd.filestore.bootstrap.Path.read_bytes", side_effect=OSError("fail")):
            synced = bootstrapper.sync_bundled_to_filestore()
            assert synced == []


class TestGetDownloadUrl:
    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="x86_64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Linux")
    def test_openbao_linux(self, mock_sys, mock_mach, bootstrapper):
        url = bootstrapper.get_download_url("openbao")
        assert url is not None
        assert "openbao" in url
        assert ".tar.gz" in url

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="x86_64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Darwin")
    def test_opentofu_macos(self, mock_sys, mock_mach, bootstrapper):
        url = bootstrapper.get_download_url("opentofu")
        assert url is not None
        assert "tofu" in url
        assert ".zip" in url

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="x86_64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Linux")
    def test_osquery_linux_x86_64(self, mock_sys, mock_mach, bootstrapper):
        url = bootstrapper.get_download_url("osquery")
        assert url is not None
        assert "osquery/osquery/releases/download/5.10.2" in url
        # Verified against the real 5.10.2 release asset list: linux assets
        # carry a "_1" build-revision infix before the OS component.
        assert url.endswith("osquery-5.10.2_1.linux_x86_64.tar.gz")

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="arm64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Darwin")
    def test_osquery_macos_arm64_resolves_to_only_published_tarball(self, mock_sys, mock_mach, bootstrapper):
        # osquery 5.10.2 publishes only ONE macOS tarball — "macos_x86_64"
        # — there is no separate "macos_arm64" asset (arm64 native support
        # ships only in the .pkg). Apple Silicon must resolve to that same
        # x86_64-named asset (runs under Rosetta 2); this was the exact
        # 404: the old code built a macos_arm64.tar.gz URL that does not
        # exist.
        url = bootstrapper.get_download_url("osquery")
        assert url is not None
        assert url.endswith("osquery-5.10.2_1.macos_x86_64.tar.gz")

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="x86_64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Darwin")
    def test_osquery_macos_x86_64(self, mock_sys, mock_mach, bootstrapper):
        url = bootstrapper.get_download_url("osquery")
        assert url is not None
        assert url.endswith("osquery-5.10.2_1.macos_x86_64.tar.gz")

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="aarch64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Linux")
    def test_osquery_linux_arm64_uses_aarch64_asset(self, mock_sys, mock_mach, bootstrapper):
        # osquery 5.10.2 DOES publish a linux arm64 tarball, just under the
        # asset-name "aarch64" rather than "arm64". The old code treated
        # this platform as unpublished (returned None); it should resolve.
        url = bootstrapper.get_download_url("osquery")
        assert url is not None
        assert url.endswith("osquery-5.10.2_1.linux_aarch64.tar.gz")

    @patch("general_ludd.filestore.bootstrap.platform.machine", return_value="amd64")
    @patch("general_ludd.filestore.bootstrap.platform.system", return_value="Windows")
    def test_osquery_windows_unavailable(self, mock_sys, mock_mach, bootstrapper):
        # osquery ships an .msi on Windows, not a .tar.gz -> None.
        assert bootstrapper.get_download_url("osquery") is None

    def test_osquery_in_known_versions(self, bootstrapper):
        assert bootstrapper.KNOWN_VERSIONS["osquery"] == "5.10.2"


class TestDownload:
    @pytest.mark.asyncio
    async def test_bundled_binary_used(self, bootstrapper, mock_store):
        # A matching pinned checksum is required to store a bundled binary.
        bootstrapper.KNOWN_SHA256["openbao"] = hashlib.sha256(b"\x00\x01").hexdigest()
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value="/bundled/openbao"), \
             patch("general_ludd.filestore.bootstrap.Path.read_bytes", return_value=b"\x00\x01"):
            result = await bootstrapper.download("openbao")
            assert result is True
            mock_store.write_bytes.assert_called()

    @pytest.mark.asyncio
    async def test_bundled_read_fails_falls_back(self, bootstrapper, mock_store):
        # Read failure falls back to the HTTP download, whose bytes must match
        # the pinned checksum to be stored.
        bootstrapper.KNOWN_SHA256["openbao"] = hashlib.sha256(b"\x02\x03").hexdigest()
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value="/bundled/openbao"), \
             patch("general_ludd.filestore.bootstrap.Path.read_bytes", side_effect=OSError("fail")), \
             patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"\x02\x03"
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await bootstrapper.download("openbao")
            assert result is True

    @pytest.mark.asyncio
    async def test_http_download_success(self, bootstrapper, mock_store):
        # The downloaded bytes must match the pinned checksum to be stored.
        bootstrapper.KNOWN_SHA256["openbao"] = hashlib.sha256(b"\x04\x05").hexdigest()
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value=None), \
             patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.content = b"\x04\x05"
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await bootstrapper.download("openbao")
            assert result is True

    @pytest.mark.asyncio
    async def test_http_download_non_200(self, bootstrapper, mock_store):
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value=None), \
             patch("httpx.AsyncClient") as MockClient:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await bootstrapper.download("openbao")
            assert result is False

    @pytest.mark.asyncio
    async def test_http_download_exception(self, bootstrapper, mock_store):
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value=None), \
             patch("httpx.AsyncClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(side_effect=Exception("network error"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_client
            result = await bootstrapper.download("openbao")
            assert result is False

    @pytest.mark.asyncio
    async def test_no_url_returns_false(self, bootstrapper, mock_store):
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value=None), \
             patch.object(bootstrapper, "get_download_url", return_value=None):
            result = await bootstrapper.download("openbao")
            assert result is False


class TestDownloadOpenbao:
    @pytest.mark.asyncio
    async def test_delegates(self, bootstrapper):
        with patch.object(bootstrapper, "download", new_callable=AsyncMock, return_value=True):
            result = await bootstrapper.download_openbao()
            assert result is True


class TestCheckOpenbaoInStore:
    def test_in_store(self, bootstrapper, mock_store):
        mock_store.exists.return_value = True
        assert bootstrapper.check_openbao_in_store() is True

    def test_bundled(self, bootstrapper, mock_store):
        mock_store.exists.return_value = False
        with patch.object(bootstrapper, "_has_bundled", return_value=True):
            assert bootstrapper.check_openbao_in_store() is True

    def test_not_available(self, bootstrapper, mock_store):
        mock_store.exists.return_value = False
        with patch.object(bootstrapper, "_has_bundled", return_value=False):
            assert bootstrapper.check_openbao_in_store() is False


class TestDownloadAll:
    @pytest.mark.asyncio
    async def test_downloads_all(self, bootstrapper):
        with patch.object(bootstrapper, "download", new_callable=AsyncMock, return_value=True):
            results = await bootstrapper.download_all()
            assert "openbao" in results
            assert "opentofu" in results
            assert all(results.values())


def _make_osquery_tarball(member_name: str = "osquery-5.10.2.linux_x86_64/bin/osqueryi") -> bytes:
    """Build a minimal in-memory .tar.gz containing a single osqueryi member."""
    import io as _io
    import tarfile as _tf

    content = b"\x7fELF fake osqueryi binary"
    buf = _io.BytesIO()
    with _tf.open(fileobj=buf, mode="w:gz") as archive:
        info = _tf.TarInfo(name=member_name)
        info.size = len(content)
        archive.addfile(info, _io.BytesIO(content))
    return buf.getvalue()


class TestOsqueryExtraction:
    """Unit tests for _extract_osquery_executable."""

    def test_extracts_osqueryi_from_tarball(self, bootstrapper):
        tar_data = _make_osquery_tarball("osquery-5.10.2.linux_x86_64/bin/osqueryi")
        result = bootstrapper._extract_osquery_executable(tar_data)
        assert result == b"\x7fELF fake osqueryi binary"

    def test_extracts_osqueryi_top_level(self, bootstrapper):
        tar_data = _make_osquery_tarball("osqueryi")
        result = bootstrapper._extract_osquery_executable(tar_data)
        assert result == b"\x7fELF fake osqueryi binary"

    def test_rejects_absolute_path_member(self, bootstrapper):
        """A member with an absolute path must not be extracted."""
        import io as _io
        import tarfile as _tf

        content = b"evil"
        buf = _io.BytesIO()
        with _tf.open(fileobj=buf, mode="w:gz") as archive:
            info = _tf.TarInfo(name="/etc/osqueryi")
            info.size = len(content)
            archive.addfile(info, _io.BytesIO(content))
        tar_data = buf.getvalue()
        result = bootstrapper._extract_osquery_executable(tar_data)
        # No safe member found -- original bytes returned unchanged.
        assert result == tar_data

    def test_rejects_dotdot_path_member(self, bootstrapper):
        """A member containing '..' must not be extracted."""
        import io as _io
        import tarfile as _tf

        content = b"evil"
        buf = _io.BytesIO()
        with _tf.open(fileobj=buf, mode="w:gz") as archive:
            info = _tf.TarInfo(name="../escape/osqueryi")
            info.size = len(content)
            archive.addfile(info, _io.BytesIO(content))
        tar_data = buf.getvalue()
        result = bootstrapper._extract_osquery_executable(tar_data)
        assert result == tar_data

    def test_plain_binary_returned_unchanged(self, bootstrapper):
        """Non-tarball bytes (e.g. a bundled plain executable) pass through."""
        plain = b"\x7fELF not a tar"
        assert bootstrapper._extract_osquery_executable(plain) == plain


@pytest.mark.asyncio
async def test_osquery_binary_is_executable_after_download(tmp_path):
    """After download(), the stored osquery binary must satisfy os.access(path, os.X_OK).

    Uses a real FileStore backed by tmp_path so that write_bytes actually
    writes to disk and os.chmod is exercisable.
    """
    import os as _os

    from general_ludd.filestore.bootstrap import BinaryBootstrapper
    from general_ludd.filestore.store import FileStore

    tar_bytes = _make_osquery_tarball("osquery-5.10.2.linux_x86_64/bin/osqueryi")

    store = FileStore(root_path=str(tmp_path))
    # Pin the received (tarball) bytes so the integrity gate admits them; the
    # executable member is then extracted + chmod'd.
    boot = BinaryBootstrapper(
        store=store,
        known_sha256={"osquery": hashlib.sha256(tar_bytes).hexdigest()},
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = tar_bytes
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(boot, "get_bundled_binary_path", return_value=None), \
         patch.object(boot, "get_download_url", return_value="https://example.com/osquery.tar.gz"), \
         patch("httpx.AsyncClient", return_value=mock_client):
        result = await boot.download("osquery")

    assert result is True, "download() must return True on HTTP 200"
    stored_path = boot.get_binary_path("osquery")
    assert stored_path is not None, "get_binary_path('osquery') must not be None after download"
    assert _os.path.isfile(stored_path), f"Expected a file at {stored_path!r}"
    assert _os.access(stored_path, _os.X_OK), (
        f"osquery binary at {stored_path!r} is NOT executable after download — "
        "chmod step missing"
    )


@pytest.mark.asyncio
async def test_download_rejects_oversized_response(tmp_path):
    """download() must refuse a response over the 512 MiB cap (bounds memory)
    by its declared Content-Length, and store nothing."""
    import os as _os

    from general_ludd.filestore.bootstrap import (
        _MAX_DOWNLOAD_BYTES,
        BinaryBootstrapper,
    )
    from general_ludd.filestore.store import FileStore

    store = FileStore(root_path=str(tmp_path))
    boot = BinaryBootstrapper(store=store)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    # Declared size over the cap → rejected before storing.
    mock_resp.headers = {"content-length": str(_MAX_DOWNLOAD_BYTES + 1)}
    mock_resp.content = b"small-body"
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch.object(boot, "get_bundled_binary_path", return_value=None), \
         patch.object(boot, "get_download_url", return_value="https://example.com/x.tar.gz"), \
         patch("httpx.AsyncClient", return_value=mock_client):
        result = await boot.download("osquery")

    assert result is False, "oversized download must be rejected"
    stored = boot.get_binary_path("osquery")
    assert stored is None or not _os.path.isfile(stored), "nothing stored on rejection"
