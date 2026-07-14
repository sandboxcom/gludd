"""H-STARTUP-NULL-DEPS: verify EventLoop gets live instances of all three deps.

Before the fix, infra_tracker, deployment_manager, and adaptive_router were
all None at EventLoop construction time:
  - infra_tracker: read from app.state._infra_tracker before it was assigned
  - deployment_manager: read from app.state._deployment_manager, lazily set
    only in routers/compute.py (never during startup)
  - adaptive_router: could be None if _get_or_create_extended_subsystems
    was called before session_factory was available

The fix (CA-T7/8/9/H3 pattern): pre-build all three deps BEFORE the
EventLoop() constructor so the idle-GPU cost-recording + teardown phases
actually run instead of being silently dead code.
"""

import pytest


class TestDaemonStartupNonNullDeps:
    """After daemon startup (lifespan complete), the EventLoop must hold live
    (non-None) instances of infra_tracker, deployment_manager, and
    adaptive_router — the three deps that were None before the H.1 fix.
    """

    @pytest.mark.asyncio
    async def test_event_loop_has_live_infra_tracker(self):
        """EventLoop._infra_tracker must be non-None after lifespan.

        Without this, idle-GPU cost recording (loop.py record_gpu_seconds)
        is permanently dead code — every GPU-second runs free.
        """
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(config_dir=None)
        async with app.router.lifespan_context(app):
            loop = app.state.event_loop
            assert loop is not None, "EventLoop must exist after lifespan"
            assert loop._infra_tracker is not None, (
                "EventLoop._infra_tracker is None — idle-GPU cost recording "
                "in loop.py is dead code"
            )
            assert app.state._infra_tracker is loop._infra_tracker, (
                "app.state._infra_tracker must be the SAME instance the EventLoop "
                "holds, else routers/spend.py and loop disagree on infra state"
            )

    @pytest.mark.asyncio
    async def test_event_loop_has_live_deployment_manager(self):
        """EventLoop._deployment_manager must be non-None after lifespan.

        Without this, idle-GPU auto-teardown (loop.py deployment_manager.destroy)
        is permanently dead code — GPU stacks are torn down from bookkeeping
        but never actually destroyed (confirmed cost leak).
        """
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(config_dir=None)
        async with app.router.lifespan_context(app):
            loop = app.state.event_loop
            assert loop is not None, "EventLoop must exist after lifespan"
            assert loop._deployment_manager is not None, (
                "EventLoop._deployment_manager is None — idle-GPU teardown "
                "in loop.py is dead code"
            )
            assert app.state._deployment_manager is loop._deployment_manager, (
                "app.state._deployment_manager must be the SAME instance the "
                "EventLoop holds, else /admin/compute/destroy and idle-teardown "
                "disagree on deployment state"
            )

    @pytest.mark.asyncio
    async def test_event_loop_has_live_adaptive_router(self):
        """EventLoop._adaptive_router must be non-None after lifespan.

        Without this, adaptive prompt routing (loop.py route()) and routing
        decision capture (loop.py _capture_routing_decision) silently skip —
        every prompt gets the default model with zero adaptation.
        """
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(config_dir=None)
        async with app.router.lifespan_context(app):
            loop = app.state.event_loop
            assert loop is not None, "EventLoop must exist after lifespan"
            assert loop._adaptive_router is not None, (
                "EventLoop._adaptive_router is None — adaptive prompt routing "
                "and routing-decision capture are dead code"
            )
            assert app.state._adaptive_router is loop._adaptive_router, (
                "app.state._adaptive_router must be the SAME instance the "
                "EventLoop holds, else routers that read app.state and the "
                "loop's route() disagree on routing state"
            )

    @pytest.mark.asyncio
    async def test_all_three_deps_non_null_after_startup(self):
        """Regression test: verify H-STARTUP-NULL-DEPS fix is complete.

        All three deps (infra_tracker, deployment_manager, adaptive_router)
        must be non-None on the EventLoop, AND must be the same instances
        as app.state, after daemon startup completes. This guards against
        the construction-order bug class recurring on a 4th dependency.
        """
        from general_ludd.daemon import create_daemon_app

        app = create_daemon_app(config_dir=None)
        async with app.router.lifespan_context(app):
            loop = app.state.event_loop
            assert loop is not None
            assert loop._infra_tracker is not None
            assert loop._deployment_manager is not None
            assert loop._adaptive_router is not None
            assert app.state._infra_tracker is loop._infra_tracker
            assert app.state._deployment_manager is loop._deployment_manager
            assert app.state._adaptive_router is loop._adaptive_router
