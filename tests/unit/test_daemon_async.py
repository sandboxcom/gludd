"""Focused async-correctness tests for daemon H1/H2/H3 fixes.

H1 — build_secrets_resolver is offloaded to a thread (asyncio.to_thread) in
     _lifespan so a slow OpenBao never blocks the event loop.
H2 — _maybe_open_pr is a coroutine function so push_and_create_pr's shell-out
     never blocks the event loop (runs via asyncio.to_thread inside the method).
H3 — EventLoop._spend_limiter is set VIA the constructor (spend_limiter= arg),
     guaranteeing it is non-None before asyncio.create_task schedules the first
     tick — the spend cap cannot be bypassed on startup.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop

# ---------------------------------------------------------------------------
# H2: _maybe_open_pr must be a coroutine so it can offload blocking work
# ---------------------------------------------------------------------------

def test_h2_maybe_open_pr_is_coroutine_function() -> None:
    """_maybe_open_pr must be async so the event loop is never stalled."""
    assert inspect.iscoroutinefunction(EventLoop._maybe_open_pr), (
        "_maybe_open_pr must be 'async def' (H2 fix: push_and_create_pr shells "
        "out to gh/git and must be run via asyncio.to_thread inside an async method)"
    )


@pytest.mark.asyncio
async def test_h2_maybe_open_pr_no_op_when_open_pr_disabled() -> None:
    """When open_pr is False (the default), _maybe_open_pr returns without shelling out."""
    loop = EventLoop(config={"git_automation": {"open_pr": False}})
    todo = MagicMock()
    # Should complete without touching PRDelivery.
    await loop._maybe_open_pr(todo, "/tmp/wt", "feature/x")


@pytest.mark.asyncio
async def test_h2_maybe_open_pr_uses_to_thread_when_enabled() -> None:
    """When open_pr is True, push_and_create_pr is called via asyncio.to_thread."""
    loop = EventLoop(config={
        "git_automation": {
            "open_pr": True,
            "base_branch": "main",
            "pr_draft": False,
            "pr_labels": [],
        }
    })
    todo = MagicMock()
    todo.todo_id = "t-001"
    todo.title = "Test PR"

    fake_delivery = MagicMock()
    fake_delivery.push_and_create_pr.return_value = {"pr_url": "https://github.com/x/y/pull/1"}

    captured_fn: list = []

    async def fake_to_thread(fn: object, **kwargs: object) -> dict:
        captured_fn.append(fn)
        # call synchronously in test context (no real thread needed)
        return fake_delivery.push_and_create_pr(**kwargs)

    with (
        patch("general_ludd.git_automation.pr_delivery.PRDelivery", return_value=fake_delivery),
        patch("asyncio.to_thread", side_effect=fake_to_thread),
    ):
        await loop._maybe_open_pr(todo, "/tmp/wt", "feature/x")

    assert captured_fn, "asyncio.to_thread was not called — push_and_create_pr ran synchronously"
    assert fake_delivery.push_and_create_pr.called


# ---------------------------------------------------------------------------
# H3: EventLoop constructor accepts spend_limiter and stores it immediately
# ---------------------------------------------------------------------------

def test_h3_event_loop_accepts_spend_limiter_kwarg() -> None:
    """EventLoop.__init__ must accept a spend_limiter= keyword argument."""
    params = inspect.signature(EventLoop.__init__).parameters
    assert "spend_limiter" in params, (
        "EventLoop.__init__ is missing the spend_limiter= parameter (H3 fix: "
        "the limiter must be passed at construction time, not assigned after "
        "asyncio.create_task)"
    )


def test_h3_event_loop_stores_spend_limiter_from_constructor() -> None:
    """EventLoop._spend_limiter is set to the constructor's spend_limiter arg."""
    fake_limiter = MagicMock()
    loop = EventLoop(spend_limiter=fake_limiter)
    assert loop._spend_limiter is fake_limiter, (
        "EventLoop._spend_limiter was not set from the constructor arg (H3 fix)"
    )


def test_h3_event_loop_spend_limiter_none_by_default() -> None:
    """When spend_limiter is omitted, _spend_limiter is None (not missing)."""
    loop = EventLoop()
    assert loop._spend_limiter is None


def test_h3_spend_limiter_constructed_before_create_task_in_source() -> None:
    """AST-level check: in daemon._lifespan, spend_limiter is assigned BEFORE
    asyncio.create_task(event_loop.run_forever(...)).

    This catches a future regression where someone moves the SpendLimiter block
    back below create_task.
    """
    src = Path("src/general_ludd/daemon.py").read_text()
    tree = ast.parse(src)

    # Walk the _lifespan function body to find line numbers for:
    # 1. The first assignment to 'spend_limiter' (type-annotated or plain)
    # 2. The asyncio.create_task(event_loop.run_forever(...)) call

    spend_limiter_line: int | None = None
    create_task_line: int | None = None

    class _Visitor(ast.NodeVisitor):
        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
            nonlocal spend_limiter_line
            if (
                isinstance(node.target, ast.Name)
                and node.target.id == "spend_limiter"
                and spend_limiter_line is None
            ):
                spend_limiter_line = node.lineno
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            nonlocal create_task_line
            # Match asyncio.create_task(event_loop.run_forever(...)) wherever it
            # appears — bare statement, assignment value (e.g. stored on
            # app.state to keep a strong task ref), or awaited expression. The
            # earlier visit_Expr-only form missed the assigned-task case.
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_task"
                and create_task_line is None
                and node.args
                and isinstance(node.args[0], ast.Call)
                and isinstance(node.args[0].func, ast.Attribute)
                and node.args[0].func.attr == "run_forever"
            ):
                create_task_line = node.lineno
            self.generic_visit(node)

    _Visitor().visit(tree)

    assert spend_limiter_line is not None, "Could not find 'spend_limiter:' assignment in daemon.py"
    assert create_task_line is not None, "Could not find asyncio.create_task(event_loop.run_forever(...)) in daemon.py"
    assert spend_limiter_line < create_task_line, (
        f"H3 regression: spend_limiter is declared at line {spend_limiter_line} "
        f"but asyncio.create_task is at line {create_task_line} — "
        "SpendLimiter must be built BEFORE the event loop task is scheduled."
    )


# ---------------------------------------------------------------------------
# H1: build_secrets_resolver is called via asyncio.to_thread in _lifespan
# ---------------------------------------------------------------------------

def test_h1_lifespan_uses_to_thread_for_build_secrets_resolver() -> None:
    """AST-level check: _lifespan calls asyncio.to_thread(build_secrets_resolver, ...)
    rather than calling build_secrets_resolver(...) directly.

    This catches a future regression where someone removes the to_thread wrapper.
    """
    src = Path("src/general_ludd/daemon.py").read_text()
    tree = ast.parse(src)

    # Collect all Call nodes matching asyncio.to_thread(build_secrets_resolver, ...)
    found_to_thread = False

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            nonlocal found_to_thread
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "to_thread"
                and node.args
            ):
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Name) and first_arg.id == "build_secrets_resolver":
                    found_to_thread = True
            self.generic_visit(node)

    _Visitor().visit(tree)

    assert found_to_thread, (
        "H1 regression: build_secrets_resolver is not called via asyncio.to_thread "
        "in daemon.py — a slow OpenBao will block the event loop on startup."
    )
