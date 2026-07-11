"""Tests for daemon HibernationController wiring.

Verifies: app.state._hibernation_controller is populated; dispatcher receives hibernation.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import FastAPI

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.hibernation import (
    HibernationController,
    HibernationStore,
)
from general_ludd.agents.registry import AgentRegistry
from general_ludd.controllers.pause_controller import PauseController


class TestDaemonHibernationWiring:
    def test_app_state_hibernation_controller_populated(self, tmp_path: Path):
        app = FastAPI()
        pause_dir = tmp_path / "pause"
        pause_dir.mkdir()
        os.environ["GLUDD_PAUSE_DIR"] = str(pause_dir)
        try:
            pc = PauseController()
            app.state._pause_controller = pc

            from general_ludd.agents.hibernation import _load_hibernate_mac_key

            pause_base = app.state._pause_controller._store.base_dir
            hibernate_mac_key = _load_hibernate_mac_key(str(pause_base))
            app.state._hibernation_controller = HibernationController(
                store=HibernationStore(
                    base_dir=str(pause_base), mac_key=hibernate_mac_key
                ),
            )

            assert app.state._hibernation_controller is not None
            assert isinstance(app.state._hibernation_controller, HibernationController)
            assert isinstance(app.state._hibernation_controller._store._mac_key, bytes)
            assert len(app.state._hibernation_controller._store._mac_key) == 32
        finally:
            os.environ.pop("GLUDD_PAUSE_DIR", None)

    def test_dispatcher_receives_hibernation_param(self):
        registry = AgentRegistry()
        mock_hibernation = MagicMock(spec=HibernationController)

        dispatcher = AgentDispatcher(
            registry=registry,
            hibernation=mock_hibernation,
        )

        assert dispatcher._hibernation is mock_hibernation

    def test_dispatcher_hibernation_none_by_default(self):
        registry = AgentRegistry()
        dispatcher = AgentDispatcher(registry=registry)
        assert dispatcher._hibernation is None

    def test_dispatcher_with_both_pause_and_hibernation(self):
        registry = AgentRegistry()
        mock_pause = MagicMock(spec=PauseController)
        mock_hibernation = MagicMock(spec=HibernationController)

        dispatcher = AgentDispatcher(
            registry=registry,
            pause_controller=mock_pause,
            hibernation=mock_hibernation,
        )

        assert dispatcher._pause_controller is mock_pause
        assert dispatcher._hibernation is mock_hibernation
