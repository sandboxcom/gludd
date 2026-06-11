from __future__ import annotations

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
             patch("general_ludd.filestore.bootstrap.Path.read_bytes", return_value=b"\x00"):
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


class TestDownload:
    @pytest.mark.asyncio
    async def test_bundled_binary_used(self, bootstrapper, mock_store):
        with patch.object(bootstrapper, "get_bundled_binary_path", return_value="/bundled/openbao"), \
             patch("general_ludd.filestore.bootstrap.Path.read_bytes", return_value=b"\x00\x01"):
            result = await bootstrapper.download("openbao")
            assert result is True
            mock_store.write_bytes.assert_called()

    @pytest.mark.asyncio
    async def test_bundled_read_fails_falls_back(self, bootstrapper, mock_store):
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
