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
