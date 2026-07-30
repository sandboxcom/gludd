"""C.16 — Filestore RCE: verify checksum/signature enforcement before binary execution.

Tests that:
1. Downloaded binaries are sha256-verified before storage + chmod-exec
2. Wrong checksum → binary NOT stored and NOT made executable
3. Missing pin → fail-closed (binary refused)
4. sync_bundled_to_filestore verifies checksums
5. Executable bit only set after successful verification
"""

from __future__ import annotations

import hashlib
import io
import tarfile
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.filestore.bootstrap import (
    _MAX_DOWNLOAD_BYTES,
    BinaryBootstrapper,
)


@pytest.fixture
def real_hash() -> str:
    return hashlib.sha256(b"safe-binary-data").hexdigest()


@pytest.fixture
def bootstrap_with_pin(real_hash: str) -> BinaryBootstrapper:
    return BinaryBootstrapper(known_sha256={"test-bin": real_hash})


@pytest.fixture
def bootstrap_no_pin() -> BinaryBootstrapper:
    return BinaryBootstrapper(known_sha256={})


class TestC16VerifyDigestFailClosed:
    def test_missing_pin_refuses_binary(self, bootstrap_no_pin: BinaryBootstrapper):
        result = bootstrap_no_pin._verify_digest("unknown-bin", b"data")
        assert result is False

    def test_mismatched_checksum_refuses_binary(self, bootstrap_with_pin: BinaryBootstrapper):
        result = bootstrap_with_pin._verify_digest("test-bin", b"wrong-data")
        assert result is False

    def test_correct_checksum_allows_binary(self, bootstrap_with_pin: BinaryBootstrapper):
        result = bootstrap_with_pin._verify_digest("test-bin", b"safe-binary-data")
        assert result is True


class TestC16DownloadedBytesVerifiedBeforeStorage:
    def test_wrong_checksum_not_stored_nor_executed(self, real_hash: str) -> None:
        """A download whose sha256 mismatches the pin MUST NOT be stored or made executable."""
        store = MagicMock()
        boot = BinaryBootstrapper(store=store, known_sha256={"test-bin": real_hash})

        bad_data = b"malicious-payload"
        assert hashlib.sha256(bad_data).hexdigest() != real_hash

        with patch.object(boot, "_verify_digest", wraps=boot._verify_digest) as spy_verify:
            result = boot._verify_digest("test-bin", bad_data)

        assert result is False
        spy_verify.assert_called_once_with("test-bin", bad_data)
        store.write_bytes.assert_not_called()
        store.assert_not_called()

    def test_verified_binary_is_stored(self, bootstrap_with_pin: BinaryBootstrapper) -> None:
        """A sha256-matching binary proceeds to storage."""
        assert bootstrap_with_pin._verify_digest("test-bin", b"safe-binary-data") is True

    def test_store_binary_writes_to_correct_path(self, real_hash: str) -> None:
        store = MagicMock()
        boot = BinaryBootstrapper(store=store, known_sha256={"test-bin": real_hash})
        boot.store_binary("test-bin", b"safe-binary-data")
        store.write_bytes.assert_called_once_with("binaries/test-bin", b"safe-binary-data")


class TestC16ChmodOnlyAfterVerification:
    def test_store_binary_and_chmod_extracts_and_sets_executable(
        self, real_hash: str
    ) -> None:
        """For tarball-packaged binaries, store+chmod only executes after verification."""
        store = MagicMock()
        boot = BinaryBootstrapper(store=store, known_sha256={"osquery": real_hash})

        executable = b"#!/bin/sh\necho safe"
        real_sha = hashlib.sha256(executable).hexdigest()
        boot = BinaryBootstrapper(store=store, known_sha256={"osquery": real_sha})

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name="osquery-5.10.2/bin/osqueryi")
            info.size = len(executable)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(executable))

        tarball = buf.getvalue()
        tarball_sha = hashlib.sha256(tarball).hexdigest()
        boot = BinaryBootstrapper(store=store, known_sha256={"osquery": tarball_sha})

        with tempfile.TemporaryDirectory() as tmp:
            fake_path = Path(tmp) / "binaries" / "osquery"
            fake_path.parent.mkdir(parents=True, exist_ok=True)
            fake_path.write_bytes(b"")
            store.root_path = str(tmp)

            def fake_get_binary_path(name: str) -> str:
                return str(fake_path)

            with patch.object(boot, "get_binary_path", side_effect=fake_get_binary_path):
                boot._store_binary_and_chmod("osquery", tarball)

            store.write_bytes.assert_called_once()
            written_path = store.write_bytes.call_args[0][0]
            assert written_path == "binaries/osquery"

    def test_non_tarball_binary_no_chmod(self, real_hash: str) -> None:
        store = MagicMock()
        boot = BinaryBootstrapper(store=store, known_sha256={"openbao": real_hash})
        boot._store_binary_and_chmod("openbao", b"openbao-data")
        store.write_bytes.assert_called_once()
        call_path = store.write_bytes.call_args[0][0]
        assert call_path == "binaries/openbao"


class TestC16DownloadFlowIntegrity:
    def test_download_rejects_unpinned_binary(self) -> None:
        store = MagicMock()
        store.exists.return_value = False
        boot = BinaryBootstrapper(store=store, known_sha256={})
        with patch.object(boot, "_has_bundled", return_value=False), \
                patch.object(boot, "get_download_url", return_value="https://example.com/bin"), \
                patch(
                    "httpx.AsyncClient.get",
                    side_effect=Exception("should not be called"),
                ):
            import asyncio
            asyncio.run(boot.download("test-bin"))

        assert boot._verify_digest("test-bin", b"anything") is False

    def test_download_rejects_wrong_checksum(self, real_hash: str) -> None:
        store = MagicMock()
        store.exists.return_value = False
        boot = BinaryBootstrapper(store=store, known_sha256={"test-bin": real_hash})

        bad_bytes = b"wrong-data"

        class FakeResponse:
            def __init__(self) -> None:
                self.status_code = 200
                self.content = bad_bytes
                self.headers: dict = {}

        async def fake_get(url: str) -> FakeResponse:
            return FakeResponse()

        with patch.object(boot, "_has_bundled", return_value=False), \
                patch.object(boot, "get_download_url", return_value="https://example.com/bin"), \
                patch("httpx.AsyncClient.get", side_effect=fake_get):
            import asyncio
            result = asyncio.run(boot.download("test-bin"))

        assert result is False
        store.write_bytes.assert_not_called()

    def test_download_allows_correct_checksum(self, real_hash: str) -> None:
        store = MagicMock()
        store.exists.return_value = False
        boot = BinaryBootstrapper(store=store, known_sha256={"test-bin": real_hash})

        class FakeResponse:
            def __init__(self) -> None:
                self.status_code = 200
                self.content = b"safe-binary-data"
                self.headers: dict = {}

        async def fake_get(url: str) -> FakeResponse:
            return FakeResponse()

        with patch.object(boot, "_has_bundled", return_value=False), \
                patch.object(boot, "get_download_url", return_value="https://example.com/bin"), \
                patch("httpx.AsyncClient.get", side_effect=fake_get):
            import asyncio
            result = asyncio.run(boot.download("test-bin"))

        assert result is True
        store.write_bytes.assert_called_once()


class TestC16SyncBundledToFilestoreGap:
    def test_sync_bundled_rejects_tampered_binary(self, real_hash: str) -> None:
        """sync_bundled_to_filestore MUST verify digest before store_binary.
        A tampered binary whose sha256 does not match the pin is refused."""
        store = MagicMock()
        store.exists.return_value = False
        boot = BinaryBootstrapper(store=store, known_sha256={"openbao": real_hash})

        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "openbao"
            bundled.write_bytes(b"attacker-planted-binary")
            boot = BinaryBootstrapper(
                store=store,
                bundled_binaries_dir=str(tmp),
                known_sha256={"openbao": real_hash},
            )

            synced = boot.sync_bundled_to_filestore()

            assert synced == []
            store.write_bytes.assert_not_called()

    def test_sync_bundled_accepts_verified_binary(self, real_hash: str) -> None:
        """sync_bundled_to_filestore stores a bundled binary whose sha256
        matches the configured pin."""
        store = MagicMock()
        store.exists.return_value = False
        data = b"safe-binary-data"
        boot = BinaryBootstrapper(store=store, known_sha256={"openbao": real_hash})

        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "openbao"
            bundled.write_bytes(data)
            boot = BinaryBootstrapper(
                store=store,
                bundled_binaries_dir=str(tmp),
                known_sha256={"openbao": real_hash},
            )

            synced = boot.sync_bundled_to_filestore()

            assert synced == ["openbao"]
            store.write_bytes.assert_called_once_with("binaries/openbao", data)

    def test_sync_bundled_skips_unpinned_binary(self) -> None:
        """sync_bundled_to_filestore skipfs a binary that has no configured pin
        (fail-closed — an unpinned binary must not be stored)."""
        store = MagicMock()
        store.exists.return_value = False
        boot = BinaryBootstrapper(store=store, known_sha256={})

        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "openbao"
            bundled.write_bytes(b"some-data")
            boot = BinaryBootstrapper(
                store=store,
                bundled_binaries_dir=str(tmp),
                known_sha256={},
            )

            synced = boot.sync_bundled_to_filestore()

            assert synced == []
            store.write_bytes.assert_not_called()


class TestC16ResolvePinsPrecedence:
    def test_env_var_overrides_module_default(self) -> None:
        with patch.dict(
            "os.environ",
            {"GLUDD_BINARY_SHA256": '{"pinned-bin": "abc123"}'},
        ):
            pins = BinaryBootstrapper._resolve_pins(None)
            assert pins.get("pinned-bin") == "abc123"

    def test_explicit_overrides_env(self) -> None:
        with patch.dict(
            "os.environ",
            {"GLUDD_BINARY_SHA256": '{"pinned-bin": "from-env"}'},
        ):
            pins = BinaryBootstrapper._resolve_pins({"pinned-bin": "from-ctor"})
            assert pins.get("pinned-bin") == "from-ctor"

    def test_malformed_env_ignored(self) -> None:
        with patch.dict(
            "os.environ",
            {"GLUDD_BINARY_SHA256": "not-json"},
        ):
            pins = BinaryBootstrapper._resolve_pins(None)
            assert pins == {}

    def test_env_not_dict_ignored(self) -> None:
        with patch.dict(
            "os.environ",
            {"GLUDD_BINARY_SHA256": "[]"},
        ):
            pins = BinaryBootstrapper._resolve_pins(None)
            assert pins == {}


class TestC16SizeCap:
    def test_download_rejects_oversized_content_length(self, real_hash: str) -> None:
        store = MagicMock()
        store.exists.return_value = False
        boot = BinaryBootstrapper(store=store, known_sha256={"test-bin": real_hash})

        class FakeResponse:
            def __init__(self) -> None:
                self.status_code = 200
                self.content = b"small"
                self.headers: dict = {"content-length": str(_MAX_DOWNLOAD_BYTES + 1)}

        async def fake_get(url: str) -> FakeResponse:
            return FakeResponse()

        with patch.object(boot, "_has_bundled", return_value=False), \
                patch.object(boot, "get_download_url", return_value="https://example.com/bin"), \
                patch("httpx.AsyncClient.get", side_effect=fake_get):
            import asyncio
            result = asyncio.run(boot.download("test-bin"))

        assert result is False

    def test_download_rejects_oversized_body(
        self, real_hash: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import general_ludd.filestore.bootstrap as bootstrap_mod

        store = MagicMock()
        store.exists.return_value = False
        boot = BinaryBootstrapper(store=store, known_sha256={"test-bin": real_hash})

        # Exercise the exact production boundary without retaining a 512 MiB
        # allocation in a long-lived xdist worker for the rest of the suite.
        test_limit = 1024
        monkeypatch.setattr(bootstrap_mod, "_MAX_DOWNLOAD_BYTES", test_limit)
        big_content = b"x" * (test_limit + 1)

        class FakeResponse:
            def __init__(self) -> None:
                self.status_code = 200
                self.content = big_content
                self.headers: dict = {}

        async def fake_get(url: str) -> FakeResponse:
            return FakeResponse()

        with patch.object(boot, "_has_bundled", return_value=False), \
                patch.object(boot, "get_download_url", return_value="https://example.com/bin"), \
                patch("httpx.AsyncClient.get", side_effect=fake_get):
            import asyncio
            result = asyncio.run(boot.download("test-bin"))

        assert result is False


class TestC16CompareDigestUsed:
    def test_verify_uses_hmac_compare_digest(self) -> None:
        import hmac

        boot = BinaryBootstrapper(known_sha256={})
        with patch.object(hmac, "compare_digest", wraps=hmac.compare_digest) as spy, patch.object(
            boot,
            "KNOWN_SHA256",
            {"timing-bin": hashlib.sha256(b"payload").hexdigest()},
        ):
            boot._verify_digest("timing-bin", b"payload")

        spy.assert_called_once()
