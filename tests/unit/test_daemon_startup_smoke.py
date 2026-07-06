"""TDD: runtime daemon startup smoke test catches wiring crashes before commit.

The _utilization_tracker crash in daemon.py:1616 was shipped because no test
actually instantiates the daemon app and verifies the lifespan completes
without crashing. Existing tests mock app.state attributes, which masks
missing-attribute errors.

This test actually creates the FastAPI app and exercises the lifespan.
"""

import pytest


class TestDaemonStartupDoesNotCrash:
    """The daemon app must survive lifespan startup without crashing.

    This test exists because commit 7a25edf4 shipped daemon.py:1616 with
    `app.state._utilization_tracker` (unsafe direct access) instead of
    `getattr(app.state, "_utilization_tracker", None)` (safe fallback).
    The existing tests mock app.state and never exercise the real lifespan.

    A passing test here means: if you break daemon startup wiring, this
    test WILL catch it before commit.
    """

    @pytest.mark.asyncio
    async def test_daemon_app_creates_without_config_dir(self):
        """FAIL (maybe): creating the app without a config dir must not crash."""
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(config_dir=None)

        # The app must have a lifespan
        assert app.router.lifespan is not None, "App must have an ASGI lifespan"

        # Default state attributes that must exist after construction
        assert hasattr(app.state, "event_loop") or app.state.event_loop is None, (
            "app.state.event_loop must exist (even if None before lifespan runs)"
        )

    @pytest.mark.asyncio
    async def test_daemon_lifespan_context_manager_completes(self):
        """FAIL: lifespan context manager must enter and exit without raising.

        This is the test that would have caught the _utilization_tracker crash.
        The bug: daemon.py:1616 accessed app.state._utilization_tracker directly,
        but the attribute didn't exist yet at that point in the lifespan.
        The lifespan exception handler at daemon.py:1893 caught the AttributeError
        and put the daemon in degraded mode, which the old tests never checked.
        """
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(config_dir=None)

        # We don't need a full ASGI server — just enter and exit the lifespan
        # to verify no unhandled exceptions occur during startup wiring.
        async with app.router.lifespan_context(app):
            # If we get here, the lifespan startup completed without fatal error.
            # Check that it didn't silently enter degraded mode.
            degraded = getattr(app.state, "_degraded", None)
            assert degraded is None, (
                f"Daemon entered degraded mode during startup: {degraded}"
            )

    def test_daemon_app_has_required_state_after_lifespan(self):
        """FAIL: after lifespan runs, critical state attributes must be present."""
        import asyncio

        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(config_dir=None)

        async def _run():
            async with app.router.lifespan_context(app):
                # Critical subsystems that must be initialized
                required_attrs = [
                    "_metrics_collector",
                    "_model_registry",
                    "_skill_registry",
                ]
                missing = [a for a in required_attrs if not hasattr(app.state, a)]
                assert not missing, (
                    f"Required app.state attributes missing after lifespan: {missing}"
                )

        asyncio.run(_run())
