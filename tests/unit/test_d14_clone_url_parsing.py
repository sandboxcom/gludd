"""SEC.1 D-14: github.com clone URL strict parsing — behavioral tests.

Covers:
- reject_unsafe_repo_url blocks smart-transport RCE (:: separator)
- reject_unsafe_repo_url blocks file:// and local schemes
- reject_unsafe_repo_url blocks SSRF (loopback, link-local, private IPs)
- reject_unsafe_repo_url allows safe GitHub/GitLab URLs
- _reject_clone_url blocks option injection (leading dash)
- _reject_clone_url blocks ProxyCommand injection
- _reject_clone_url blocks file:// when allow_local=False
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from general_ludd.git_automation.repo import (
    _reject_clone_url,
    reject_unsafe_repo_url,
)


class TestRejectUnsafeRepoUrl:
    SAFE_URLS: ClassVar[list[str]] = [
        "https://github.com/foo/bar.git",
        "https://gitlab.com/org/repo",
        "git@github.com:org/repo.git",
        "https://bitbucket.org/org/repo",
        "git@gitlab.com:org/repo.git",
        "https://dev.azure.com/org/project/_git/repo",
    ]

    def test_safe_urls_accepted(self) -> None:
        for url in self.SAFE_URLS:
            result = reject_unsafe_repo_url(url)
            assert result == url, f"Safe URL rejected: {url}"

    def test_file_scheme_rejected(self) -> None:
        with pytest.raises(ValueError):
            reject_unsafe_repo_url("file:///etc/passwd")

    def test_file_uppercase_rejected(self) -> None:
        with pytest.raises(ValueError):
            reject_unsafe_repo_url("FILE:///etc/shadow")

    def test_smart_transport_rejected(self) -> None:
        for url in [
            "ext::sh -c 'malicious'",
            "git::https://evil.com/repo",
            "fd::3//tmp/socket",
        ]:
            with pytest.raises(ValueError):
                reject_unsafe_repo_url(url)

    def test_empty_url_rejected(self) -> None:
        with pytest.raises(ValueError):
            reject_unsafe_repo_url("")
        with pytest.raises(ValueError):
            reject_unsafe_repo_url("   ")

    def test_non_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            reject_unsafe_repo_url(42)  # type: ignore[arg-type]

    def test_leading_dash_option_injection(self) -> None:
        with pytest.raises(ValueError):
            reject_unsafe_repo_url("--upload-pack=evil https://github.com/org/repo")

    def test_smart_transport_colon_colon_rejected(self) -> None:
        with pytest.raises(ValueError, match="::"):
            reject_unsafe_repo_url("ext::sh -c 'touch /tmp/pwned'")

    def test_double_colon_in_query_string_rejected(self) -> None:
        with pytest.raises(ValueError):
            reject_unsafe_repo_url("https://github.com/repo?x::y")

    def test_loopback_ssrf_rejected(self) -> None:
        with pytest.raises(ValueError, match="internal/blocked"):
            reject_unsafe_repo_url("http://127.0.0.1/repo.git")

    def test_localhost_ssrf_rejected(self) -> None:
        with pytest.raises(ValueError, match="internal/blocked"):
            reject_unsafe_repo_url("https://localhost/repo.git")

    def test_aws_metadata_ssrf_rejected(self) -> None:
        with pytest.raises(ValueError, match="internal/blocked"):
            reject_unsafe_repo_url("http://169.254.169.254/latest/meta-data/")

    def test_private_rfc1918_10_rejected(self) -> None:
        with pytest.raises(ValueError, match="internal/blocked"):
            reject_unsafe_repo_url("http://10.0.0.1/repo.git")

    def test_private_rfc1918_192_rejected(self) -> None:
        with pytest.raises(ValueError, match="internal/blocked"):
            reject_unsafe_repo_url("http://192.168.1.1/repo.git")

    def test_gcp_metadata_ssrf_rejected(self) -> None:
        with pytest.raises(ValueError, match="internal/blocked"):
            reject_unsafe_repo_url("http://metadata.google.internal/repo.git")

    def test_no_scheme_local_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="no recognized transport scheme"):
            reject_unsafe_repo_url("/local/path")

    def test_scp_syntax_with_ssrf_rejected(self) -> None:
        with pytest.raises(ValueError, match="internal/blocked"):
            reject_unsafe_repo_url("git@127.0.0.1:repo.git")

    def test_unknown_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="refusing repo url scheme"):
            reject_unsafe_repo_url("ftp://bad.example.com/repo")

    def test_telnet_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="refusing repo url scheme"):
            reject_unsafe_repo_url("telnet://evil.com/repo")


class TestRejectCloneUrl:
    def test_accepts_safe_url(self) -> None:
        url = "https://github.com/org/repo.git"
        assert _reject_clone_url(url) == url

    def test_rejects_leading_dash(self) -> None:
        with pytest.raises(ValueError, match="beginning with '-'"):
            _reject_clone_url("--upload-pack=evil https://github.com/repo")

    def test_rejects_double_colon_smart_transport(self) -> None:
        with pytest.raises(ValueError, match="::"):
            _reject_clone_url("ext::sh -c 'rm -rf /'")

    def test_rejects_proxy_command_injection(self) -> None:
        with pytest.raises(ValueError, match="ProxyCommand"):
            _reject_clone_url("git@github.com:-oProxyCommand=evil/repo")

    def test_rejects_proxy_command_with_space(self) -> None:
        with pytest.raises(ValueError, match="ProxyCommand"):
            _reject_clone_url("git@host:-o ProxyCommand='nc evil 22'/repo")

    def test_rejects_file_url_when_allow_local_false(self) -> None:
        with pytest.raises(ValueError, match="file://"):
            _reject_clone_url("file:///etc/passwd", allow_local=False)

    def test_allows_file_url_when_allow_local_true(self) -> None:
        assert _reject_clone_url("file:///local/repo", allow_local=True) == "file:///local/repo"

    def test_rejects_file_uppercase_when_allow_local_false(self) -> None:
        with pytest.raises(ValueError, match="file://"):
            _reject_clone_url("FILE:///etc/shadow", allow_local=False)

    def test_rejects_empty_url(self) -> None:
        with pytest.raises(ValueError):
            _reject_clone_url("")

    def test_rejects_whitespace_only_url(self) -> None:
        with pytest.raises(ValueError):
            _reject_clone_url("   ")

    def test_rejects_non_string_type(self) -> None:
        with pytest.raises(ValueError):
            _reject_clone_url(42)  # type: ignore[arg-type]

    def test_accepts_git_ssh_url(self) -> None:
        assert _reject_clone_url("git@github.com:org/repo.git") == "git@github.com:org/repo.git"
