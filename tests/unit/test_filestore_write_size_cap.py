"""Regression tests for /admin/filestore/write content-size cap (LOW/MED).

A single write request with unbounded content can fill the backing volume.
The handler must reject payloads whose UTF-8 byte length exceeds
FILESTORE_WRITE_MAX_BYTES with HTTP 413.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

import general_ludd.routers.filestore as filestore_mod
from general_ludd.daemon import create_daemon_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def app():
    """Daemon app with a real filestore router registered."""
    return create_daemon_app()


@pytest.fixture()
def transport(app):
    return ASGITransport(app=app)


def _store_mock():
    """FileStore mock that accepts write_text without touching the filesystem."""
    m = MagicMock()
    m.write_text = MagicMock()
    return m


# ---------------------------------------------------------------------------
# Size-cap tests
# ---------------------------------------------------------------------------

class TestFilestoreWriteSizeCap:
    """Verify that oversized write payloads are rejected before disk I/O."""

    @pytest.mark.asyncio
    async def test_over_cap_content_returns_413(self, transport):
        """A content string whose UTF-8 encoding exceeds the cap must yield 413."""
        # Use a tiny cap so the test is fast.
        small_cap = 100  # bytes

        with (
            patch.object(filestore_mod, "FILESTORE_WRITE_MAX_BYTES", small_cap),
            patch("general_ludd.routers.filestore.FileStore", return_value=_store_mock()),
            patch("general_ludd.routers.filestore.sanitize_path", return_value="ok/path"),
        ):
            payload = {"path": "ok/path", "content": "x" * (small_cap + 1)}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/filestore/write", json=payload)

        assert resp.status_code == 413
        body = resp.json()
        assert body["success"] is False
        assert body["max_bytes"] == small_cap
        assert body["received_bytes"] > small_cap

    @pytest.mark.asyncio
    async def test_over_cap_does_not_call_write_text(self, transport):
        """Disk write must NOT be called when the cap is exceeded."""
        small_cap = 50
        store = _store_mock()

        with (
            patch.object(filestore_mod, "FILESTORE_WRITE_MAX_BYTES", small_cap),
            patch("general_ludd.routers.filestore.FileStore", return_value=store),
            patch("general_ludd.routers.filestore.sanitize_path", return_value="some/path"),
        ):
            payload = {"path": "some/path", "content": "a" * (small_cap + 10)}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                await client.post("/admin/filestore/write", json=payload)

        store.write_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_at_cap_content_is_accepted(self, transport):
        """A payload exactly at the cap boundary must succeed (HTTP 200)."""
        small_cap = 64
        store = _store_mock()

        with (
            patch.object(filestore_mod, "FILESTORE_WRITE_MAX_BYTES", small_cap),
            patch("general_ludd.routers.filestore.FileStore", return_value=store),
            patch("general_ludd.routers.filestore.sanitize_path", return_value="ok/file"),
        ):
            payload = {"path": "ok/file", "content": "b" * small_cap}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/filestore/write", json=payload)

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        store.write_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_well_under_cap_content_is_accepted(self, transport):
        """A small, normal payload must succeed."""
        store = _store_mock()

        with (
            patch("general_ludd.routers.filestore.FileStore", return_value=store),
            patch("general_ludd.routers.filestore.sanitize_path", return_value="notes/todo.txt"),
        ):
            payload = {"path": "notes/todo.txt", "content": "hello world"}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/filestore/write", json=payload)

        assert resp.status_code == 200
        assert resp.json()["success"] is True

    @pytest.mark.asyncio
    async def test_multibyte_utf8_counted_correctly(self, transport):
        """Content with multi-byte Unicode chars must be sized by byte length,
        not character count, so a string of N emoji (4 bytes each) that exceeds
        the cap in bytes is rejected even though its char count is smaller."""
        small_cap = 20  # bytes
        store = _store_mock()

        # Each "😀" is 4 UTF-8 bytes; 6 of them = 24 bytes > 20-byte cap
        emoji_content = "😀" * 6
        assert len(emoji_content.encode("utf-8")) > small_cap

        with (
            patch.object(filestore_mod, "FILESTORE_WRITE_MAX_BYTES", small_cap),
            patch("general_ludd.routers.filestore.FileStore", return_value=store),
            patch("general_ludd.routers.filestore.sanitize_path", return_value="emoji.txt"),
        ):
            payload = {"path": "emoji.txt", "content": emoji_content}
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post("/admin/filestore/write", json=payload)

        assert resp.status_code == 413
        store.write_text.assert_not_called()
