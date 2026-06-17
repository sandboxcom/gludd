"""Supply-chain hardening tests for BinaryBootstrapper.

Covers:
  1. SHA-256 checksum verification (mismatch -> False / ChecksumMismatchError)
  2. Size-cap enforcement (stream aborted if > 200 MiB)
  3. Extraction traversal guard (_safe_extract rejects '../' and absolute members)
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.filestore.bootstrap import (
    BinaryBootstrapper,
    DownloadSizeExceededError,
    _safe_extract,
)
from general_ludd.filestore.store import FileStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tar_gz(members: list[tuple[str, bytes]]) -> bytes:
    """Build an in-memory .tar.gz with the given (name, data) members."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in members:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _make_zip(members: list[tuple[str, bytes]]) -> bytes:
    """Build an in-memory .zip with the given (name, data) members."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def _sha256sums_text(filename: str, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    return f"{digest}  {filename}\n"


# ---------------------------------------------------------------------------
# 1. Checksum verification
# ---------------------------------------------------------------------------


class TestChecksumVerification:
    """SHA-256 mismatch must cause download() to return False (not store the binary)."""

    def _make_boot(self, tmp: str) -> BinaryBootstrapper:
        store = FileStore(root_path=tmp)
        return BinaryBootstrapper(store=store)

    def _mock_client(self, archive_content: bytes, checksum_text: str) -> MagicMock:
        """Return a mock AsyncClient context manager whose stream returns archive_content."""
        # Build a mock that satisfies: async with client.stream("GET", url) as resp: ...
        stream_resp = MagicMock()
        stream_resp.raise_for_status = MagicMock()

        # aiter_bytes must be an async generator
        async def _aiter_bytes(chunk_size: int = 65536):
            yield archive_content

        stream_resp.aiter_bytes = _aiter_bytes
        stream_resp.__aenter__ = AsyncMock(return_value=stream_resp)
        stream_resp.__aexit__ = AsyncMock(return_value=False)

        cs_resp = MagicMock()
        cs_resp.status_code = 200
        cs_resp.text = checksum_text

        client = MagicMock()
        client.stream = MagicMock(return_value=stream_resp)
        client.get = AsyncMock(return_value=cs_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        return client

    def test_checksum_match_stores_binary(self):
        """Correct checksum: binary is stored and download returns True."""
        content = b"fake-bao-archive"
        sums = _sha256sums_text("bao_2.2.0_linux_amd64.tar.gz", content)

        with tempfile.TemporaryDirectory() as tmp:
            boot = self._make_boot(tmp)
            client = self._mock_client(content, sums)

            with patch("httpx.AsyncClient", return_value=client), patch.object(
                boot, "get_bundled_binary_path", return_value=None
            ), patch.object(
                boot,
                "get_platform_info",
                return_value={"os": "linux", "arch": "amd64"},
            ):
                result = asyncio.run(boot.download("openbao"))

            assert result is True
            assert FileStore(root_path=tmp).exists("binaries/openbao")

    def test_checksum_mismatch_returns_false(self):
        """Wrong checksum: download() returns False and does NOT store the binary."""
        content = b"fake-bao-archive"
        # Tampered content has a different hash from what the sums file claims
        wrong_sums = f"{'a' * 64}  bao_2.2.0_linux_amd64.tar.gz\n"

        with tempfile.TemporaryDirectory() as tmp:
            boot = self._make_boot(tmp)
            client = self._mock_client(content, wrong_sums)

            with patch("httpx.AsyncClient", return_value=client), patch.object(
                boot, "get_bundled_binary_path", return_value=None
            ), patch.object(
                boot,
                "get_platform_info",
                return_value={"os": "linux", "arch": "amd64"},
            ):
                result = asyncio.run(boot.download("openbao"))

            assert result is False
            assert not FileStore(root_path=tmp).exists("binaries/openbao")

    def test_checksum_mismatch_logs_error(self, caplog):
        """Mismatch is logged at ERROR level."""
        import logging

        content = b"different-content"
        wrong_sums = f"{'b' * 64}  bao_2.2.0_linux_amd64.tar.gz\n"

        with tempfile.TemporaryDirectory() as tmp:
            boot = self._make_boot(tmp)
            client = self._mock_client(content, wrong_sums)

            with patch("httpx.AsyncClient", return_value=client), patch.object(
                boot, "get_bundled_binary_path", return_value=None
            ), patch.object(
                boot,
                "get_platform_info",
                return_value={"os": "linux", "arch": "amd64"},
            ), caplog.at_level(logging.ERROR, logger="general_ludd.filestore.bootstrap"):
                asyncio.run(boot.download("openbao"))

            assert any("SHA-256 mismatch" in r.message for r in caplog.records)

    def test_parse_sha256sums_finds_entry(self):
        text = (
            "aabbcc  other_file.tar.gz\n"
            "ddeeff  bao_2.2.0_linux_amd64.tar.gz\n"
        )
        boot = BinaryBootstrapper.__new__(BinaryBootstrapper)
        result = boot._parse_sha256sums(text, "bao_2.2.0_linux_amd64.tar.gz")
        assert result == "ddeeff"

    def test_parse_sha256sums_missing_returns_none(self):
        text = "aabbcc  something_else.tar.gz\n"
        boot = BinaryBootstrapper.__new__(BinaryBootstrapper)
        result = boot._parse_sha256sums(text, "bao_2.2.0_linux_amd64.tar.gz")
        assert result is None

    def test_parse_sha256sums_strips_star_prefix(self):
        text = "deadbeef *bao_2.2.0_linux_amd64.tar.gz\n"
        boot = BinaryBootstrapper.__new__(BinaryBootstrapper)
        result = boot._parse_sha256sums(text, "bao_2.2.0_linux_amd64.tar.gz")
        assert result == "deadbeef"


# ---------------------------------------------------------------------------
# 2. Size cap
# ---------------------------------------------------------------------------


class TestSizeCap:
    """_stream_download must abort when response body exceeds 200 MiB."""

    def _make_oversized_client(self, cap: int) -> MagicMock:
        """Mock client that streams cap+1 bytes in one chunk."""
        oversized_chunk = b"x" * (cap + 1)

        stream_resp = MagicMock()
        stream_resp.raise_for_status = MagicMock()

        async def _aiter_bytes(chunk_size: int = 65536):
            yield oversized_chunk

        stream_resp.aiter_bytes = _aiter_bytes
        stream_resp.__aenter__ = AsyncMock(return_value=stream_resp)
        stream_resp.__aexit__ = AsyncMock(return_value=False)

        client = MagicMock()
        client.stream = MagicMock(return_value=stream_resp)
        return client

    def test_stream_raises_when_over_cap(self):
        from general_ludd.filestore import bootstrap as bs_mod

        cap = 1024  # use a tiny cap for speed
        boot = BinaryBootstrapper.__new__(BinaryBootstrapper)
        client = self._make_oversized_client(cap)

        original_cap = bs_mod._MAX_DOWNLOAD_BYTES
        try:
            bs_mod._MAX_DOWNLOAD_BYTES = cap
            with pytest.raises(DownloadSizeExceededError):
                asyncio.run(boot._stream_download(client, "http://example.com/fake.tar.gz"))
        finally:
            bs_mod._MAX_DOWNLOAD_BYTES = original_cap

    def test_download_returns_false_when_over_cap(self):
        """download() returns False (not raises) when the size cap is exceeded."""
        from general_ludd.filestore import bootstrap as bs_mod

        cap = 512
        original_cap = bs_mod._MAX_DOWNLOAD_BYTES

        oversized_chunk = b"y" * (cap + 1)

        stream_resp = MagicMock()
        stream_resp.raise_for_status = MagicMock()

        async def _aiter_bytes(chunk_size: int = 65536):
            yield oversized_chunk

        stream_resp.aiter_bytes = _aiter_bytes
        stream_resp.__aenter__ = AsyncMock(return_value=stream_resp)
        stream_resp.__aexit__ = AsyncMock(return_value=False)

        client = MagicMock()
        client.stream = MagicMock(return_value=stream_resp)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        with tempfile.TemporaryDirectory() as tmp:
            store = FileStore(root_path=tmp)
            boot = BinaryBootstrapper(store=store)

            try:
                bs_mod._MAX_DOWNLOAD_BYTES = cap
                with patch("httpx.AsyncClient", return_value=client), patch.object(
                    boot, "get_bundled_binary_path", return_value=None
                ), patch.object(
                    boot,
                    "get_platform_info",
                    return_value={"os": "linux", "arch": "amd64"},
                ):
                    result = asyncio.run(boot.download("openbao"))
            finally:
                bs_mod._MAX_DOWNLOAD_BYTES = original_cap

        assert result is False
        assert not store.exists("binaries/openbao")

    def test_within_cap_succeeds(self):
        """A download that fits within the cap is accepted."""
        from general_ludd.filestore import bootstrap as bs_mod

        cap = 1024
        content = b"z" * cap  # exactly at cap, not over

        stream_resp = MagicMock()
        stream_resp.raise_for_status = MagicMock()

        async def _aiter_bytes(chunk_size: int = 65536):
            yield content

        stream_resp.aiter_bytes = _aiter_bytes
        stream_resp.__aenter__ = AsyncMock(return_value=stream_resp)
        stream_resp.__aexit__ = AsyncMock(return_value=False)

        client = MagicMock()
        client.stream = MagicMock(return_value=stream_resp)

        original_cap = bs_mod._MAX_DOWNLOAD_BYTES
        try:
            bs_mod._MAX_DOWNLOAD_BYTES = cap
            boot = BinaryBootstrapper.__new__(BinaryBootstrapper)
            result = asyncio.run(
                boot._stream_download(client, "http://example.com/fake.tar.gz")
            )
        finally:
            bs_mod._MAX_DOWNLOAD_BYTES = original_cap

        assert result == content


# ---------------------------------------------------------------------------
# 3. Extraction traversal guard
# ---------------------------------------------------------------------------


class TestExtractionGuard:
    """_safe_extract must reject members with absolute paths or '..' components."""

    # ---- tar.gz ----

    def test_tar_gz_safe_member_ok(self):
        archive = _make_tar_gz([("bao/bao", b"binary-data")])
        with tempfile.TemporaryDirectory() as dest:
            _safe_extract(archive, Path(dest), "bao_2.2.0_linux_amd64.tar.gz")
            assert (Path(dest) / "bao" / "bao").exists()

    def test_tar_gz_dotdot_member_rejected(self):
        archive = _make_tar_gz([("../evil", b"evil")])
        with tempfile.TemporaryDirectory() as dest, pytest.raises(
            (ValueError, tarfile.ExtractError, Exception)
        ):
            _safe_extract(archive, Path(dest), "archive.tar.gz")

    def test_tar_gz_absolute_member_rejected(self):
        # tarfile won't normally create a TarInfo with absolute name; we patch it.
        safe_archive = _make_tar_gz([("safe", b"data")])
        buf = io.BytesIO(safe_archive)
        with tarfile.open(fileobj=buf, mode="r:gz") as tf:
            members = tf.getmembers()

        members[0].name = "/etc/passwd"

        # Rebuild a tar with the tampered member
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode="w:gz") as tf2:
            data = b"evil"
            members[0].size = len(data)
            tf2.addfile(members[0], io.BytesIO(data))
        tampered = out.getvalue()

        with tempfile.TemporaryDirectory() as dest:
            if sys.version_info >= (3, 12):
                # On 3.12+, tarfile filter='data' sanitises absolute paths by
                # stripping the leading '/'.  It does NOT raise, but it ALSO
                # does not write outside dest — that is the security guarantee.
                _safe_extract(tampered, Path(dest), "archive.tar.gz")
                extracted = list(Path(dest).rglob("*"))
                for p in extracted:
                    # Resolve both to catch symlinks; the extracted file must be
                    # a descendant of dest.
                    assert str(p.resolve()).startswith(
                        str(Path(dest).resolve())
                    ), f"Extracted path escaped dest: {p}"
            else:
                with pytest.raises((ValueError, tarfile.ExtractError, Exception)):
                    _safe_extract(tampered, Path(dest), "archive.tar.gz")

    # ---- zip ----

    def test_zip_safe_member_ok(self):
        archive = _make_zip([("tofu/tofu", b"binary-data")])
        with tempfile.TemporaryDirectory() as dest:
            _safe_extract(archive, Path(dest), "tofu_1.9.0_darwin_amd64.zip")
            assert (Path(dest) / "tofu" / "tofu").exists()

    def test_zip_dotdot_member_rejected(self):
        archive = _make_zip([("../evil.sh", b"evil")])
        with tempfile.TemporaryDirectory() as dest, pytest.raises(ValueError, match="unsafe zip member"):
            _safe_extract(archive, Path(dest), "archive.zip")

    def test_zip_absolute_member_rejected(self):
        archive = _make_zip([(("/etc/cron.d/evil" if sys.platform != "win32" else "C:\\Windows\\evil"), b"evil")])
        with tempfile.TemporaryDirectory() as dest, pytest.raises(ValueError, match="unsafe zip member"):
            _safe_extract(archive, Path(dest), "archive.zip")

    def test_unsupported_format_raises(self):
        with tempfile.TemporaryDirectory() as dest, pytest.raises(
            ValueError, match="Unsupported archive format"
        ):
            _safe_extract(b"garbage", Path(dest), "archive.rar")

    # ---- deep traversal paths ----

    def test_tar_gz_deep_dotdot_rejected(self):
        """Members like 'foo/../../etc/passwd' must be caught."""
        archive = _make_tar_gz([("foo/../../etc/passwd", b"evil")])
        with tempfile.TemporaryDirectory() as dest, pytest.raises(
            (ValueError, tarfile.ExtractError, Exception)
        ):
            _safe_extract(archive, Path(dest), "archive.tar.gz")

    def test_zip_deep_dotdot_rejected(self):
        archive = _make_zip([("a/b/../../etc/passwd", b"evil")])
        with tempfile.TemporaryDirectory() as dest, pytest.raises(ValueError, match="unsafe zip member"):
            _safe_extract(archive, Path(dest), "archive.zip")
