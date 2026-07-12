"""Tests for daemon.py is_public_path (S.4: /docs bypass)."""

import pytest

from general_ludd.daemon import is_public_path


class TestIsPublicPath:
    """is_public_path must NOT treat /docs_evil as public (S.4)."""

    @pytest.mark.parametrize("path,method,expected", [
        ("/docs", "GET", True),
        ("/docs", "HEAD", True),
        ("/docs", "OPTIONS", True),
        ("/docs", "POST", False),
        ("/docs", "PUT", False),
        ("/docs", "DELETE", False),
        ("/docs/openapi.json", "GET", True),
        ("/docs/swagger", "GET", True),
        ("/docs_evil", "GET", False),
        ("/docs_evil", "POST", False),
        ("/docs_evil/secrets", "GET", False),
        ("/documentation", "GET", False),
        ("/openapi.json", "GET", True),
        ("/redoc", "GET", True),
        ("/healthz", "GET", True),
        ("/render/report", "GET", True),
        ("/render/report", "POST", False),
        ("/admin/secrets", "GET", False),
        ("/v1/telemetry", "POST", True),
        ("/v1/ingest", "GET", True),
        ("/ingest/logs", "POST", True),
    ])
    def test_is_public_path(self, path, method, expected):
        assert is_public_path(method, path) is expected
