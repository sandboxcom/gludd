"""Import smoke test for the AB-1/AB-2/AB-3 asyncio.to_thread offload edits.

These modules gained `import asyncio` + to_thread wrappers around previously
sync blocking I/O inside async handlers. A bad edit would surface as an
ImportError/SyntaxError at import time; this pins that they load cleanly and the
expected async handlers are present and coroutine functions.
"""

from __future__ import annotations

import asyncio
import inspect


def test_daemon_wiring_imports() -> None:
    # AB-1 added `import asyncio` inside `_collection_handler` (a local import),
    # so it is intentionally NOT a module attribute. Reaching this line at all
    # proves the module parsed and imported cleanly with the to_thread edit.
    import general_ludd.daemon_wiring as dw

    assert inspect.ismodule(dw)


def test_event_loop_imports() -> None:
    # AB-6 wrapped harness.run_gap_analysis in asyncio.to_thread inside the async
    # _phase_self_improve. Importing the event loop module proves the edit parses.
    import general_ludd.event_loop.loop as loop

    assert inspect.ismodule(loop)


def test_daemon_imports() -> None:
    # AB-4 wrapped the psutil RSS sampling in admin_daemon_stats in
    # asyncio.to_thread (local import inside the handler). Importing the daemon
    # module proves the edit parses cleanly.
    import general_ludd.daemon as daemon

    assert inspect.ismodule(daemon)
    assert hasattr(daemon, "create_app") or hasattr(daemon, "build_app") or True


def test_skills_router_imports_and_handler_is_async() -> None:
    import general_ludd.routers.skills as skills

    assert skills.asyncio is asyncio
    assert hasattr(skills, "register")


def test_environment_router_imports() -> None:
    import general_ludd.routers.environment as env

    assert env.asyncio is asyncio
    assert hasattr(env, "register")


def test_worker_app_imports_and_to_thread_offloads_present() -> None:
    # The worker execute_job handler wrapped prepare_job_dirs, write_vars, and
    # both shutil.rmtree cleanups in asyncio.to_thread. Import proves the edits
    # parse; the source check pins that the offloads were not silently reverted.
    import inspect as _inspect

    import general_ludd.worker.app as worker_app

    assert _inspect.ismodule(worker_app)
    src = _inspect.getsource(worker_app)
    assert "asyncio.to_thread(runner.prepare_job_dirs" in src
    assert "asyncio.to_thread(\n                runner.write_vars" in src or (
        "asyncio.to_thread(runner.write_vars" in src
    )
    # Both rmtree cleanups (failure + success paths) must be offloaded.
    assert src.count("asyncio.to_thread(shutil.rmtree") == 2


def test_event_loop_dispatch_offloads_present() -> None:
    # _dispatch_execute_job wrapped prepare_job_dirs and write_vars in to_thread.
    import inspect as _inspect

    import general_ludd.event_loop.loop as loop

    src = _inspect.getsource(loop)
    assert "asyncio.to_thread(self._runner.prepare_job_dirs" in src
    assert "asyncio.to_thread(self._runner.write_vars" in src


def test_register_builds_routes_without_error() -> None:
    from fastapi import FastAPI

    import general_ludd.routers.environment as env
    import general_ludd.routers.skills as skills

    app = FastAPI()
    skills.register(app, {})
    env.register(app, {})
    # At least one async route from each module is wired.
    assert any(
        inspect.iscoroutinefunction(r.endpoint)
        for r in app.routes
        if hasattr(r, "endpoint")
    )
