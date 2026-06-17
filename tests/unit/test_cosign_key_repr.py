"""Tests that CosignKey repr hides private_key and password but attrs are readable."""

from general_ludd.secrets.cosign import CosignKey

_PRIVKEY = "SUPERSECRET_PRIVKEY"  # pragma: allowlist secret
_PASSWORD = "MyPassword123"  # pragma: allowlist secret


def test_repr_hides_private_key() -> None:
    key = CosignKey(key_name="k", private_key=_PRIVKEY, public_key="pubkey")
    r = repr(key)
    assert _PRIVKEY not in r


def test_repr_hides_password() -> None:
    key = CosignKey(key_name="k", private_key="pk", public_key="pub", password=_PASSWORD)
    r = repr(key)
    assert _PASSWORD not in r


def test_repr_shows_key_name_and_public_key() -> None:
    key = CosignKey(key_name="mykey", private_key="pk", public_key="pub")
    r = repr(key)
    assert "mykey" in r
    assert "pub" in r


def test_private_key_attr_still_readable() -> None:
    key = CosignKey(key_name="k", private_key=_PRIVKEY, public_key="pub")
    assert key.private_key == _PRIVKEY


def test_password_attr_still_readable() -> None:
    key = CosignKey(key_name="k", private_key="pk", public_key="pub", password=_PASSWORD)
    assert key.password == _PASSWORD


def test_password_none_default() -> None:
    key = CosignKey(key_name="k", private_key="pk", public_key="pub")
    assert key.password is None


def test_equality_unchanged() -> None:
    key1 = CosignKey(key_name="k", private_key="pk", public_key="pub", password="pw")
    key2 = CosignKey(key_name="k", private_key="pk", public_key="pub", password="pw")
    assert key1 == key2


def test_inequality_on_private_key() -> None:
    key1 = CosignKey(key_name="k", private_key="pk1", public_key="pub")
    key2 = CosignKey(key_name="k", private_key="pk2", public_key="pub")
    assert key1 != key2
