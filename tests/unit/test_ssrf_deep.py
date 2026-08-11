"""Deep behavioral tests for security/ssrf.py — m._nonstandard_ip_blocked edge cases."""

from __future__ import annotations

import ipaddress

import pytest

from general_ludd.security import ssrf as m
from general_ludd.security.ssrf import (
    BLOCKED_HOST_NAMES,
    BLOCKED_METADATA_IPS,
    PinnedTarget,
    SSRFError,
    host_is_blocked,
    is_url_blocked,
    resolve_and_pin,
    resolved_host_is_blocked,
)


class TestNonstandardIpBlocked:
    """The curl-bypass detector: decimal/octal/hex/mixed IP encodings."""

    # -- Decimal integer IPv4 — "2130706433" = 127.0.0.1 ------------------
    def test_decimal_integer_loopback_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("2130706433") is True

    def test_decimal_integer_rfc1918_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("3232235777") is True  # 192.168.1.1

    def test_decimal_integer_public_allowed(self) -> None:
        assert m._nonstandard_ip_blocked("134744072") is False  # 8.8.8.8

    def test_decimal_integer_too_large(self) -> None:
        assert m._nonstandard_ip_blocked("4294967296") is False  # 2^32, overflow

    def test_decimal_integer_non_digit(self) -> None:
        assert m._nonstandard_ip_blocked("abc123") is False

    # -- Octal dotted-quad — "0177.0.0.1" = 127.0.0.1 -------------------
    def test_octal_dotted_quad_loopback_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("0177.0.0.1") is True

    def test_octal_dotted_quad_rfc1918_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("0300.0250.01.01") is True  # 192.168.1.1

    def test_octal_all_octets_loopback_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("0177.0000.0000.0001") is True

    # -- Hex dotted-quad — "0x7f.0.0.1" = 127.0.0.1 -------------------
    def test_hex_dotted_quad_loopback_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("0x7f.0.0.1") is True

    def test_hex_dotted_quad_regular_allowed(self) -> None:
        assert m._nonstandard_ip_blocked("0x08.0x08.0x08.0x08") is False  # 8.8.8.8

    def test_hex_uppercase_x_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("0X7f.0X00.0X00.0X01") is True

    # -- Mixed encodings — "0177.0x1f.0.1" = 127.31.0.1 ----------------
    def test_mixed_octal_hex_loopback_blocked(self) -> None:
        assert m._nonstandard_ip_blocked("0177.0x00.0x00.0x01") is True

    def test_mixed_encodings_public_allowed(self) -> None:
        assert m._nonstandard_ip_blocked("0x08.020.0x08.08") is False  # 8.16.8.8

    # -- Ruled-out inputs -------------------------------------------------
    def test_no_dots_non_numeric(self) -> None:
        assert m._nonstandard_ip_blocked("example.com") is False

    def test_three_octets_skipped(self) -> None:
        assert m._nonstandard_ip_blocked("10.0.0") is False

    def test_five_octets_skipped(self) -> None:
        assert m._nonstandard_ip_blocked("10.0.0.0.1") is False

    def test_empty_octet_part(self) -> None:
        assert m._nonstandard_ip_blocked("10..0.1") is False

    def test_invalid_octal_digit_sets_decimal_path(self) -> None:
        assert m._nonstandard_ip_blocked("0128.0.0.1") is False  # '0128' fails octal check

    # -- Standard IP literals pass through correctly -----------------------
    def test_standard_ip_literal_not_handled_here(self) -> None:
        """Standard dotted-quad IP is handled by ip_address(), not this function."""
        assert m._nonstandard_ip_blocked("127.0.0.1") is True  # both paths agree
        assert m._nonstandard_ip_blocked("8.8.8.8") is False


class TestHostIsBlocked:
    """Literal host deny predicate — NO DNS, pure string logic."""

    def test_empty_string_blocked(self) -> None:
        assert host_is_blocked("") is True

    def test_whitespace_only_blocked(self) -> None:
        assert host_is_blocked("   ") is True

    def test_nul_byte_smuggling(self) -> None:
        assert host_is_blocked("\x00") is True
        assert host_is_blocked("normal\x00.evil") is True

    def test_localhost_and_subdomains(self) -> None:
        assert host_is_blocked("localhost") is True
        assert host_is_blocked("foo.localhost") is True
        assert host_is_blocked("LOCALHOST") is True  # case-insensitive
        assert host_is_blocked("Foo.LocalHost") is True

    def test_localhost_contained_not_blocked(self) -> None:
        assert host_is_blocked("notlocalhost.com") is False
        assert host_is_blocked("mylocalhost.example.com") is False

    def test_blocked_host_names(self) -> None:
        for name in BLOCKED_HOST_NAMES:
            assert host_is_blocked(name) is True, f"{name!r} should be blocked"

    def test_blocked_metadata_ips(self) -> None:
        for ip in BLOCKED_METADATA_IPS:
            assert host_is_blocked(ip) is True, f"{ip!r} should be blocked"

    def test_trailing_dot_stripped(self) -> None:
        assert host_is_blocked("127.0.0.1.") is True
        assert host_is_blocked("localhost.") is True
        assert host_is_blocked("169.254.169.254.") is True

    def test_double_trailing_dot_stripped(self) -> None:
        assert host_is_blocked("127.0.0.1..") is True

    def test_public_host_trailing_dot_allowed(self) -> None:
        assert host_is_blocked("api.github.com.") is False

    def test_single_label_hostname_blocked(self) -> None:
        assert host_is_blocked("vault") is True
        assert host_is_blocked("grafana") is True
        assert host_is_blocked("prometheus") is True

    def test_multi_label_public_allowed(self) -> None:
        assert host_is_blocked("api.github.com") is False
        assert host_is_blocked("example.org") is False

    def test_ipv6_bracket_unwrap(self) -> None:
        assert host_is_blocked("[::1]") is True
        assert host_is_blocked("[fc00::1]") is True

    def test_ipv6_bracket_public_allowed(self) -> None:
        assert host_is_blocked("[2606:4700::6810:85e5]") is False

    def test_ipv6_bracket_with_trailing_dot(self) -> None:
        assert host_is_blocked("[::1].") is True

    def test_stripped_to_empty(self) -> None:
        assert host_is_blocked(".") is True
        assert host_is_blocked("[") is True


class TestIsUrlBlocked:
    """Scheme-aware URL SSRF gate."""

    def test_none_input_blocked(self) -> None:
        assert is_url_blocked(None) is True  # type: ignore[arg-type]

    def test_empty_string_blocked(self) -> None:
        assert is_url_blocked("") is True

    def test_malformed_url_blocked(self) -> None:
        assert is_url_blocked("not a url at all") is True

    def test_disallowed_scheme_blocked(self) -> None:
        assert is_url_blocked("ftp://example.com/x", {"https"}) is True

    def test_http_scheme_in_default_allowlist(self) -> None:
        assert is_url_blocked("http://api.github.com/repos") is False

    def test_https_with_blocked_host(self) -> None:
        assert is_url_blocked("https://127.0.0.1:8200/secret") is True

    def test_https_with_public_host(self) -> None:
        assert is_url_blocked("https://api.github.com/repos", {"https"}) is False

    def test_custom_scheme_allowlist(self) -> None:
        assert is_url_blocked("http://public.example.com", {"https"}) is True
        assert is_url_blocked("https://public.example.com", {"https"}) is False

    def test_no_host_in_url(self) -> None:
        assert is_url_blocked("https:///path") is True

    def test_case_insensitive_scheme_matching(self) -> None:
        assert is_url_blocked("HTTPS://public.example.com", {"https"}) is False


class TestResolveAndPin:
    """DNS-resolving SSRF guard — bounded, fail-closed."""

    def test_blocked_host_raises_immediately(self) -> None:
        with pytest.raises(SSRFError):
            resolve_and_pin("127.0.0.1")

    def test_literal_public_ip_returns_pinned_target(self) -> None:
        result = resolve_and_pin("8.8.8.8", port=443)
        assert isinstance(result, PinnedTarget)
        assert result.ip == "8.8.8.8"
        assert result.host == "8.8.8.8"
        assert result.port == 443

    def test_literal_ip_with_trailing_dot_returns_pinned_target(self) -> None:
        result = resolve_and_pin("8.8.8.8.")
        assert result.ip == "8.8.8.8"

    def test_unresolvable_host_raises(self) -> None:
        with pytest.raises(SSRFError):
            resolve_and_pin("this-does-not-exist-asdlkfjas", timeout=0.5)

    def test_resolved_host_is_blocked_wrapper(self) -> None:
        assert resolved_host_is_blocked("127.0.0.1") is True
        assert resolved_host_is_blocked("localhost") is True
        assert resolved_host_is_blocked("8.8.8.8") is False


class TestIpAddrIsBlocked:
    """The fundamental IP classifier."""

    def test_private_range(self) -> None:

        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("10.0.0.1")) is True
        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("192.168.1.1")) is True
        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("172.16.0.1")) is True

    def test_loopback(self) -> None:

        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("127.0.0.1")) is True
        assert m._ip_addr_is_blocked(ipaddress.IPv6Address("::1")) is True

    def test_link_local(self) -> None:

        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("169.254.1.1")) is True
        assert m._ip_addr_is_blocked(ipaddress.IPv6Address("fe80::1")) is True

    def test_multicast(self) -> None:

        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("224.0.0.1")) is True

    def test_global_public_allowed(self) -> None:

        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("8.8.8.8")) is False
        assert m._ip_addr_is_blocked(ipaddress.IPv6Address("2606:4700::6810:85e5")) is False

    def test_reserved_test_net_blocked(self) -> None:

        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("192.0.2.1")) is True  # TEST-NET-1

    def test_unspecified_blocked(self) -> None:

        assert m._ip_addr_is_blocked(ipaddress.IPv4Address("0.0.0.0")) is True


class TestSingleLabelHostname:
    """m._is_single_label_hostname classification."""

    def test_single_label_true(self) -> None:
        assert m._is_single_label_hostname("vault") is True
        assert m._is_single_label_hostname("grafana") is True

    def test_multi_label_false(self) -> None:
        assert m._is_single_label_hostname("api.example.com") is False

    def test_ip_literal_false(self) -> None:
        assert m._is_single_label_hostname("127.0.0.1") is False
        assert m._is_single_label_hostname("::1") is False

    def test_empty_string_false(self) -> None:
        assert m._is_single_label_hostname("") is True
