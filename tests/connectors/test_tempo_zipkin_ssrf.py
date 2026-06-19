"""SSRF guard tests for TempoZipkinSource / _assert_public_base_url."""

import pytest

from general_ludd.connectors.tempo_zipkin import _assert_public_base_url


@pytest.mark.parametrize("url", sorted([
    "http://localhost/api",
    "http://localhost.localdomain/api",
    "http://metadata/api",
    "http://metadata.google.internal/api",
    "http://127.0.0.1/api",
    "http://169.254.169.254/api",
    "http://10.0.0.1/api",
    "http://192.168.1.1/api",
    "http://::1/api",
    "http://[::1]/api",
]))
def test_blocked_url(url: str) -> None:
    with pytest.raises(ValueError):
        _assert_public_base_url(url)


@pytest.mark.parametrize("url", sorted([
    "https://tempo.example.com",
    "https://zipkin.prod.myco.io:9411",
    "http://1.2.3.4:9411",
]))
def test_allowed_public_url(url: str) -> None:
    _assert_public_base_url(url)  # must not raise


def test_bad_scheme_rejected() -> None:
    with pytest.raises(ValueError):
        _assert_public_base_url("ftp://tempo.example.com")


def test_allow_private_skips_ip_block() -> None:
    # With allow_private=True, a private IP should be allowed
    _assert_public_base_url("http://10.0.0.1/api", allow_private=True)


def test_allow_private_still_blocks_bad_scheme() -> None:
    with pytest.raises(ValueError):
        _assert_public_base_url("ftp://10.0.0.1/api", allow_private=True)
