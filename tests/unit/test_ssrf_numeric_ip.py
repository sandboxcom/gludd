from __future__ import annotations

from general_ludd.security.ssrf import _nonstandard_ip_blocked, host_is_blocked, is_url_blocked


class TestDecimalIPEncoding:
    def test_loopback_decimal(self):
        assert _nonstandard_ip_blocked("2130706433")
        assert host_is_blocked("2130706433")

    def test_private_10_decimal(self):
        assert _nonstandard_ip_blocked("167772161")
        assert host_is_blocked("167772161")

    def test_private_192_168_decimal(self):
        assert _nonstandard_ip_blocked("3232235521")
        assert host_is_blocked("3232235521")

    def test_private_172_16_decimal(self):
        assert _nonstandard_ip_blocked("2886729729")
        assert host_is_blocked("2886729729")

    def test_non_blocked_decimal_ip_value(self):
        assert not _nonstandard_ip_blocked("134744072")  # 8.8.8.8

    def test_decimal_in_url(self):
        assert is_url_blocked("http://2130706433/")
        assert is_url_blocked("http://2130706433:8080/api")


class TestOctalDottedQuad:
    def test_loopback_octal(self):
        assert _nonstandard_ip_blocked("0177.0.0.1")
        assert host_is_blocked("0177.0.0.1")

    def test_private_10_octal(self):
        assert _nonstandard_ip_blocked("012.0.0.1")  # octal 10.0.0.1
        assert host_is_blocked("012.0.0.1")

    def test_private_172_octal(self):
        assert _nonstandard_ip_blocked("0254.020.0.1")  # octal 172.16.0.1

    def test_private_192_octal(self):
        assert _nonstandard_ip_blocked("0300.0250.0.1")  # octal 192.168.0.1

    def test_octal_in_url(self):
        assert is_url_blocked("http://0177.0.0.1/")
        assert is_url_blocked("http://0177.0.0.1:8080/admin")

    def test_non_blocked_octal(self):
        assert not _nonstandard_ip_blocked("011.0.0.1")  # octal 9, decimal 11 — both public
        assert not host_is_blocked("011.0.0.1")

    def test_octal_with_trailing_dot(self):
        assert host_is_blocked("0177.0.0.1.")


class TestHexDottedQuad:
    def test_loopback_hex(self):
        assert _nonstandard_ip_blocked("0x7f.0.0.1")
        assert host_is_blocked("0x7f.0.0.1")

    def test_loopback_all_hex(self):
        assert _nonstandard_ip_blocked("0x7f.0x00.0x00.0x01")
        assert host_is_blocked("0x7f.0x00.0x00.0x01")

    def test_private_10_hex(self):
        assert _nonstandard_ip_blocked("0xa.0x0.0x0.0x1")
        assert host_is_blocked("0xa.0x0.0x0.0x1")

    def test_hex_uppercase(self):
        assert _nonstandard_ip_blocked("0X7F.0.0.1")
        assert host_is_blocked("0X7F.0.0.1")

    def test_hex_in_url(self):
        assert is_url_blocked("http://0x7f.0.0.1/")
        assert is_url_blocked("http://0x7f.0.0.1/api")

    def test_non_blocked_hex(self):
        assert not _nonstandard_ip_blocked("0x8.0x8.0x8.0x8")  # 8.8.8.8
        assert not host_is_blocked("0x8.0x8.0x8.0x8")


class TestMixedEncodings:
    def test_mixed_octal_hex_decimal(self):
        assert _nonstandard_ip_blocked("0177.0x1f.0.1")

    def test_mixed_octal_decimal(self):
        assert _nonstandard_ip_blocked("0177.0.0.1")

    def test_mixed_hex_decimal(self):
        assert _nonstandard_ip_blocked("0x7f.0.1.0")


class TestStandardIPsUnaffected:
    def test_standard_loopback(self):
        assert host_is_blocked("127.0.0.1")

    def test_standard_private(self):
        assert host_is_blocked("10.0.0.1")

    def test_standard_public(self):
        assert not host_is_blocked("8.8.8.8")
        assert not host_is_blocked("1.2.3.4")

    def test_standard_ipv6_loopback(self):
        assert host_is_blocked("::1")


class TestAmbiguousOctalDecimal:
    def test_ambiguous_octal_yields_blocked_via_decimal(self):
        assert host_is_blocked("010.0.0.1")

    def test_ambiguous_octal_not_blocked_either_way(self):
        assert not host_is_blocked("011.0.0.1")
