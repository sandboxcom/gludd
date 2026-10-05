"""Tests for ``general_ludd.routers.__init__.py`` — register_all router registration."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.routers import register_all

ROUTER_NAMES: list[str] = [
    "account",
    "adversarial",
    "ansible",
    "benchmark",
    "chat",
    "compute",
    "coordination",
    "estimation",
    "eval",
    "filestore",
    "game",
    "generate",
    "human_todos",
    "integrity",
    "mcp",
    "memory",
    "model_performance",
    "models",
    "ornith",
    "projects",
    "quantization",
    "reload",
    "remediation",
    "render",
    "security",
    "self_improve",
    "signing",
    "skills",
    "slurm",
    "spec_quality",
    "stream",
    "terraform_state",
    "todos",
    "variants",
    "web_search",
    "worktree",
]


class TestRegisterAll:
    """Unit tests for ``register_all(app, daemon_state)``."""

    ROUTER_NAMES: list[str] = ROUTER_NAMES

    @staticmethod
    def _mock_all_routers() -> dict[str, MagicMock]:
        mocks: dict[str, MagicMock] = {}
        for name in TestRegisterAll.ROUTER_NAMES:
            module_mock = MagicMock()
            module_mock.register = MagicMock()
            mocks[f"general_ludd.routers.{name}"] = module_mock
        return mocks

    def test_register_all_calls_every_register_once(self) -> None:
        mocks = self._mock_all_routers()
        app = MagicMock()
        daemon_state: dict[str, object] = {}
        with patch.dict(sys.modules, mocks, clear=False):
            register_all(app, daemon_state)
        for name in self.ROUTER_NAMES:
            mocks[f"general_ludd.routers.{name}"].register.assert_called_once()

    def test_register_all_passes_app_to_every_register(self) -> None:
        mocks = self._mock_all_routers()
        app = MagicMock()
        daemon_state: dict[str, object] = {}
        with patch.dict(sys.modules, mocks, clear=False):
            register_all(app, daemon_state)
        for name in self.ROUTER_NAMES:
            args, _kwargs = mocks[f"general_ludd.routers.{name}"].register.call_args
            assert args[0] is app

    def test_register_all_passes_daemon_state_to_every_register(self) -> None:
        mocks = self._mock_all_routers()
        app = MagicMock()
        daemon_state: dict[str, object] = {}
        with patch.dict(sys.modules, mocks, clear=False):
            register_all(app, daemon_state)
        for name in self.ROUTER_NAMES:
            args, _kwargs = mocks[f"general_ludd.routers.{name}"].register.call_args
            assert args[1] is daemon_state

    def test_register_all_returns_none(self) -> None:
        mocks = self._mock_all_routers()
        app = MagicMock()
        daemon_state: dict[str, object] = {}
        with patch.dict(sys.modules, mocks, clear=False):
            register_all(app, daemon_state)

    def test_register_all_empty_daemon_state(self) -> None:
        mocks = self._mock_all_routers()
        app = MagicMock()
        daemon_state: dict[str, object] = {}
        with patch.dict(sys.modules, mocks, clear=False):
            register_all(app, daemon_state)
        for name in self.ROUTER_NAMES:
            mocks[f"general_ludd.routers.{name}"].register.assert_called_once_with(app, daemon_state)

    def test_register_all_import_error_propagates(self) -> None:
        mocks = self._mock_all_routers()
        broken = types.ModuleType("general_ludd.routers.account")

        def _raise_on_register(attr: str) -> object:
            if attr == "register":
                raise ImportError("simulated import failure: account deps unavailable")
            raise AttributeError(attr)

        broken.__getattr__ = _raise_on_register  # type: ignore[attr-defined]
        mocks["general_ludd.routers.account"] = broken  # type: ignore[assignment]
        app = MagicMock()
        daemon_state: dict[str, object] = {}
        with (
            patch.dict(sys.modules, mocks, clear=False),
            pytest.raises(ImportError, match="account deps unavailable"),
        ):
            register_all(app, daemon_state)

    def test_register_all_register_exception_propagates(self) -> None:
        mocks = self._mock_all_routers()
        mocks["general_ludd.routers.account"].register.side_effect = RuntimeError("register burst")
        app = MagicMock()
        daemon_state: dict[str, object] = {}
        with patch.dict(sys.modules, mocks, clear=False), pytest.raises(RuntimeError, match="register burst"):
            register_all(app, daemon_state)

    def test_register_all_type_checking_not_at_runtime(self) -> None:
        import general_ludd.routers
        assert "FastAPI" not in vars(general_ludd.routers)
