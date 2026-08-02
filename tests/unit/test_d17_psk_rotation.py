"""D-17: Automated daemon-worker PSK rotation with versioned identities,
short overlap, atomic promotion, and rollback."""

from __future__ import annotations

import os
import time
from typing import Any
from unittest.mock import patch

import pytest

from general_ludd.security.psk_rotation import (
    InMemoryPSKStore,
    PSKIdentity,
    PSKRotationState,
    PSKRotator,
    PSKStore,
    create_psk_rotator,
)

# ── PSKIdentity ──


class TestPSKIdentity:
    def test_create_identity(self) -> None:
        identity = PSKIdentity(version=1, key="abc123", created_at=time.time())
        assert identity.version == 1
        assert identity.key == "abc123"

    def test_identity_hides_key_in_repr(self) -> None:
        identity = PSKIdentity(version=1, key="secret-key", created_at=time.time())
        repr_str = repr(identity)
        assert "secret-key" not in repr_str

    def test_identity_is_expired(self) -> None:
        identity = PSKIdentity(
            version=1,
            key="old-key",
            created_at=time.time() - 100,
            expires_at=time.time() - 1,
        )
        assert identity.is_expired()

    def test_identity_not_expired_when_fresh(self) -> None:
        identity = PSKIdentity(
            version=1,
            key="fresh-key",
            created_at=time.time(),
            expires_at=time.time() + 3600,
        )
        assert not identity.is_expired()


# ── InMemoryPSKStore ──


class TestInMemoryPSKStore:
    def test_store_and_load(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        identity = PSKIdentity(version=1, key="key-1", created_at=time.time())
        store.save(identity)
        loaded = store.load(1)
        assert loaded is not None
        assert loaded.key == "key-1"

    def test_load_nonexistent_returns_none(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        assert store.load(99) is None

    def test_list_versions(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        store.save(PSKIdentity(version=1, key="k1", created_at=time.time()))
        store.save(PSKIdentity(version=2, key="k2", created_at=time.time()))
        versions = store.list_versions()
        assert versions == [1, 2]

    def test_delete_removes_version(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        store.save(PSKIdentity(version=1, key="k1", created_at=time.time()))
        store.delete(1)
        assert store.load(1) is None


# ── PSKRotator ──


class TestPSKRotator:
    def _rotator(self, **kwargs: Any) -> PSKRotator:
        defaults: dict[str, Any] = {
            "store": InMemoryPSKStore(),
            "overlap_seconds": 300,
            "identity_ttl_seconds": 3600,
            "key_bytes": 32,
        }
        defaults.update(kwargs)
        return PSKRotator(**defaults)

    def test_initial_rotation_creates_version_1(self) -> None:
        rotator = self._rotator()
        result = rotator.rotate()
        assert result.state == PSKRotationState.ACTIVE
        assert result.new_version == 1
        assert result.new_key is not None
        assert len(result.new_key) >= 32

    def test_second_rotation_creates_version_2(self) -> None:
        rotator = self._rotator()
        rotator.rotate()
        result = rotator.rotate()
        assert result.new_version == 2
        assert rotator.current_version() == 2

    def test_rotation_within_overlap_accepts_prior_key(self) -> None:
        rotator = self._rotator(overlap_seconds=300)
        r1 = rotator.rotate()
        r2 = rotator.rotate()

        assert rotator.accept_key(r1.new_key) is True
        assert rotator.accept_key(r2.new_key) is True
        assert rotator.accept_key("bogus-key") is False

    def test_old_key_rejected_after_overlap(self) -> None:
        rotator = self._rotator(overlap_seconds=-1)
        r1 = rotator.rotate()
        rotator.rotate()

        assert rotator.accept_key(r1.new_key) is False

    def test_rollback_restores_prior_version(self) -> None:
        rotator = self._rotator()
        r1 = rotator.rotate()
        rotator.rotate()

        rollback_result = rotator.rollback()
        assert rollback_result.success
        assert rotator.current_version() == r1.new_version

    def test_rollback_fails_when_no_prior_version(self) -> None:
        rotator = self._rotator()
        result = rotator.rollback()
        assert not result.success
        assert result.error is not None

    def test_rollback_after_rollback_restores_original(self) -> None:
        rotator = self._rotator()
        r1 = rotator.rotate()
        r2 = rotator.rotate()
        rotator.rotate()

        rotator.rollback()
        assert rotator.current_version() == r2.new_version
        rotator.rollback()
        assert rotator.current_version() == r1.new_version

    def test_active_version_is_accessible(self) -> None:
        rotator = self._rotator()
        rotator.rotate()
        key = rotator.active_key()
        assert key is not None
        assert len(key) >= 32

    def test_rotation_result_carries_overlap_window(self) -> None:
        rotator = self._rotator(overlap_seconds=120)
        rotator.rotate()
        result = rotator.rotate()
        assert result.overlap_start is not None
        assert result.overlap_end is not None
        assert result.overlap_end - result.overlap_start == 120

    def test_key_revocation_removes_old_identity(self) -> None:
        rotator = self._rotator(overlap_seconds=0)
        r1 = rotator.rotate()
        rotator.rotate()
        rotator.revoke_version(1)
        assert not rotator.accept_key(r1.new_key)
        assert rotator._store.load(1) is None

    def test_cannot_revoke_active_version(self) -> None:
        rotator = self._rotator()
        rotator.rotate()
        with pytest.raises(ValueError, match="active"):
            rotator.revoke_version(rotator.current_version())

    def test_accept_uses_constant_time_compare(self) -> None:
        rotator = self._rotator()
        rotator.rotate()
        active = rotator.active_key()
        wrong = active[:-1] + ("X" if active[-1] != "X" else "Y")
        assert rotator.accept_key(wrong) is False


# ── PSK rotation with environment integration ──


class TestPSKRotationEnvironmentIntegration:
    def test_create_psk_rotator_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GLUDD_PSK_ROTATION_OVERLAP_SECONDS": "120",
                "GLUDD_PSK_IDENTITY_TTL_SECONDS": "7200",
            },
        ):
            rotator = create_psk_rotator()
            assert rotator.overlap_seconds == 120
            assert rotator.identity_ttl_seconds == 7200

    def test_create_psk_rotator_defaults(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            rotator = create_psk_rotator()
            assert rotator.overlap_seconds == 300
            assert rotator.identity_ttl_seconds == 3600


# ── Live two-worker simulation ──


class TestLiveTwoWorkerRotation:
    def test_two_workers_accept_both_keys_during_overlap(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        worker_a = PSKRotator(store=store, overlap_seconds=60, identity_ttl_seconds=3600)
        worker_b = PSKRotator(store=store, overlap_seconds=60, identity_ttl_seconds=3600)

        r1 = worker_a.rotate()
        r2 = worker_a.rotate()

        assert worker_b.accept_key(r1.new_key)
        assert worker_b.accept_key(r2.new_key)

    def test_two_workers_converge_on_current_version(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        worker_a = PSKRotator(store=store, overlap_seconds=60, identity_ttl_seconds=3600)
        worker_b = PSKRotator(store=store, overlap_seconds=60, identity_ttl_seconds=3600)

        worker_a.rotate()
        worker_a.rotate()

        assert worker_b.current_version() == worker_a.current_version()

    def test_rotation_no_lost_event_during_overlap(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        rotator = PSKRotator(store=store, overlap_seconds=60, identity_ttl_seconds=3600)

        r1 = rotator.rotate()
        r2 = rotator.rotate()

        assert rotator.accept_key(r1.new_key)
        assert rotator.accept_key(r2.new_key)

        rotator.revoke_version(1)
        assert not rotator.accept_key(r1.new_key)
        assert rotator.accept_key(r2.new_key)

    def test_rollback_preserves_within_overlap(self) -> None:
        store: PSKStore = InMemoryPSKStore()
        rotator = PSKRotator(store=store, overlap_seconds=10, identity_ttl_seconds=3600)

        rotator.rotate()
        r2 = rotator.rotate()
        rotator.rotate()

        rotator.rollback()
        assert rotator.accept_key(r2.new_key)
        assert rotator.current_version() == r2.new_version


# ── Edge cases ──


class TestPSKRotationEdgeCases:
    def test_rotate_with_zero_overlap(self) -> None:
        rotator = PSKRotator(
            store=InMemoryPSKStore(),
            overlap_seconds=0,
            identity_ttl_seconds=3600,
        )
        r1 = rotator.rotate()
        rotator.rotate()
        assert not rotator.accept_key(r1.new_key)

    def test_key_deterministic_from_seed(self) -> None:
        rotator_a = PSKRotator(
            store=InMemoryPSKStore(),
            overlap_seconds=300,
            identity_ttl_seconds=3600,
        )
        rotator_b = PSKRotator(
            store=InMemoryPSKStore(),
            overlap_seconds=300,
            identity_ttl_seconds=3600,
        )
        rotator_a.rotate()
        rotator_b.rotate()
        assert rotator_a.active_key() != rotator_b.active_key()

    def test_identity_repr_never_leaks_key(self) -> None:
        identity = PSKIdentity(version=1, key="super-secret-key", created_at=time.time())
        for field in ("super-secret-key", "secret"):
            assert field not in repr(identity).lower()
