"""Tests for ``src/general_ludd/sts/rotator.py``.

Covers TokenRotationError, TokenRotator.needs_rotation, and TokenRotator._sanitize.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.sts.rotator import TokenRotationError, TokenRotator


class TestTokenRotationError:
    def test_is_runtime_error(self) -> None:
        exc = TokenRotationError("test message")
        assert isinstance(exc, RuntimeError)

    def test_message_preserved(self) -> None:
        exc = TokenRotationError("no token for agent x")
        assert str(exc) == "no token for agent x"

    def test_can_be_caught_as_runtime_error(self) -> None:
        with pytest.raises(RuntimeError):
            raise TokenRotationError("catch me")


class TestNeedsRotation:
    def _rotator(self, window: int = 600) -> TokenRotator:
        from unittest.mock import MagicMock

        return TokenRotator(
            secrets_manager=MagicMock(),
            token_store=MagicMock(),
            rotation_window_seconds=window,
        )

    def test_none_expiry_returns_false(self) -> None:
        rotator = self._rotator()
        assert rotator.needs_rotation(expires_at=None) is False

    def test_expired_token_returns_true(self) -> None:
        rotator = self._rotator()
        past = datetime.now(UTC) - timedelta(seconds=1)
        assert rotator.needs_rotation(expires_at=past) is True

    def test_within_window_returns_true(self) -> None:
        rotator = self._rotator(window=600)
        soon = datetime.now(UTC) + timedelta(seconds=300)
        assert rotator.needs_rotation(expires_at=soon) is True

    def test_outside_window_returns_false(self) -> None:
        rotator = self._rotator(window=600)
        far = datetime.now(UTC) + timedelta(seconds=3600)
        assert rotator.needs_rotation(expires_at=far) is False

    def test_exactly_at_window_boundary_returns_true(self) -> None:
        rotator = self._rotator(window=600)
        at_edge = datetime.now(UTC) + timedelta(seconds=600)
        assert rotator.needs_rotation(expires_at=at_edge) is True

    def test_custom_now_parameter(self) -> None:
        rotator = self._rotator(window=600)
        fake_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        expires = datetime(2026, 1, 1, 12, 5, 0, tzinfo=UTC)
        assert rotator.needs_rotation(expires_at=expires, now=fake_now) is True

    def test_custom_now_outside_window(self) -> None:
        rotator = self._rotator(window=600)
        fake_now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        expires = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)
        assert rotator.needs_rotation(expires_at=expires, now=fake_now) is False

    def test_zero_window_expiry_equals_now_returns_true(self) -> None:
        rotator = self._rotator(window=0)
        now = datetime.now(UTC)
        assert rotator.needs_rotation(expires_at=now) is True


class TestSanitize:
    def test_returns_class_name(self) -> None:
        exc = ValueError("secret message in args")
        result = TokenRotator._sanitize(exc)
        assert result == "ValueError"

    def test_returns_name_for_custom_exception(self) -> None:
        class CustomBaoError(Exception):
            pass

        result = TokenRotator._sanitize(CustomBaoError("connection refused"))
        assert result == "CustomBaoError"

    def test_does_not_leak_message(self) -> None:
        exc = RuntimeError("some-credential-12345")
        result = TokenRotator._sanitize(exc)
        assert "credential" not in result
        assert result == "RuntimeError"
