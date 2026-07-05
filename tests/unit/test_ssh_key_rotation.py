"""Tests for SSH key rotation module."""

from __future__ import annotations

import tempfile
from datetime import UTC
from pathlib import Path

import pytest

from general_ludd.security.ssh_key_rotation import (
    KeyMetadata,
    RotationEvent,
    generate_key_pair,
    list_keys,
    read_key_metadata,
    record_rotation,
    rotation_history,
    scrub_key,
)


class TestKeyMetadata:
    def test_fields(self) -> None:
        from datetime import datetime
        now = datetime.now(UTC)
        m = KeyMetadata(
            name="deploy-key",
            fingerprint="SHA256:abc123",
            created_at=now,
        )
        assert m.name == "deploy-key"
        assert m.fingerprint == "SHA256:abc123"
        assert m.created_at == now
        assert m.rotated_at is None


class TestRotationEvent:
    def test_fields(self) -> None:
        from datetime import datetime
        now = datetime.now(UTC)
        ev = RotationEvent(
            key_name="deploy-key",
            fingerprint="SHA256:new",
            rotated_at=now,
            old_fingerprints=["SHA256:old1", "SHA256:old2"],
        )
        assert ev.key_name == "deploy-key"
        assert ev.fingerprint == "SHA256:new"
        assert ev.old_fingerprints == ["SHA256:old1", "SHA256:old2"]


class TestGenerateKeyPair:
    def test_generates_private_and_public_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            meta = generate_key_pair("deploy-key", keystore_dir=td)
            assert meta.name == "deploy-key"
            assert "SHA256:stub" in meta.fingerprint
            private = Path(td) / "deploy-key"
            public = Path(td) / "deploy-key.pub"
            assert private.is_file()
            assert public.is_file()
            assert private.read_text().startswith("-----BEGIN")
            assert public.read_text().startswith("ed25519")

    def test_refuses_duplicate_key_name(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            generate_key_pair("unique-key", keystore_dir=td)
            with pytest.raises(FileExistsError, match="already exists"):
                generate_key_pair("unique-key", keystore_dir=td)

    def test_validates_bit_range(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with pytest.raises(ValueError, match="bits must be"):
                generate_key_pair("bad-key", bits=-1, keystore_dir=td)
            with pytest.raises(ValueError, match="bits must be"):
                generate_key_pair("bad-key", bits=99999, keystore_dir=td)

    def test_private_key_has_restrictive_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            generate_key_pair("deploy-key", keystore_dir=td)
            private = Path(td) / "deploy-key"
            mode = private.stat().st_mode & 0o777
            assert mode == 0o600


class TestListKeys:
    def test_empty_directory_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            keys = list_keys(keystore_dir=td)
            assert keys == []

    def test_lists_generated_keys(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            generate_key_pair("k1", keystore_dir=td)
            generate_key_pair("k2", keystore_dir=td)
            keys = list_keys(keystore_dir=td)
            assert len(keys) == 2
            names = {k.name for k in keys}
            assert names == {"k1", "k2"}


class TestReadKeyMetadata:
    def test_returns_none_for_nonexistent_file(self) -> None:
        assert read_key_metadata(Path("/nonexistent/key.pub")) is None

    def test_reads_valid_pub_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            generate_key_pair("read-test", keystore_dir=td)
            meta = read_key_metadata(Path(td) / "read-test.pub")
            assert meta is not None
            assert meta.name == "read-test"


class TestScrubKey:
    def test_removes_private_and_public(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            generate_key_pair("scrub-me", keystore_dir=td)
            assert scrub_key("scrub-me", keystore_dir=td) is True
            assert not (Path(td) / "scrub-me").exists()
            assert not (Path(td) / "scrub-me.pub").exists()

    def test_missing_key_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            assert scrub_key("does-not-exist", keystore_dir=td) is False


class TestRotationHistory:
    def test_empty_when_no_history_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            assert rotation_history(keystore_dir=td) == []

    def test_empty_lines_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            hist = Path(td) / "rotation_history.txt"
            hist.write_text("\n\n")
            assert rotation_history(keystore_dir=td) == []

    def test_records_and_reads_events(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            from datetime import datetime
            now = datetime.now(UTC)
            ev = RotationEvent(
                key_name="deploy-key",
                fingerprint="SHA256:new",
                rotated_at=now,
                old_fingerprints=["SHA256:old1"],
            )
            record_rotation(ev, keystore_dir=td)
            events = rotation_history(keystore_dir=td)
            assert len(events) == 1
            assert events[0].key_name == "deploy-key"
            assert events[0].fingerprint == "SHA256:new"
            assert events[0].old_fingerprints == ["SHA256:old1"]


class TestGenerateKeyPairCustomType:
    def test_rsa_key_generation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            meta = generate_key_pair("rsa-key", key_type="rsa", bits=4096, keystore_dir=td)
            assert meta.name == "rsa-key"
            private = Path(td) / "rsa-key"
            assert private.is_file()
            content = private.read_text()
            assert "rsa" in content
            assert "4096" in content

    def test_defaults_to_ed25519(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            generate_key_pair("default-key", keystore_dir=td)
            private = Path(td) / "default-key"
            content = private.read_text()
            assert "ed25519" in content
