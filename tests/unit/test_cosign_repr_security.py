"""Regression tests: CosignKey must not leak private_key or password via repr/str."""
from general_ludd.secrets.cosign import CosignKey


def test_repr_does_not_leak_private_key():
    key = CosignKey(
        key_name="test-key",
        private_key="SUPERSECRET_PK",  # pragma: allowlist secret
        public_key="public-data",
        password="SUPERSECRET_PW",  # pragma: allowlist secret
    )
    r = repr(key)
    assert "SUPERSECRET_PK" not in r, f"private_key leaked in repr: {r}"
    assert "SUPERSECRET_PW" not in r, f"password leaked in repr: {r}"


def test_str_does_not_leak_private_key():
    key = CosignKey(
        key_name="test-key",
        private_key="SUPERSECRET_PK",  # pragma: allowlist secret
        public_key="public-data",
        password="SUPERSECRET_PW",  # pragma: allowlist secret
    )
    s = str(key)
    assert "SUPERSECRET_PK" not in s, f"private_key leaked in str: {s}"
    assert "SUPERSECRET_PW" not in s, f"password leaked in str: {s}"


def test_fstring_does_not_leak_private_key():
    key = CosignKey(
        key_name="test-key",
        private_key="SUPERSECRET_PK",  # pragma: allowlist secret
        public_key="public-data",
        password="SUPERSECRET_PW",  # pragma: allowlist secret
    )
    s = f"key={key}"
    assert "SUPERSECRET_PK" not in s, f"private_key leaked in f-string: {s}"
    assert "SUPERSECRET_PW" not in s, f"password leaked in f-string: {s}"


def test_non_secret_fields_still_visible_in_repr():
    """Non-secret fields should still appear in repr for debuggability."""
    key = CosignKey(
        key_name="my-signing-key",
        private_key="SUPERSECRET_PK",  # pragma: allowlist secret
        public_key="public-data",
        password="SUPERSECRET_PW",  # pragma: allowlist secret
    )
    r = repr(key)
    assert "my-signing-key" in r, f"key_name missing from repr: {r}"
    assert "public-data" in r, f"public_key missing from repr: {r}"


def test_equality_still_works_with_repr_false():
    """field(repr=False) must not break equality comparison."""
    key1 = CosignKey(
        key_name="k",
        private_key="SUPERSECRET_PK",  # pragma: allowlist secret
        public_key="pub",
        password="SUPERSECRET_PW",  # pragma: allowlist secret
    )
    key2 = CosignKey(
        key_name="k",
        private_key="SUPERSECRET_PK",  # pragma: allowlist secret
        public_key="pub",
        password="SUPERSECRET_PW",  # pragma: allowlist secret
    )
    assert key1 == key2


def test_field_access_still_works():
    """Field values must still be accessible normally after repr=False."""
    key = CosignKey(
        key_name="k",
        private_key="SUPERSECRET_PK",  # pragma: allowlist secret
        public_key="pub",
        password="SUPERSECRET_PW",  # pragma: allowlist secret
    )
    assert key.private_key == "SUPERSECRET_PK"  # pragma: allowlist secret
    assert key.password == "SUPERSECRET_PW"  # pragma: allowlist secret
