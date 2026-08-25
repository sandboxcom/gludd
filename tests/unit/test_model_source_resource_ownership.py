"""Resource-ownership contracts for model registry probes and downloads."""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest

from general_ludd.cloud.model_sources import (
    DownloadError,
    _check_url_reachable,
    _download_from_direct_url,
)


def _http_error(url: str) -> tuple[urllib.error.HTTPError, io.BytesIO]:
    """Create one inspectable file-backed HTTP error."""
    body = io.BytesIO(b"missing")
    error = urllib.error.HTTPError(url, 404, "Not Found", hdrs=Message(), fp=body)
    return error, body


def test_direct_url_download_owns_response_and_replaces_atomically(tmp_path: Path) -> None:
    """A successful download closes both resources and leaves only its result."""
    response = io.BytesIO(b"deterministic-gguf")
    with (
        patch("urllib.request.urlopen", return_value=response) as mock_open,
        patch(
            "urllib.request.urlretrieve",
            side_effect=AssertionError("urlretrieve does not expose response ownership"),
        ),
    ):
        result = _download_from_direct_url(
            "https://models.example.test/model.gguf",
            dest_dir=str(tmp_path),
            timeout=7.0,
        )

    assert response.closed
    mock_open.assert_called_once()
    assert result.local_path == str(tmp_path / "model.gguf")
    assert (tmp_path / "model.gguf").read_bytes() == b"deterministic-gguf"
    assert list(tmp_path.glob("*.part")) == []


def test_url_health_probe_owns_response() -> None:
    """A successful health probe closes its response before returning."""
    response = io.BytesIO(b"")
    with patch("urllib.request.urlopen", return_value=response):
        assert _check_url_reachable("https://models.example.test", timeout=1.0)
    assert response.closed


def test_url_health_probe_closes_http_error_response() -> None:
    """A failed health probe closes the file-like exception response."""
    error, body = _http_error("https://models.example.test/missing")
    with patch("urllib.request.urlopen", side_effect=error):
        assert not _check_url_reachable("https://models.example.test/missing", timeout=1.0)
    assert body.closed


def test_direct_url_download_closes_http_error_and_removes_partial(tmp_path: Path) -> None:
    """An HTTP failure closes its body and removes the partial model."""
    error, body = _http_error("https://models.example.test/missing.gguf")
    with (
        patch("urllib.request.urlopen", side_effect=error),
        pytest.raises(urllib.error.HTTPError),
    ):
        _download_from_direct_url(
            "https://models.example.test/missing.gguf",
            dest_dir=str(tmp_path),
            timeout=1.0,
        )
    assert body.closed
    assert list(tmp_path.iterdir()) == []


def test_direct_url_download_removes_partial_on_transport_error(tmp_path: Path) -> None:
    """A non-HTTP transport failure cannot leave a partial model."""
    with (
        patch("urllib.request.urlopen", side_effect=OSError("connection reset")),
        pytest.raises(OSError, match="connection reset"),
    ):
        _download_from_direct_url(
            "https://models.example.test/interrupted.gguf",
            dest_dir=str(tmp_path),
        )
    assert list(tmp_path.iterdir()) == []


def test_direct_url_download_rejects_missing_filename(tmp_path: Path) -> None:
    """A URL without an artifact name fails before acquiring resources."""
    with pytest.raises(DownloadError, match="no filename"):
        _download_from_direct_url("https://models.example.test/", dest_dir=str(tmp_path))
    assert list(tmp_path.iterdir()) == []
