"""Integration: create_daemon_app wires the collection loader into /api/dispatch.

Task #39 — the daemon's HTTP dispatch router registered
``collection_handler=None`` with a STALE TODO ("no loader exists"). A complete
loader DOES exist: :func:`general_ludd.daemon_wiring.make_collection_handler`.
This suite proves ``create_daemon_app`` now registers a real, LAZY collection
handler so the ``collection`` kind is no longer fail-closed as *unknown* at the
dispatch router.

The daemon resolves the live ``AnsibleRunnerAdapter`` (``app.state._runner``,
assigned during lifespan startup) LAZILY — mirroring the mcp/role lazy handlers —
so the handler binds to the real adapter once startup runs, and FAILS CLOSED
(raises ``RuntimeError``, which the DynamicDispatcher converts to a
``handler_error``) when no runner is present.

The wired closure is a local inside ``create_daemon_app`` and the HTTP dispatcher
is registered ``role=None`` (deny-by-default for the privileged ``collection``
kind), so the closure cannot be exercised through a plain ``POST /api/dispatch``.
These tests intercept ``dispatch_router.register`` to capture the REAL closure and
drive it directly against a stubbed runner.

Before the fix (``collection_handler=None``): the captured handler is ``None``
(not callable) and ``collection`` is absent from the dispatcher's
``registered_kinds`` — both assertions below go RED.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest

from general_ludd.daemon import create_daemon_app


class _FakeRunnerAdapter:
    """AnsibleRunnerAdapter stand-in: records register/run/unregister.

    Mirrors the slice of :class:`general_ludd.ansible.runner.AnsibleRunnerAdapter`
    that :func:`make_collection_handler` touches — ``private_data_dir`` plus
    ``register_playbook`` / ``run_playbook`` / ``unregister_playbook`` — so the
    handler reaches the genuine transient-playbook path, not a bypass.
    """

    def __init__(self, private_data_dir: str) -> None:
        self.private_data_dir = private_data_dir
        self.registered: list[tuple[str, str]] = []
        self.unregistered: list[str] = []
        self.run_calls: list[tuple[str, dict[str, Any]]] = []

    def register_playbook(self, name: str, path: str) -> None:
        self.registered.append((name, path))

    def unregister_playbook(self, name: str) -> None:
        self.unregistered.append(name)

    def run_playbook(self, playbook_name: str, **kwargs: Any) -> dict[str, Any]:
        self.run_calls.append((playbook_name, dict(kwargs)))
        return {
            "status": "successful",
            "rc": 0,
            "events": [],
            "stats": {},
            "host_results": {},
            "error": None,
        }


def _build_app_capturing_dispatch_kwargs(monkeypatch: Any) -> tuple[Any, dict[str, Any]]:
    """Build the daemon app, intercepting ``dispatch_router.register`` to capture
    the REAL lazy handler closures it wires.

    The dispatcher itself is a local inside ``routers.dispatch.register`` and is
    never stored on ``app.state``, so spying on ``register`` is the only faithful
    way to reach the daemon's own ``_lazy_collection_handler`` closure.
    """
    from general_ludd.routers import dispatch as dispatch_router

    captured: dict[str, Any] = {}
    real_register = dispatch_router.register

    def _spy(app: Any, state: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_register(app, state, **kwargs)

    monkeypatch.setattr(dispatch_router, "register", _spy)
    with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
        app = create_daemon_app(tick_interval=999.0)
    return app, captured


class TestCreateDaemonAppCollectionWiring:
    def test_collection_kind_registered_on_dispatch_router(self) -> None:
        """create_daemon_app registers a non-None collection handler.

        RED before the fix: ``collection_handler=None`` → the DynamicDispatcher
        never stores a ``collection`` handler → the kind is absent from
        ``registered_kinds`` and a collection dispatch fails closed as *unknown*.
        The ``_dispatch_facet`` snapshot is set at register-time (no lifespan
        required).
        """
        with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
            app = create_daemon_app(tick_interval=999.0)
        facet = app.state._dispatch_facet()
        assert "collection" in facet["registered_kinds"]

    def test_lazy_collection_handler_is_wired_not_none(self, monkeypatch: Any) -> None:
        """The daemon passes a callable lazy collection handler (not ``None``).

        RED before the fix: the captured ``collection_handler`` is ``None``.
        """
        _app, captured = _build_app_capturing_dispatch_kwargs(monkeypatch)
        assert callable(captured["collection_handler"])

    @pytest.mark.asyncio
    async def test_lazy_handler_runs_against_stubbed_runner(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        """With ``app.state._runner`` stubbed, the wired handler runs the module.

        Drives the REAL closure: it reads ``app.state._runner`` lazily, builds a
        transient one-task playbook and register→run→unregisters it through the
        adapter, returning the runner's result dict verbatim.
        """
        app, captured = _build_app_capturing_dispatch_kwargs(monkeypatch)
        handler = captured["collection_handler"]

        adapter = _FakeRunnerAdapter(str(tmp_path))
        app.state._runner = adapter

        result = await handler("ansible.builtin.command", {"cmd": "echo wired"})

        assert result["status"] == "successful"
        assert len(adapter.run_calls) == 1
        # Transient playbook registered for the run then cleaned up — one each,
        # same generated name throughout the register→run→unregister lifecycle.
        assert len(adapter.registered) == 1
        assert len(adapter.unregistered) == 1
        reg_name = adapter.registered[0][0]
        run_name = adapter.run_calls[0][0]
        assert reg_name == run_name == adapter.unregistered[0]
        assert reg_name.startswith("_")

    @pytest.mark.asyncio
    async def test_lazy_handler_fails_closed_without_runner(
        self, monkeypatch: Any
    ) -> None:
        """No runner on ``app.state`` ⇒ the handler fails CLOSED (regression).

        With no ``AnsibleRunnerAdapter`` assigned (lifespan never ran),
        ``make_collection_handler(None)`` is ``None`` and the lazy closure raises
        ``RuntimeError`` — the SAME fail-closed shape the mcp/role lazy handlers
        produce (the DynamicDispatcher converts it to a generic ``handler_error``).
        """
        app, captured = _build_app_capturing_dispatch_kwargs(monkeypatch)
        handler = captured["collection_handler"]

        assert getattr(app.state, "_runner", None) is None
        with pytest.raises(RuntimeError):
            await handler("ansible.builtin.command", {"cmd": "x"})

    @pytest.mark.asyncio
    async def test_lazy_handler_rejects_non_fqcn(
        self, monkeypatch: Any, tmp_path: Any
    ) -> None:
        """The FQCN guard still rejects a non-dotted module name.

        A malformed name becomes a YAML task key, so ``make_collection_handler``
        rejects it with ``ValueError`` BEFORE building the playbook — the wiring
        must not weaken that guard.
        """
        app, captured = _build_app_capturing_dispatch_kwargs(monkeypatch)
        handler = captured["collection_handler"]
        app.state._runner = _FakeRunnerAdapter(str(tmp_path))

        with pytest.raises(ValueError):
            await handler("notdotted", {})
