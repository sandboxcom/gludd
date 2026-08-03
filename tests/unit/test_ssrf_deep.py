"""Deep SSRF protection tests: DNS rebinding, IPv6-mapped, encoding tricks, homographs.

Covers vectors NOT already in test_security_comprehensive.py or
test_connector_security_regression.py.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import FrozenInstanceError

import pytest

from general_ludd.security.ssrf import (
    PinnedTarget,
    SSRFError,
    _ip_addr_is_blocked,
    _nonstandard_ip_blocked,
    host_is_blocked,
    is_url_blocked,
    resolve_and_pin,
    resolved_host_is_blocked,
)

# ── IPv6-mapped IPv4 addresses ──────────────────────────────────────────────


class TestIPv6MappedIPv4:
    def test_mapped_loopback_blocked(self) -> None:
        assert host_is_blocked("::ffff:127.0.0.1") is True

    def test_mapped_loopback_bracketed_blocked(self) -> None:
        assert host_is_blocked("[::ffff:127.0.0.1]") is True

    def test_mapped_rfc1918_blocked(self) -> None:
        assert host_is_blocked("::ffff:10.0.0.1") is True

    def test_mapped_link_local_blocked(self) -> None:
        assert host_is_blocked("::ffff:169.254.169.254") is True

    def test_mapped_localhost_blocked(self) -> None:
        assert host_is_blocked("::ffff:0.0.0.0") is True

    def test_mapped_public_allowed(self) -> None:
        assert host_is_blocked("::ffff:93.184.216.34") is False


# ── Octal / hex / mixed IP notation edge cases ──────────────────────────────


class TestNonstandardIPEdgeCases:
    def test_all_octets_octal(self) -> None:
        assert _nonstandard_ip_blocked("0177.00.00.01") is True

    def test_all_octets_hex(self) -> None:
        assert _nonstandard_ip_blocked("0x7f.0x00.0x00.0x01") is True

    def test_decimal_with_leading_zeros(self) -> None:
        assert _nonstandard_ip_blocked("010.0.0.1") is True

    def test_nonstandard_public_allowed(self) -> None:
        assert _nonstandard_ip_blocked("0x5d.0xb8.0xd8.0x22") is False

    def test_hex_no_dotted_loopback(self) -> None:
        assert _nonstandard_ip_blocked("0x7f000001") is False

    def test_decimal_integer_loopback(self) -> None:
        assert _nonstandard_ip_blocked("2130706433") is True

    def test_decimal_integer_above_2pow32_rejected(self) -> None:
        assert _nonstandard_ip_blocked("5000000000") is False

    def test_too_few_octets_not_flagged(self) -> None:
        assert _nonstandard_ip_blocked("127.0.0") is False

    def test_too_many_octets_not_flagged(self) -> None:
        assert _nonstandard_ip_blocked("127.0.0.1.1") is False

    def test_octets_out_of_range_not_flagged(self) -> None:
        assert _nonstandard_ip_blocked("0xFFF.0.0.1") is False


# ── URL scheme confusion ────────────────────────────────────────────────────


class TestURLSchemeConfusion:
    def test_file_scheme_blocked(self) -> None:
        assert is_url_blocked("file:///etc/passwd") is True

    def test_gopher_scheme_blocked(self) -> None:
        assert is_url_blocked("gopher://127.0.0.1:70/_hello") is True

    def test_dict_scheme_blocked(self) -> None:
        assert is_url_blocked("dict://localhost:11211/stats") is True

    def test_ftp_scheme_blocked_by_default(self) -> None:
        assert is_url_blocked("ftp://example.com/data") is True

    def test_ssh_scheme_blocked(self) -> None:
        assert is_url_blocked("ssh://localhost/") is True

    def test_https_public_allowed(self) -> None:
        assert is_url_blocked("https://api.github.com/status") is False

    def test_http_public_allowed_with_http_allowlist(self) -> None:
        assert is_url_blocked("http://example.com", scheme_allowlist={"http", "https"}) is False

    def test_https_only_rejects_http(self) -> None:
        assert is_url_blocked("http://example.com", scheme_allowlist={"https"}) is True

    def test_scheme_case_insensitive(self) -> None:
        assert is_url_blocked("HTTPS://example.com") is False


# ── URL encoding tricks ─────────────────────────────────────────────────────


class TestURLEncodingTricks:
    def test_percent_encoded_localhost_blocked(self) -> None:
        assert host_is_blocked("%6c%6f%63%61%6c%68%6f%73%74") is True

    def test_percent_encoded_host_not_magically_decoded(self) -> None:
        url = "https://127.0.0.1%2f%2fadmin"
        assert is_url_blocked(url) is False

    def test_url_with_query_params_blocked(self) -> None:
        assert is_url_blocked("https://localhost/api?safe=true&token=abc") is True

    def test_url_with_fragment_blocked(self) -> None:
        assert is_url_blocked("https://localhost/api#section") is True

    def test_url_with_at_sign_username(self) -> None:
        assert is_url_blocked("https://safe@127.0.0.1/api") is True

    def test_malformed_invalid_url(self) -> None:
        assert is_url_blocked("not a url at all") is True

    def test_url_no_host_blocked(self) -> None:
        assert is_url_blocked("https:///path") is True

    def test_url_non_string_type(self) -> None:
        assert is_url_blocked(42) is True  # type: ignore[arg-type]


# ── IPv6 scope / zone IDs ───────────────────────────────────────────────────


class TestIPv6ScopeAndZone:
    def test_link_local_with_scope_blocked(self) -> None:
        assert host_is_blocked("fe80::1%eth0") is True

    def test_loopback_with_zone_id_blocked(self) -> None:
        assert host_is_blocked("::1%lo0") is True

    def test_link_local_bracketed_scope_blocked(self) -> None:
        assert host_is_blocked("[fe80::1%25eth0]") is True

    def test_scope_id_stripped_in_resolve_and_pin(self, monkeypatch) -> None:
        def _resolve_link_local(*_args, **_kwargs):
            return [
                (
                    socket.AF_INET6,
                    socket.SOCK_STREAM,
                    0,
                    "",
                    ("fe80::1%eth0", 0, 0, 0),
                )
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_link_local)
        with pytest.raises(SSRFError):
            resolve_and_pin("some-host.local")

    def test_public_ipv6_with_zone_not_blocked(self) -> None:
        assert host_is_blocked("2606:2800:220:1:248:1893:25c8:1946%eth0") is False


# ── Unicode homograph attacks ───────────────────────────────────────────────


class TestUnicodeHomographAttacks:
    def test_cyrillic_o_in_localhost(self) -> None:
        assert host_is_blocked("l\u043ecalh\u043est") is True

    def test_cyrillic_a_in_localhost(self) -> None:
        assert host_is_blocked("l\u043e\u0441alh\u043est") is True

    def test_homograph_dotless(self) -> None:
        assert host_is_blocked("l\u043ecalhost") is True

    def test_idn_encoded_blocked(self) -> None:
        assert host_is_blocked("xn--metadatat-bgf.google.internal") is False

    def test_homograph_single_label(self) -> None:
        assert host_is_blocked("m\u0435tadata") is True


# ── DNS rebinding attacks ───────────────────────────────────────────────────


class TestDNSRebinding:
    def test_resolve_and_pin_all_private_rejected(self, monkeypatch) -> None:
        def _resolve_all_private(*_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("192.168.1.1", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_all_private)
        with pytest.raises(SSRFError):
            resolve_and_pin("benign.example.com")

    def test_resolve_and_pin_mixed_private_public(self, monkeypatch) -> None:
        def _resolve_mixed(*_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_mixed)
        with pytest.raises(SSRFError):
            resolve_and_pin("benign.example.com")

    def test_resolve_and_pin_all_private_ipv6(self, monkeypatch) -> None:
        def _resolve_ipv6_private(*_args, **_kwargs):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("::1", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_ipv6_private)
        with pytest.raises(SSRFError):
            resolve_and_pin("benign.example.com")

    def test_resolve_and_pin_timeout_rejected(self, monkeypatch) -> None:
        import concurrent.futures

        def _slow_resolve(*_args, **_kwargs):
            raise concurrent.futures.TimeoutError()

        monkeypatch.setattr(socket, "getaddrinfo", _slow_resolve)
        with pytest.raises(SSRFError):
            resolve_and_pin("benign.example.com", timeout=0.1)

    def test_resolve_and_pin_empty_result_rejected(self, monkeypatch) -> None:
        def _resolve_empty(*_args, **_kwargs):
            return []

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_empty)
        with pytest.raises(SSRFError):
            resolve_and_pin("benign.example.com")

    def test_resolve_and_pin_literal_ip_returns_pinned_target(self) -> None:
        result = resolve_and_pin("93.184.216.34", port=8443)
        assert isinstance(result, PinnedTarget)
        assert result.ip == "93.184.216.34"
        assert result.host == "93.184.216.34"
        assert result.port == 8443

    def test_resolve_and_pin_blocked_host_raises_immediately(self) -> None:
        with pytest.raises(SSRFError):
            resolve_and_pin("127.0.0.1")

    def test_resolved_host_is_blocked_true(self, monkeypatch) -> None:
        def _resolve_private(*_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("10.0.0.1", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_private)
        assert resolved_host_is_blocked("some-host", timeout=0.5) is True

    def test_resolved_host_is_blocked_false(self, monkeypatch) -> None:
        def _resolve_public(*_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", _resolve_public)
        assert resolved_host_is_blocked("example.com", timeout=0.5) is False


# ── Additional host_is_blocked edge cases ───────────────────────────────────


class TestHostIsBlockedEdgeCases:
    def test_uppercase_mixed_localhost_blocked(self) -> None:
        assert host_is_blocked("LoCaLhOsT") is True

    def test_uppercase_loopback_blocked(self) -> None:
        assert host_is_blocked("127.0.0.1") is True

    def test_whitespace_padding_blocked(self) -> None:
        assert host_is_blocked("  127.0.0.1  ") is True

    def test_whitespace_empty_after_strip(self) -> None:
        assert host_is_blocked("   ") is True

    def test_multiple_trailing_dots_blocked(self) -> None:
        assert host_is_blocked("127.0.0.1...") is True

    def test_link_local_ipv4_blocked(self) -> None:
        assert host_is_blocked("169.254.1.1") is True

    def test_link_local_ipv6_blocked(self) -> None:
        assert host_is_blocked("fe80::1") is True

    def test_unique_local_ipv6_blocked(self) -> None:
        assert host_is_blocked("fc00::1") is True

    def test_unique_local_ipv6_bracketed_blocked(self) -> None:
        assert host_is_blocked("[fd00::1]") is True

    def test_reserved_documentation_ipv4_blocked(self) -> None:
        assert host_is_blocked("198.51.100.1") is True

    def test_reserved_benchmark_ipv4_blocked(self) -> None:
        assert host_is_blocked("198.18.0.1") is True

    def test_cgnat_address_blocked(self) -> None:
        assert host_is_blocked("100.64.0.1") is True

    def test_array_suffix_localhost_blocked(self) -> None:
        assert host_is_blocked("localhost.") is True


# ── _ip_addr_is_blocked exhaustive checks ───────────────────────────────────


class TestIPAddrIsBlocked:
    def test_is_global_public_allowed(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("93.184.216.34")) is False

    def test_is_global_public_ipv6_allowed(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("2606:2800:220:1:248:1893:25c8:1946")) is False

    def test_ipv4_loopback_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("127.0.0.1")) is True

    def test_ipv6_loopback_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("::1")) is True

    def test_ipv4_private_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("10.0.0.1")) is True

    def test_ipv6_private_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv6Address("fd00::1")) is True

    def test_multicast_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("224.0.0.1")) is True

    def test_unspecified_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("0.0.0.0")) is True

    def test_link_local_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("169.254.1.1")) is True

    def test_not_global_blocked(self) -> None:
        assert _ip_addr_is_blocked(ipaddress.IPv4Address("192.0.2.1")) is True


# ── NUL byte and truncation attacks ─────────────────────────────────────────


class TestNullByteAttacks:
    def test_nul_mid_host_blocked(self) -> None:
        assert host_is_blocked("127.0\x000.1") is True

    def test_nul_anywhere_in_host_blocked(self) -> None:
        assert host_is_blocked("example\x00.com") is True

    def test_nul_leading_truncation(self) -> None:
        assert host_is_blocked("\x00127.0.0.1") is True

    def test_multiple_nul_bytes(self) -> None:
        assert host_is_blocked("127\x00.0\x00.0\x00.1") is True


# ── PinnedTarget dataclass ──────────────────────────────────────────────────


class TestPinnedTarget:
    def test_pinned_target_creation(self) -> None:
        pt = PinnedTarget(host="example.com", ip="93.184.216.34", port=443)
        assert pt.host == "example.com"
        assert pt.ip == "93.184.216.34"
        assert pt.port == 443

    def test_pinned_target_frozen(self) -> None:
        pt = PinnedTarget(host="example.com", ip="93.184.216.34", port=443)
        with pytest.raises(FrozenInstanceError):
            pt.host = "other"  # type: ignore[misc]

    def test_pinned_target_equality(self) -> None:
        a = PinnedTarget(host="example.com", ip="93.184.216.34", port=443)
        b = PinnedTarget(host="example.com", ip="93.184.216.34", port=443)
        c = PinnedTarget(host="example.com", ip="93.184.216.34", port=8080)
        assert a == b
        assert a != c

    def test_pinned_target_hashable(self) -> None:
        pt = PinnedTarget(host="example.com", ip="93.184.216.34", port=443)
        s = {pt}
        assert len(s) == 1
