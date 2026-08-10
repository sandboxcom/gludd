"""Deep edge-case tests for db/tenant.py — contextvars lifecycle, nesting, concurrency, abuse."""

from __future__ import annotations

import asyncio
from contextvars import Token, copy_context

import pytest

from general_ludd.db.tenant import get_tenant, reset_tenant, set_tenant

# ---------------------------------------------------------------------------
# Out-of-order reset / token misuse
# ---------------------------------------------------------------------------


class TestOutOfOrderReset:
    def test_reset_outer_while_inner_active_reverts_to_outer_value(self):
        """Resetting the outer token while an inner token is still active
        restores the value that was current when outer was set — the
        inner value is lost because the context is a stack."""
        base = set_tenant("base")
        assert get_tenant() == "base"
        mid = set_tenant("mid")
        assert get_tenant() == "mid"
        set_tenant("inner")
        assert get_tenant() == "inner"
        reset_tenant(mid)
        assert get_tenant() == "base"
        reset_tenant(base)
        assert get_tenant() is None

    def test_resetting_same_token_twice_raises_runtime_error(self):
        """A token consumed by reset_tenant() cannot be reused."""
        tok = set_tenant("only")
        reset_tenant(tok)
        with pytest.raises(RuntimeError):
            reset_tenant(tok)
        assert get_tenant() is None

    def test_token_from_different_context_raises_value_error(self):
        """A token created in a contextvar copy_context cannot be
        consumed in the main context."""
        ctx = copy_context()
        tok: Token[str | None] = ctx.run(set_tenant, "isolated")
        with pytest.raises(ValueError):
            reset_tenant(tok)

    def test_reset_out_of_order_does_not_raise(self):
        """Non-LIFO reset does not raise — it just restores to whatever
        value was current when the token was minted."""
        a = set_tenant("A")
        set_tenant("B")
        set_tenant("C")
        reset_tenant(a)  # skips B and C — restores to None (default)
        assert get_tenant() is None


# ---------------------------------------------------------------------------
# Empty / boundary values
# ---------------------------------------------------------------------------


class TestBoundaryValues:
    def test_set_empty_string(self):
        tok = set_tenant("")
        try:
            assert get_tenant() == ""
        finally:
            reset_tenant(tok)
        assert get_tenant() is None

    def test_set_very_long_project_id(self):
        long_id = "x" * 4096
        tok = set_tenant(long_id)
        try:
            assert get_tenant() == long_id
        finally:
            reset_tenant(tok)

    def test_set_unicode_characters(self):
        pid = "prøjéct-αβγ-日本語-🚀"
        tok = set_tenant(pid)
        try:
            assert get_tenant() == pid
        finally:
            reset_tenant(tok)

    def test_set_none_clears(self):
        tok = set_tenant("exists")
        assert get_tenant() == "exists"
        none_tok = set_tenant(None)
        assert get_tenant() is None
        reset_tenant(none_tok)
        assert get_tenant() == "exists"
        reset_tenant(tok)

    def test_set_sql_metacharacters(self):
        pid = "'; DROP TABLE projects; --"
        tok = set_tenant(pid)
        try:
            assert get_tenant() == pid
        finally:
            reset_tenant(tok)

    def test_set_whitespace_only(self):
        pid = "   \t\n  "
        tok = set_tenant(pid)
        try:
            assert get_tenant() == pid
        finally:
            reset_tenant(tok)

    def test_set_null_byte(self):
        pid = "proj\x00ect"
        tok = set_tenant(pid)
        try:
            assert get_tenant() == pid
        finally:
            reset_tenant(tok)


# ---------------------------------------------------------------------------
# Deep nesting
# ---------------------------------------------------------------------------


class TestDeepNesting:
    def test_deep_100_nesting(self):
        """100 nested set/reset operations — all reset correctly."""
        tokens: list[Token[str | None]] = []
        for i in range(100):
            tokens.append(set_tenant(f"level-{i}"))
            assert get_tenant() == f"level-{i}"
        for i in reversed(range(100)):
            reset_tenant(tokens[i])
            if i > 0:
                assert get_tenant() == f"level-{i - 1}"
        assert get_tenant() is None

    def test_nesting_with_same_value(self):
        """Setting the same value multiple times should produce
        distinct tokens and proper stack unwinding."""
        t1 = set_tenant("same")
        t2 = set_tenant("same")
        t3 = set_tenant("same")
        assert get_tenant() == "same"
        assert t1 is not t2
        assert t2 is not t3
        reset_tenant(t3)
        assert get_tenant() == "same"
        reset_tenant(t2)
        assert get_tenant() == "same"
        reset_tenant(t1)
        assert get_tenant() is None

    def test_interleaved_none_and_value(self):
        """Repeatedly set None and a value to stress the stack."""
        t1 = set_tenant("a")
        assert get_tenant() == "a"
        t2 = set_tenant(None)
        assert get_tenant() is None
        t3 = set_tenant("b")
        assert get_tenant() == "b"
        t4 = set_tenant(None)
        assert get_tenant() is None
        reset_tenant(t4)
        assert get_tenant() == "b"
        reset_tenant(t3)
        assert get_tenant() is None
        reset_tenant(t2)
        assert get_tenant() == "a"
        reset_tenant(t1)
        assert get_tenant() is None


# ---------------------------------------------------------------------------
# Asyncio concurrency
# ---------------------------------------------------------------------------


class TestAsyncioConcurrency:
    @pytest.mark.asyncio
    async def test_concurrent_tasks_have_isolated_tenants(self):
        """Two asyncio tasks running concurrently see their own tenant,
        not each other's."""

        async def task_a(results: list[str | None]):
            tok = set_tenant("task-a")
            await asyncio.sleep(0.01)
            results.append(get_tenant())
            reset_tenant(tok)

        async def task_b(results: list[str | None]):
            tok = set_tenant("task-b")
            await asyncio.sleep(0.005)
            results.append(get_tenant())
            reset_tenant(tok)

        results: list[str | None] = []
        await asyncio.gather(task_a(results), task_b(results))
        assert "task-a" in results
        assert "task-b" in results
        assert get_tenant() is None

    @pytest.mark.asyncio
    async def test_nested_coroutine_inherits_parent_tenant(self):
        """A coroutine awaited from within a tenant context inherits
        the parent's tenant — contextvars are copied on task creation."""

        async def inner() -> str | None:
            return get_tenant()

        tok = set_tenant("parent-task")
        try:
            got = await inner()
            assert got == "parent-task"
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_create_task_inherits_parent_tenant(self):
        """asyncio.create_task copies the context of the creating coroutine."""

        async def child(results: list[str | None]):
            results.append(get_tenant())

        results: list[str | None] = []
        tok = set_tenant("creator-tenant")
        try:
            t = asyncio.create_task(child(results))
            await t
            assert results == ["creator-tenant"]
        finally:
            reset_tenant(tok)

    @pytest.mark.asyncio
    async def test_task_modifying_tenant_does_not_affect_parent(self):
        """An asyncio task that modifies the tenant context does not
        affect its parent's view."""

        async def modifier():
            tok = set_tenant("modifier-tenant")
            assert get_tenant() == "modifier-tenant"
            reset_tenant(tok)

        tok = set_tenant("parent-tenant")
        try:
            await asyncio.create_task(modifier())
            assert get_tenant() == "parent-tenant"
        finally:
            reset_tenant(tok)


# ---------------------------------------------------------------------------
# Context copy and thread-safety
# ---------------------------------------------------------------------------


class TestContextCopyAndThreadSafety:
    def test_copy_context_captures_current_value(self):
        tok = set_tenant("frozen")
        try:
            ctx = copy_context()
            assert ctx.run(get_tenant) == "frozen"
            reset_tenant(tok)
            assert get_tenant() is None
            assert ctx.run(get_tenant) == "frozen"
        finally:
            pass  # already reset

    def test_copy_context_mutation_isolated(self):
        tok = set_tenant("original")
        try:
            ctx = copy_context()
            ctx.run(set_tenant, "modified-in-copy")

            assert ctx.run(get_tenant) == "modified-in-copy"
            assert get_tenant() == "original"
        finally:
            reset_tenant(tok)

    def test_copy_context_run_reset_does_not_affect_parent(self):
        tok = set_tenant("parent")
        try:
            ctx = copy_context()
            inner_tok = ctx.run(set_tenant, "child")
            ctx.run(reset_tenant, inner_tok)
            assert ctx.run(get_tenant) == "parent"
            assert get_tenant() == "parent"
        finally:
            reset_tenant(tok)


# ---------------------------------------------------------------------------
# Token lifecycle and garbage collection
# ---------------------------------------------------------------------------


class TestTokenLifecycle:
    def test_unreferenced_token_does_not_leak(self):
        """Minting a token and discarding it after reset returns to default."""
        tok = set_tenant("temp")
        assert get_tenant() == "temp"
        reset_tenant(tok)
        assert get_tenant() is None
        del tok

        tok2 = set_tenant("temp2")
        assert get_tenant() == "temp2"
        reset_tenant(tok2)
        assert get_tenant() is None
        del tok2

    def test_set_then_forget_then_reset_default_clean(self):
        """Set a tenant, never store the token, and the context
        should be clean on fresh context."""
        ctx = copy_context()
        ctx.run(set_tenant, "orphan")
        assert ctx.run(get_tenant) == "orphan"
        # Main context unaffected
        assert get_tenant() is None


# ---------------------------------------------------------------------------
# Chained context manager pattern
# ---------------------------------------------------------------------------


class TestContextManagerPattern:
    def test_manual_enter_exit_mimics_context_manager(self):
        tok = set_tenant("cm-test")
        try:
            assert get_tenant() == "cm-test"
        finally:
            reset_tenant(tok)
        assert get_tenant() is None

    def test_nested_try_finally_correct_unwind_on_exception(self):
        tok1 = set_tenant("outer-cm")
        try:
            tok2 = set_tenant("inner-cm")
            try:
                assert get_tenant() == "inner-cm"
                raise RuntimeError("boom")
            except RuntimeError:
                reset_tenant(tok2)
            assert get_tenant() == "outer-cm"
        finally:
            reset_tenant(tok1)
        assert get_tenant() is None

    def test_exception_during_set_tenant(self):
        """If set_tenant succeeds but the immediate code raises,
        the tenant should be reset."""
        with pytest.raises(ZeroDivisionError):
            tok = set_tenant("div-by-zero")
            try:
                _ = 1 / 0
            finally:
                reset_tenant(tok)
        assert get_tenant() is None


# ---------------------------------------------------------------------------
# Concurrent write stress
# ---------------------------------------------------------------------------


class TestConcurrentWriteStress:
    @pytest.mark.asyncio
    async def test_many_tasks_set_reset_serially_stay_clean(self):
        """Many asyncio tasks serially set and reset — final state is None."""

        async def set_and_reset(label: str):
            tok = set_tenant(label)
            await asyncio.sleep(0)
            got = get_tenant()
            reset_tenant(tok)
            return got

        for i in range(50):
            result = await set_and_reset(f"serial-{i}")
            assert result == f"serial-{i}"
        assert get_tenant() is None

    @pytest.mark.asyncio
    async def test_interleaved_gather_maintains_isolation(self):
        """Multiple gather'd tasks each set/reset — none leak to main."""

        async def worker(label: str, results: list[str | None]):
            tok = set_tenant(label)
            results.append(get_tenant())
            reset_tenant(tok)

        results: list[str | None] = []
        tasks = [worker(f"w{i}", results) for i in range(20)]
        await asyncio.gather(*tasks)
        assert len(results) == 20
        assert all(r is not None for r in results)
        assert get_tenant() is None


# ---------------------------------------------------------------------------
# Default value verification
# ---------------------------------------------------------------------------


class TestDefaultValue:
    def test_default_is_none_at_module_load(self):
        assert get_tenant() is None

    def test_default_is_none_after_full_cycle(self):
        tok = set_tenant("cycle")
        reset_tenant(tok)
        assert get_tenant() is None

    def test_default_is_none_in_fresh_context(self):
        ctx = copy_context()
        assert ctx.run(get_tenant) is None
