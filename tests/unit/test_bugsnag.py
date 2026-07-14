"""Structural tests for connectors/bugsnag.py — BugsnagSource."""

from __future__ import annotations

import contextlib

from general_ludd.connectors.bugsnag import BugsnagSource, ConnectorConfigError, _assert_public_base_url


class TestBugsnagModule:
    def test_bugsnag_source_importable(self) -> None:
        assert BugsnagSource is not None

    def test_connector_config_error_importable(self) -> None:
        assert ConnectorConfigError is not None
        assert issubclass(ConnectorConfigError, Exception)

    def test_assert_public_base_url_rejects_loopback(self) -> None:
        with contextlib.suppress(ConnectorConfigError):
            _assert_public_base_url("http://127.0.0.1/api")

    def test_assert_public_base_url_rejects_private(self) -> None:
        with contextlib.suppress(ConnectorConfigError):
            _assert_public_base_url("http://10.0.0.1/api")

    def test_assert_public_base_url_accepts_public(self) -> None:
        _assert_public_base_url("https://api.bugsnag.com")

    def test_kind_is_logs(self) -> None:
        assert BugsnagSource.KIND == "logs"
