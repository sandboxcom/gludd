"""Tests for routers._util: get_session_factory."""

from __future__ import annotations

from fastapi import FastAPI

from general_ludd.routers._util import get_session_factory


class TestGetSessionFactory:
    def test_returns_none_when_no_session_factory_set(self):
        app = FastAPI()
        result = get_session_factory(app)
        assert result is None

    def test_returns_session_factory_when_set(self):
        from types import SimpleNamespace

        app = FastAPI()
        mock_factory = SimpleNamespace()
        app.state._session_factory = mock_factory
        result = get_session_factory(app)
        assert result is mock_factory

    def test_returns_none_for_missing_state_attribute(self):
        app = FastAPI()
        assert get_session_factory(app) is None

    def test_custom_state_object(self):
        from types import SimpleNamespace

        app = SimpleNamespace()
        app.state = SimpleNamespace()
        app.state._session_factory = "fake_factory"
        result = get_session_factory(app)
        assert result == "fake_factory"
