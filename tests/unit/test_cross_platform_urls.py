"""Cross-platform binary download URL tests."""

from __future__ import annotations

from unittest.mock import patch


class TestCrossPlatformUrls:
    def test_linux_amd64_openbao_url(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch.object(boot, "get_platform_info", return_value={"os": "linux", "arch": "amd64"}):
            url = boot.get_download_url("openbao")
            assert url is not None
            assert "linux" in url
            assert "bao_" in url
            assert ".tar.gz" in url

    def test_linux_amd64_opentofu_url(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch.object(boot, "get_platform_info", return_value={"os": "linux", "arch": "amd64"}):
            url = boot.get_download_url("opentofu")
            assert url is not None
            assert "linux" in url
            assert "tofu_" in url
            assert ".tar.gz" in url

    def test_darwin_arm64_opentofu_url(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch.object(boot, "get_platform_info", return_value={"os": "darwin", "arch": "arm64"}):
            url = boot.get_download_url("opentofu")
            assert url is not None
            assert "darwin" in url
            assert ".zip" in url

    def test_darwin_amd64_opentofu_url(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch.object(boot, "get_platform_info", return_value={"os": "darwin", "arch": "amd64"}):
            url = boot.get_download_url("opentofu")
            assert url is not None
            assert "darwin" in url
            assert ".zip" in url

    def test_windows_amd64_opentofu_url(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch.object(boot, "get_platform_info", return_value={"os": "windows", "arch": "amd64"}):
            url = boot.get_download_url("opentofu")
            assert url is not None
            assert "windows" in url
            assert ".zip" in url

    def test_platform_info_correct_on_actual_host(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        info = boot.get_platform_info()
        assert "os" in info
        assert "arch" in info
        assert info["os"] in ("darwin", "linux", "windows")
        assert info["arch"] in ("amd64", "arm64", "x86_64", "aarch64")

    def test_opentofu_arm64_normalized_to_amd64_for_download(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        with patch.object(boot, "get_platform_info", return_value={"os": "linux", "arch": "aarch64"}):
            url = boot.get_download_url("opentofu")
            assert "linux_amd64" in url

    def test_all_known_binaries_have_urls_for_each_platform(self):
        from general_ludd.filestore.bootstrap import BinaryBootstrapper

        boot = BinaryBootstrapper()
        for os_name in ("linux", "darwin", "windows"):
            for arch in ("amd64", "arm64"):
                with patch.object(boot, "get_platform_info", return_value={"os": os_name, "arch": arch}):
                    for name in boot.KNOWN_VERSIONS:
                        url = boot.get_download_url(name)
                        if os_name == "darwin" and arch == "arm64" and name == "openbao":
                            continue
                        assert url is not None, f"No URL for {name} on {os_name}/{arch}"
                        assert "github" in url
