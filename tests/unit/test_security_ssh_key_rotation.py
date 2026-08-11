"""Deep tests for ssh_key_rotation — key generation, listing, scrubbing, rotation history."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from general_ludd.security.ssh_key_rotation import (
    DEFAULT_KEY_TYPE,
    RotationEvent,
    generate_key_pair,
    list_keys,
    read_key_metadata,
    record_rotation,
    rotation_history,
    scrub_key,
)


class TestGenerateKeyPair:
    def test_generates_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            meta = generate_key_pair("test-key", keystore_dir=d)
            assert Path(d, "test-key").exists()
            assert Path(d, "test-key.pub").exists()
            assert meta.name == "test-key"

    def test_private_key_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_key_pair("test-key", keystore_dir=d)
            priv = Path(d, "test-key")
            mode = priv.stat().st_mode & 0o777
            assert mode == 0o600

    def test_duplicate_key_raises(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_key_pair("dup-key", keystore_dir=d)
            with pytest.raises(FileExistsError, match="already exists"):
                generate_key_pair("dup-key", keystore_dir=d)

    def test_duplicate_private_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            priv = Path(d, "test-key")
            priv.write_text("stub")
            with pytest.raises(FileExistsError, match="already exists"):
                generate_key_pair("test-key", keystore_dir=d)

    def test_duplicate_public_only(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pub = Path(d, "test-key.pub")
            pub.write_text("stub")
            with pytest.raises(FileExistsError, match="already exists"):
                generate_key_pair("test-key", keystore_dir=d)

    def test_custom_key_type(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            meta = generate_key_pair("test-key", key_type="rsa", bits=4096, keystore_dir=d)
            priv_content = (Path(d) / "test-key").read_text()
            assert "rsa" in priv_content.lower()
            assert meta.name == "test-key"

    def test_bits_out_of_range_low(self) -> None:
        with tempfile.TemporaryDirectory() as d, pytest.raises(ValueError, match="bits"):
            generate_key_pair("test-key", bits=-1, keystore_dir=d)

    def test_bits_out_of_range_high(self) -> None:
        with tempfile.TemporaryDirectory() as d, pytest.raises(ValueError, match="bits"):
            generate_key_pair("test-key", bits=99999, keystore_dir=d)

    def test_default_key_type(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_key_pair("test-key", keystore_dir=d)
            pub = Path(d, "test-key.pub").read_text()
            assert pub.startswith(DEFAULT_KEY_TYPE)

    def test_key_metadata_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            meta = generate_key_pair("mykey", keystore_dir=d)
            assert "mykey" in meta.fingerprint
            assert "SHA256:stub" in meta.fingerprint


class TestListKeys:
    def test_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            keys = list_keys(keystore_dir=d)
            assert keys == []

    def test_lists_keys(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_key_pair("key1", keystore_dir=d)
            generate_key_pair("key2", keystore_dir=d)
            keys = list_keys(keystore_dir=d)
            assert len(keys) == 2
            names = {k.name for k in keys}
            assert names == {"key1", "key2"}

    def test_ignores_non_pub_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_key_pair("key1", keystore_dir=d)
            Path(d, "notes.txt").write_text("random")
            keys = list_keys(keystore_dir=d)
            assert len(keys) == 1


class TestReadKeyMetadata:
    def test_read_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            file_bytes = b"ed25519 AAA...stub... user@key1-521\n"
            pub_path = Path(d, "key1.pub")
            pub_path.write_bytes(file_bytes)
            meta = read_key_metadata(pub_path)
            assert meta is not None
            assert meta.name == "key1"
            assert "user@" in meta.fingerprint

    def test_nonexistent_file(self) -> None:
        assert read_key_metadata(Path("/nonexistent/key.pub")) is None

    def test_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            pub_path = Path(d, "key.pub")
            pub_path.write_text("")
            meta = read_key_metadata(pub_path)
            assert meta is not None
            assert meta.fingerprint == "unknown"


class TestScrubKey:
    def test_scrub_existing(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            generate_key_pair("key1", keystore_dir=d)
            assert Path(d, "key1").exists()
            result = scrub_key("key1", keystore_dir=d)
            assert result
            assert not Path(d, "key1").exists()
            assert not Path(d, "key1.pub").exists()

    def test_scrub_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            result = scrub_key("nonexistent", keystore_dir=d)
            assert not result

    def test_scrub_partial(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            Path(d, "key1.pub").write_text("stub")
            result = scrub_key("key1", keystore_dir=d)
            assert result
            assert not Path(d, "key1.pub").exists()


class TestRotationRecord:
    def test_record_and_read_history(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            event = RotationEvent(
                key_name="mykey",
                fingerprint="SHA256:abc",
                rotated_at=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
                old_fingerprints=["SHA256:def"],
            )
            record_rotation(event, keystore_dir=d)
            events = rotation_history(keystore_dir=d)
            assert len(events) == 1
            assert events[0].key_name == "mykey"
            assert events[0].fingerprint == "SHA256:abc"
            assert events[0].old_fingerprints == ["SHA256:def"]

    def test_empty_history(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            events = rotation_history(keystore_dir=d)
            assert events == []

    def test_multiple_events(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            for i in range(3):
                event = RotationEvent(
                    key_name=f"key{i}",
                    fingerprint=f"SHA256:{i}",
                )
                record_rotation(event, keystore_dir=d)
            events = rotation_history(keystore_dir=d)
            assert len(events) == 3

    def test_corrupt_line_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            history_path = Path(d, "rotation_history.txt")
            history_path.write_text("bad-line\n")
            events = rotation_history(keystore_dir=d)
            assert len(events) == 0

    def test_record_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            base = Path(d, "nested", "ssh")
            base.mkdir(parents=True)
            event = RotationEvent(key_name="k", fingerprint="SHA256:x")
            record_rotation(event, keystore_dir=base)
            assert (base / "rotation_history.txt").exists()
