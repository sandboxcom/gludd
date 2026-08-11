"""Deep unit tests for general_ludd.security.ssrf — the canonical SSRF guard.

Covers _ip_addr_is_blocked, _nonstandard_ip_blocked, _is_single_label_hostname,
host_is_blocked, is_url_blocked, resolve_and_pin (mocked DNS), and
resolved_host_is_blocked.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import socket
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.security.ssrf import (
    BLOCKED_HOST_NAMES,
    BLOCKED_METADATA_IPS,
    DEFAULT_SCHEME_ALLOWLIST,
    PinnedTarget,
    SSRFError,
    _ip_addr_is_blocked,
    _is_single_label_hostname,
    _nonstandard_ip_blocked,
    host_is_blocked,
    is_url_blocked,
    resolve_and_pin,
    resolved_host_is_blocked,
)

# ---------------------------------------------------------------------------
# _ip_addr_is_blocked
# ---------------------------------------------------------------------------


class TestIpAddrIsBlocked:
    def test_localhost_ipv4_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("127.0.0.1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("127.255.255.254")) is True

    def test_localhost_ipv6_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("::1")) is True

    def test_private_ipv4_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("10.0.0.1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("172.16.0.1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("192.168.1.1")) is True

    def test_link_local_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("169.254.1.1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("fe80::1")) is True

    def test_multicast_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("224.0.0.1")) is True

    def test_unspecified_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("0.0.0.0")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("::")) is True

    def test_documentation_range_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("192.0.2.1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("198.51.100.1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("203.0.113.1")) is True

    def test_unique_local_ipv6_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("fc00::1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("fd00::1")) is True

    def test_public_ipv4_allowed(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("8.8.8.8")) is False
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("1.1.1.1")) is False

    def test_public_ipv6_allowed(self):
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("2001:4860:4860::8888")) is False

    def test_docker_cgnat_range_blocked(self):
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("100.64.0.1")) is True
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("100.100.100.200")) is True


# ---------------------------------------------------------------------------
# _nonstandard_ip_blocked
# ---------------------------------------------------------------------------


class TestNonstandardIpBlocked:
    def test_decimal_integer_ipv4_loopback(self):
        assert _nonstandard_ip_blocked("2130706433") is True

    def test_decimal_integer_ipv4_public(self):
        assert _nonstandard_ip_blocked("134744072") is False

    def test_octal_dotted_quad_loopback(self):
        assert _nonstandard_ip_blocked("0177.0.0.1") is True

    def test_hex_dotted_quad_loopback(self):
        assert _nonstandard_ip_blocked("0x7f.0.0.1") is True

    def test_mixed_encoding_blocked(self):
        assert _nonstandard_ip_blocked("0177.0x1f.0.1") is True

    def test_normal_dotted_quad_public(self):
        assert _nonstandard_ip_blocked("8.8.8.8") is False

    def test_plain_hostname_not_blocked(self):
        assert _nonstandard_ip_blocked("example.com") is False

    def test_short_digit_string_blocked_as_ip(self):
        assert _nonstandard_ip_blocked("42") is True

    def test_leading_zero_decimal_private_blocked(self):
        assert _nonstandard_ip_blocked("010.0.0.1") is True


# ---------------------------------------------------------------------------
# _is_single_label_hostname
# ---------------------------------------------------------------------------


class TestIsSingleLabelHostname:
    def test_single_label_hostname(self):
        assert _is_single_label_hostname("vault") is True
        assert _is_single_label_hostname("grafana") is True
        assert _is_single_label_hostname("prometheus") is True

    def test_dotted_hostname_not_single(self):
        assert _is_single_label_hostname("vault.local") is False
        assert _is_single_label_hostname("example.com") is False

    def test_ipv4_literal_not_single(self):
        assert _is_single_label_hostname("127.0.0.1") is False
        assert _is_single_label_hostname("8.8.8.8") is False

    def test_ipv6_literal_not_single(self):
        assert _is_single_label_hostname("::1") is False
        assert _is_single_label_hostname("2001:db8::1") is False

    def test_empty_is_single_label(self):
        assert _is_single_label_hostname("") is True


# ---------------------------------------------------------------------------
# host_is_blocked
# ---------------------------------------------------------------------------


class TestHostIsBlocked:
    def test_empty_host_blocked(self):
        assert host_is_blocked("") is True
        assert host_is_blocked("   ") is True

    def test_nul_byte_host_blocked(self):
        assert host_is_blocked("localhost\x00.evil.com") is True

    def test_localhost_name_blocked(self):
        assert host_is_blocked("localhost") is True
        assert host_is_blocked("LOCALHOST") is True

    def test_localhost_subdomain_blocked(self):
        assert host_is_blocked("foo.localhost") is True
        assert host_is_blocked("api.svc.localhost") is True

    def test_localhost_with_trailing_dot_blocked(self):
        assert host_is_blocked("localhost.") is True
        assert host_is_blocked("127.0.0.1.") is True

    def test_trailing_dots_stripped(self):
        assert host_is_blocked("127.0.0.1..") is True

    def test_blocked_metadata_names(self):
        for name in BLOCKED_HOST_NAMES:
            assert host_is_blocked(name) is True, f"expected {name!r} blocked"

    def test_blocked_metadata_ips(self):
        for ip in BLOCKED_METADATA_IPS:
            assert host_is_blocked(ip) is True, f"expected {ip!r} blocked"

    def test_single_label_hostname_blocked(self):
        assert host_is_blocked("vault") is True
        assert host_is_blocked("prometheus") is True

    def test_loopback_ipv4_blocked(self):
        assert host_is_blocked("127.0.0.1") is True
        assert host_is_blocked("127.99.99.99") is True

    def test_private_ipv4_blocked(self):
        assert host_is_blocked("10.0.0.1") is True
        assert host_is_blocked("192.168.1.1") is True
        assert host_is_blocked("172.16.0.1") is True

    def test_ipv6_loopback_blocked(self):
        assert host_is_blocked("::1") is True

    def test_bracketed_ipv6_blocked(self):
        assert host_is_blocked("[::1]") is True

    def test_public_ip_allowed(self):
        assert host_is_blocked("8.8.8.8") is False

    def test_public_hostname_allowed(self):
        assert host_is_blocked("example.com") is False
        assert host_is_blocked("github.com") is False

    def test_nonstandard_ip_decimal_blocked(self):
        assert host_is_blocked("2130706433") is True

    def test_hex_ip_blocked(self):
        assert host_is_blocked("0x7f.0.0.1") is True

    def test_strip_and_lower(self):
        assert host_is_blocked(" Localhost ") is True


# ---------------------------------------------------------------------------
# is_url_blocked
# ---------------------------------------------------------------------------


class TestIsUrlBlocked:
    def test_empty_url_blocked(self):
        assert is_url_blocked("") is True

    def test_non_string_url_blocked(self):
        assert is_url_blocked(None) is True
        assert is_url_blocked(42) is True

    def test_allowed_https_url(self):
        assert is_url_blocked("https://example.com/path") is False

    def test_allowed_http_url(self):
        assert is_url_blocked("http://example.com/path") is False

    def test_ftp_scheme_blocked_by_default(self):
        assert is_url_blocked("ftp://example.com/file") is True

    def test_file_scheme_blocked(self):
        assert is_url_blocked("file:///etc/passwd") is True

    def test_custom_scheme_allowlist(self):
        assert is_url_blocked("ftp://example.com", {"https"}) is True
        assert is_url_blocked("ftp://example.com", {"ftp"}) is False

    def test_localhost_url_blocked(self):
        assert is_url_blocked("https://localhost") is True
        assert is_url_blocked("http://127.0.0.1") is True

    def test_metadata_url_blocked(self):
        assert is_url_blocked("http://169.254.169.254/latest/meta-data") is True

    def test_malformed_url_blocked(self):
        assert is_url_blocked("://bad") is True

    def test_missing_host_blocked(self):
        assert is_url_blocked("https:///path-only") is True

    def test_different_scheme_cases(self):
        assert is_url_blocked("HTTPS://example.com") is False
        assert is_url_blocked("HTTP://example.com") is False


# ---------------------------------------------------------------------------
# resolve_and_pin (mocked DNS)
# ---------------------------------------------------------------------------


class TestResolveAndPin:
    def test_literal_ip_returns_pinned_target(self):
        result = resolve_and_pin("8.8.8.8", port=443)
        assert isinstance(result, PinnedTarget)
        assert result.host == "8.8.8.8"
        assert result.ip == "8.8.8.8"
        assert result.port == 443

    def test_bracketed_ipv6_returns_pinned_target(self):
        result = resolve_and_pin("[2001:4860:4860::8888]", port=8080)
        assert result.ip == "2001:4860:4860::8888"
        assert result.port == 8080

    def test_blocked_literal_raises(self):
        with pytest.raises(SSRFError):
            resolve_and_pin("127.0.0.1")
        with pytest.raises(SSRFError):
            resolve_and_pin("10.0.0.1")

    def test_blocked_name_raises(self):
        with pytest.raises(SSRFError):
            resolve_and_pin("localhost")

    def test_single_label_raises(self):
        with pytest.raises(SSRFError):
            resolve_and_pin("vault")

    def test_successful_dns_resolution(self):
        mock_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        ]
        with patch.object(
            concurrent.futures.ThreadPoolExecutor,
            "submit",
            return_value=MagicMock(result=MagicMock(return_value=mock_infos)),
        ):
            result = resolve_and_pin("example.com", port=443)
            assert result.host == "example.com"
            assert result.ip == "8.8.8.8"
            assert result.port == 443

    def test_dns_timeout_raises(self):
        with (
            patch.object(
                concurrent.futures.ThreadPoolExecutor,
                "submit",
                return_value=MagicMock(result=MagicMock(side_effect=concurrent.futures.TimeoutError())),
            ),
            pytest.raises(SSRFError),
        ):
            resolve_and_pin("example.com", timeout=0.1)

    def test_dns_resolves_to_private_ip_raises(self):
        mock_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0)),
        ]
        with (
            patch.object(
                concurrent.futures.ThreadPoolExecutor,
                "submit",
                return_value=MagicMock(result=MagicMock(return_value=mock_infos)),
            ),
            pytest.raises(SSRFError),
        ):
            resolve_and_pin("example.com")

    def test_dns_resolves_to_loopback_raises(self):
        mock_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
        ]
        with (
            patch.object(
                concurrent.futures.ThreadPoolExecutor,
                "submit",
                return_value=MagicMock(result=MagicMock(return_value=mock_infos)),
            ),
            pytest.raises(SSRFError),
        ):
            resolve_and_pin("example.com")

    def test_empty_dns_result_raises(self):
        with (
            patch.object(
                concurrent.futures.ThreadPoolExecutor,
                "submit",
                return_value=MagicMock(result=MagicMock(return_value=[])),
            ),
            pytest.raises(SSRFError),
        ):
            resolve_and_pin("example.com")

    def test_os_error_raises(self):
        with (
            patch.object(
                concurrent.futures.ThreadPoolExecutor,
                "submit",
                return_value=MagicMock(result=MagicMock(side_effect=OSError("NXDOMAIN"))),
            ),
            pytest.raises(SSRFError),
        ):
            resolve_and_pin("example.com")

    def test_public_then_private_dns_picks_first_public(self):
        mock_infos = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 0)),
        ]
        with (
            patch.object(
                concurrent.futures.ThreadPoolExecutor,
                "submit",
                return_value=MagicMock(result=MagicMock(return_value=mock_infos)),
            ),
            pytest.raises(SSRFError),
        ):
            resolve_and_pin("example.com")

    def test_mixed_public_ipv4_ipv6_picks_first(self):
        mock_infos = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 0, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0)),
        ]
        with patch.object(
            concurrent.futures.ThreadPoolExecutor,
            "submit",
            return_value=MagicMock(result=MagicMock(return_value=mock_infos)),
        ):
            result = resolve_and_pin("example.com")
            assert result.ip == "2001:4860:4860::8888"


# ---------------------------------------------------------------------------
# resolved_host_is_blocked
# ---------------------------------------------------------------------------


class TestResolvedHostIsBlocked:
    def test_blocked_literal(self):
        assert resolved_host_is_blocked("127.0.0.1") is True

    def test_allowed_literal(self):
        assert resolved_host_is_blocked("8.8.8.8") is False

    def test_blocked_name(self):
        assert resolved_host_is_blocked("localhost") is True

    def test_single_label_blocked(self):
        assert resolved_host_is_blocked("vault") is True


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_blocked_host_names_not_empty(self):
        assert len(BLOCKED_HOST_NAMES) > 0
        assert "localhost" in BLOCKED_HOST_NAMES
        assert "metadata.google.internal" in BLOCKED_HOST_NAMES

    def test_blocked_metadata_ips_not_empty(self):
        assert len(BLOCKED_METADATA_IPS) > 0
        assert "169.254.169.254" in BLOCKED_METADATA_IPS

    def test_default_scheme_allowlist(self):
        assert "http" in DEFAULT_SCHEME_ALLOWLIST
        assert "https" in DEFAULT_SCHEME_ALLOWLIST
        assert "ftp" not in DEFAULT_SCHEME_ALLOWLIST
