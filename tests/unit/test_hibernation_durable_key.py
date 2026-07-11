"""Tests for HibernationStore durable MAC key.

Key survives roundtrip; MAC verifies across two stores; bad key fails closed.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

from general_ludd.agents.hibernation import (
    AgentEnvironmentSnapshot,
    HibernationStore,
    IntegrityError,
    _load_hibernate_mac_key,
)


def _make_snapshot(task_id: str = "TASK-DURABLE") -> AgentEnvironmentSnapshot:
    from general_ludd.agents.context import ContextMessage

    return AgentEnvironmentSnapshot(
        task_id=task_id,
        agent_name="coder",
        depth=3,
        workspace_path="/tmp/test",
        messages=[
            ContextMessage(
                role="user",
                content="hello durable mac",
                token_estimate=4,
                is_system=False,
                timestamp=1.0,
            ),
        ],
    )


class TestHibernateDurableKeyRoundtrip:
    def test_durable_key_survives_roundtrip(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        key = _load_hibernate_mac_key(str(base))
        assert key is not None
        assert isinstance(key, bytes)
        assert len(key) == 32

        store_a = HibernationStore(base_dir=str(base), mac_key=key)
        snap = _make_snapshot("TASK-ROUND")
        handle = store_a.dehydrate(snap)

        store_b = HibernationStore(base_dir=str(base), mac_key=key)
        restored = store_b.hydrate(handle)
        assert restored.task_id == "TASK-ROUND"
        assert restored.agent_name == "coder"
        assert len(restored.messages) == 1
        assert restored.messages[0].content == "hello durable mac"

    def test_mac_verifies_across_two_stores_same_key(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        key = _load_hibernate_mac_key(str(base))
        assert key is not None

        store_a = HibernationStore(base_dir=str(base), mac_key=key)
        snap = _make_snapshot("TASK-MAC2")
        handle = store_a.dehydrate(snap)

        store_b = HibernationStore(base_dir=str(base), mac_key=key)
        restored = store_b.hydrate(handle)
        assert restored.task_id == "TASK-MAC2"

    def test_different_key_causes_integrity_error(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        key_a = _load_hibernate_mac_key(str(base))
        assert key_a is not None

        store_a = HibernationStore(base_dir=str(base), mac_key=key_a)
        snap = _make_snapshot("TASK-DIFFER")
        handle = store_a.dehydrate(snap)

        bad_key = os.urandom(32)
        store_b = HibernationStore(base_dir=str(base), mac_key=bad_key)
        with pytest.raises(IntegrityError, match="checksum mismatch"):
            store_b.hydrate(handle)

    def test_ephemeral_key_when_mac_is_none(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        store = HibernationStore(base_dir=str(base), mac_key=None)
        snap = _make_snapshot("TASK-EPHEMERAL")
        handle = store.dehydrate(snap)
        restored = store.hydrate(handle)
        assert restored.task_id == "TASK-EPHEMERAL"

    def test_ephemeral_key_not_portable(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        store_a = HibernationStore(base_dir=str(base), mac_key=None)
        snap = _make_snapshot("TASK-EPHEM2")
        handle = store_a.dehydrate(snap)

        store_b = HibernationStore(base_dir=str(base), mac_key=None)
        with pytest.raises(IntegrityError, match="checksum mismatch"):
            store_b.hydrate(handle)


class TestLoadHibernateMacKeyFailClosed:
    def test_mints_key_on_fresh_store(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        key = _load_hibernate_mac_key(str(base))
        assert key is not None
        key_path = base / "secrets" / "hibernate_mac.key"
        assert key_path.exists()
        key_bytes = key_path.read_bytes()
        assert len(key_bytes) == 32
        assert key_bytes == key

    def test_reloads_existing_key(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        key1 = _load_hibernate_mac_key(str(base))
        key2 = _load_hibernate_mac_key(str(base))
        assert key1 == key2

    def test_fails_closed_on_missing_key_with_prior_evidence(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        key = _load_hibernate_mac_key(str(base))
        assert key is not None

        store = HibernationStore(base_dir=str(base), mac_key=key)
        snap = _make_snapshot("TASK-FC1")
        handle = store.dehydrate(snap)
        assert "TASK-FC1" in handle.path

        secrets_dir = base / "secrets"
        key_path = secrets_dir / "hibernate_mac.key"
        key_path.unlink()
        assert not key_path.exists()

        with pytest.raises(IntegrityError, match="missing but a prior"):
            _load_hibernate_mac_key(str(base))

    def test_fails_closed_on_insecure_keyfile(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        key = _load_hibernate_mac_key(str(base))
        assert key is not None

        key_path = base / "secrets" / "hibernate_mac.key"
        with contextlib.suppress(OSError):
            os.chmod(key_path, 0o644)

        with pytest.raises(IntegrityError, match="group/world accessible"):
            _load_hibernate_mac_key(str(base))

    def test_degraded_mode_on_oserror_no_prior_evidence(self, tmp_path: Path):
        base = tmp_path / "hibernate"
        base.mkdir()
        secrets_dir = base / "secrets"
        secrets_dir.mkdir(exist_ok=True)
        key_path = secrets_dir / "hibernate_mac.key"
        key_path.mkdir(exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(key_path, 0o700)

        result = _load_hibernate_mac_key(str(base))
        assert result is None
