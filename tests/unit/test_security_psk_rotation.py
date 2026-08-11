"""Deep tests for psk_rotation — PSK identity, rotation, overlap, rollback, acceptance."""

from __future__ import annotations

import time

import pytest

from general_ludd.security.psk_rotation import (
    InMemoryPSKStore,
    PSKIdentity,
    PSKRotationResult,
    PSKRotationState,
    PSKRotator,
    create_psk_rotator,
)


class TestPSKIdentity:
    def test_create(self) -> None:
        now = time.time()
        ident = PSKIdentity(version=1, key="secret-key-here", created_at=now)
        assert ident.version == 1
        assert ident.key == "secret-key-here"
        assert ident.created_at == now
        assert ident.expires_at == now + 3600

    def test_custom_expiry(self) -> None:
        now = time.time()
        ident = PSKIdentity(version=1, key="k", created_at=now, expires_at=now + 7200)
        assert ident.expires_at == now + 7200

    def test_expiry_before_created_rejected(self) -> None:
        now = time.time()
        with pytest.raises(ValueError, match="expires_at must be after"):
            PSKIdentity(version=1, key="k", created_at=now, expires_at=now - 1)

    def test_is_expired(self) -> None:
        now = time.time()
        ident = PSKIdentity(version=1, key="k", created_at=now - 100, expires_at=now - 1)
        assert ident.is_expired()

    def test_is_not_expired(self) -> None:
        now = time.time()
        ident = PSKIdentity(version=1, key="k", created_at=now, expires_at=now + 100)
        assert not ident.is_expired()

    def test_is_expired_with_custom_now(self) -> None:
        ident = PSKIdentity(version=1, key="k", created_at=100.0, expires_at=200.0)
        assert not ident.is_expired(now=150.0)
        assert ident.is_expired(now=200.0)
        assert ident.is_expired(now=201.0)

    def test_repr_hides_key(self) -> None:
        ident = PSKIdentity(version=1, key="secret", created_at=100.0)
        r = repr(ident)
        assert "secret" not in r
        assert "PSKIdentity" in r
        assert "version=1" in r

    def test_frozen(self) -> None:
        ident = PSKIdentity(version=1, key="k", created_at=time.time())
        with pytest.raises(AttributeError):
            ident.version = 2  # type: ignore[misc]


class TestInMemoryPSKStore:
    def test_save_load(self) -> None:
        store = InMemoryPSKStore()
        ident = PSKIdentity(version=1, key="k", created_at=time.time())
        store.save(ident)
        loaded = store.load(1)
        assert loaded is not None
        assert loaded.version == 1
        assert loaded.key == "k"

    def test_load_missing(self) -> None:
        store = InMemoryPSKStore()
        assert store.load(999) is None

    def test_list_versions_sorted(self) -> None:
        store = InMemoryPSKStore()
        store.save(PSKIdentity(version=5, key="a", created_at=time.time()))
        store.save(PSKIdentity(version=3, key="b", created_at=time.time()))
        assert store.list_versions() == [3, 5]

    def test_delete(self) -> None:
        store = InMemoryPSKStore()
        store.save(PSKIdentity(version=1, key="k", created_at=time.time()))
        store.delete(1)
        assert store.load(1) is None

    def test_delete_missing_noop(self) -> None:
        store = InMemoryPSKStore()
        store.delete(999)

    def test_empty_store(self) -> None:
        store = InMemoryPSKStore()
        assert store.list_versions() == []
        assert store.load(1) is None


class TestPSKRotatorRotate:
    @pytest.fixture
    def store(self) -> InMemoryPSKStore:
        return InMemoryPSKStore()

    @pytest.fixture
    def rotator(self, store: InMemoryPSKStore) -> PSKRotator:
        return PSKRotator(store)

    def test_first_rotation(self, rotator: PSKRotator) -> None:
        result = rotator.rotate()
        assert result.success
        assert result.state == PSKRotationState.ACTIVE
        assert result.new_version == 1
        assert result.prior_version == 0
        assert result.new_key
        assert len(result.new_key) == 64
        assert result.overlap_start is None
        assert result.overlap_end is None

    def test_second_rotation(self, rotator: PSKRotator) -> None:
        rotator.rotate()
        result = rotator.rotate()
        assert result.success
        assert result.new_version == 2
        assert result.prior_version == 1
        assert result.overlap_start is not None
        assert result.overlap_end is not None

    def test_rotation_increments_key(self, rotator: PSKRotator) -> None:
        r1 = rotator.rotate()
        r2 = rotator.rotate()
        assert r1.new_key != r2.new_key

    def test_current_version_after_rotate(self, rotator: PSKRotator) -> None:
        assert rotator.current_version() == 0
        rotator.rotate()
        assert rotator.current_version() == 1

    def test_active_key(self, rotator: PSKRotator) -> None:
        assert rotator.active_key() == ""
        r = rotator.rotate()
        assert rotator.active_key() == r.new_key


class TestPSKRotatorAcceptKey:
    @pytest.fixture
    def store(self) -> InMemoryPSKStore:
        return InMemoryPSKStore()

    @pytest.fixture
    def rotator(self, store: InMemoryPSKStore) -> PSKRotator:
        return PSKRotator(store, overlap_seconds=300)

    def test_accept_active_key(self, rotator: PSKRotator) -> None:
        r = rotator.rotate()
        assert rotator.accept_key(r.new_key)

    def test_accept_wrong_key(self, rotator: PSKRotator) -> None:
        rotator.rotate()
        assert not rotator.accept_key("wrong-key")

    def test_accept_empty_key(self, rotator: PSKRotator) -> None:
        rotator.rotate()
        assert not rotator.accept_key("")

    def test_accept_old_key_during_overlap(self, rotator: PSKRotator) -> None:
        r1 = rotator.rotate()
        r2 = rotator.rotate()
        assert rotator.accept_key(r2.new_key)
        assert rotator.accept_key(r1.new_key)

    def test_accept_old_key_during_overlap_period(self, rotator: PSKRotator) -> None:
        r1 = rotator.rotate()
        rotator.rotate()
        assert rotator.accept_key(r1.new_key)  # still within overlap window

    def test_accept_expired_old_key(self, store: InMemoryPSKStore) -> None:
        now = time.time()
        ident = PSKIdentity(version=1, key="old-key", created_at=now - 7200, expires_at=now - 3600)
        store.save(ident)
        store.save(PSKIdentity(version=2, key="new-key", created_at=now))
        rotator = PSKRotator(store, overlap_seconds=300)
        assert not rotator.accept_key("old-key")


class TestPSKRotatorRollback:
    @pytest.fixture
    def store(self) -> InMemoryPSKStore:
        return InMemoryPSKStore()

    @pytest.fixture
    def rotator(self, store: InMemoryPSKStore) -> PSKRotator:
        return PSKRotator(store)

    def test_rollback_no_prior(self, rotator: PSKRotator) -> None:
        result = rotator.rollback()
        assert not result.success
        assert result.state == PSKRotationState.ROLLBACK
        assert "no prior version" in (result.error or "")

    def test_rollback_success(self, rotator: PSKRotator) -> None:
        r1 = rotator.rotate()
        rotator.rotate()
        result = rotator.rollback()
        assert result.success
        assert result.new_version == 1
        assert result.new_key == r1.new_key

    def test_rollback_expired_prior(self, store: InMemoryPSKStore) -> None:
        now = time.time()
        store.save(PSKIdentity(version=1, key="old", created_at=now - 7200, expires_at=now - 3600))
        store.save(PSKIdentity(version=2, key="new", created_at=now))
        rotator = PSKRotator(store)
        result = rotator.rollback()
        assert not result.success
        assert "expired" in (result.error or "")


class TestPSKRotatorRevoke:
    @pytest.fixture
    def store(self) -> InMemoryPSKStore:
        return InMemoryPSKStore()

    @pytest.fixture
    def rotator(self, store: InMemoryPSKStore) -> PSKRotator:
        return PSKRotator(store)

    def test_revoke_non_active(self, rotator: PSKRotator) -> None:
        rotator.rotate()
        r2 = rotator.rotate()
        rotator.revoke_version(1)
        assert rotator.accept_key(r2.new_key)

    def test_cannot_revoke_active(self, rotator: PSKRotator) -> None:
        rotator.rotate()
        with pytest.raises(ValueError, match="cannot revoke"):
            rotator.revoke_version(1)


class TestCreatePskRotator:
    def test_default_creates(self) -> None:
        r = create_psk_rotator()
        assert isinstance(r, PSKRotator)

    def test_with_store(self) -> None:
        store = InMemoryPSKStore()
        r = create_psk_rotator(store=store)
        assert isinstance(r, PSKRotator)

    def test_env_overlap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_PSK_ROTATION_OVERLAP_SECONDS", "600")
        r = create_psk_rotator()
        assert r.overlap_seconds == 600

    def test_env_ttl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_PSK_IDENTITY_TTL_SECONDS", "7200")
        r = create_psk_rotator()
        assert r.identity_ttl_seconds == 7200

    def test_env_negative_overlap_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_PSK_ROTATION_OVERLAP_SECONDS", "-5")
        r = create_psk_rotator()
        assert r.overlap_seconds == 0

    def test_env_zero_ttl_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GLUDD_PSK_IDENTITY_TTL_SECONDS", "0")
        r = create_psk_rotator()
        assert r.identity_ttl_seconds == 3600


class TestPSKRotationResult:
    def test_defaults(self) -> None:
        r = PSKRotationResult(success=False, state=PSKRotationState.IDLE, error="nope")
        assert not r.success
        assert r.new_key == ""
        assert r.error == "nope"


class TestPSKRotationState:
    def test_enum_values(self) -> None:
        assert PSKRotationState.IDLE == "idle"
        assert PSKRotationState.ROTATING == "rotating"
        assert PSKRotationState.OVERLAP == "overlap"
        assert PSKRotationState.ACTIVE == "active"
        assert PSKRotationState.ROLLBACK == "rollback"
        assert PSKRotationState.REVOKED == "revoked"


class TestPSKKeyRandomness:
    def test_keys_unique(self) -> None:
        store = InMemoryPSKStore()
        rotator = PSKRotator(store)
        keys: set[str] = set()
        for _ in range(20):
            r = rotator.rotate()
            keys.add(r.new_key)
        assert len(keys) == 20

    def test_key_length(self) -> None:
        store = InMemoryPSKStore()
        rotator = PSKRotator(store, key_bytes=32)
        r = rotator.rotate()
        assert len(r.new_key) == 64
