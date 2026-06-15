"""TDD tests for VariableStore and apply_results.

Verifies: set/get/namespacing, render reflects an applied dispatch result
(the "live update" property required by task #26).
"""

from __future__ import annotations

from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
from general_ludd.dispatch.variable_store import VariableStore, apply_results

# ---------------------------------------------------------------------------
# Basic set / get
# ---------------------------------------------------------------------------

class TestVariableStoreSetGet:
    def test_set_and_get_simple(self):
        store = VariableStore()
        store.set("env", "model", "gpt-4o")
        assert store.get("env", "model") == "gpt-4o"

    def test_get_missing_key_returns_default(self):
        store = VariableStore()
        assert store.get("ns", "missing") is None

    def test_get_missing_key_custom_default(self):
        store = VariableStore()
        assert store.get("ns", "missing", "fallback") == "fallback"

    def test_get_missing_namespace_returns_default(self):
        store = VariableStore()
        assert store.get("no_such_ns", "key") is None

    def test_overwrite_value(self):
        store = VariableStore()
        store.set("ns", "x", 1)
        store.set("ns", "x", 2)
        assert store.get("ns", "x") == 2

    def test_different_namespaces_isolated(self):
        store = VariableStore()
        store.set("a", "key", "alpha")
        store.set("b", "key", "beta")
        assert store.get("a", "key") == "alpha"
        assert store.get("b", "key") == "beta"

    def test_get_namespace_returns_copy(self):
        store = VariableStore()
        store.set("ns", "k", 42)
        ns = store.get_namespace("ns")
        assert ns == {"k": 42}
        # Modifying the copy does not affect the store
        ns["k"] = 99
        assert store.get("ns", "k") == 42

    def test_get_namespace_missing_returns_empty(self):
        store = VariableStore()
        assert store.get_namespace("no_such") == {}


# ---------------------------------------------------------------------------
# Namespacing / isolation
# ---------------------------------------------------------------------------

class TestVariableStoreNamespacing:
    def test_all_vars_uses_double_underscore_separator(self):
        store = VariableStore()
        store.set("dispatch", "last__ok", True)
        flat = store.all_vars()
        assert "dispatch__last__ok" in flat
        assert flat["dispatch__last__ok"] is True

    def test_all_vars_multiple_namespaces(self):
        store = VariableStore()
        store.set("a", "x", 1)
        store.set("b", "y", 2)
        flat = store.all_vars()
        assert flat.get("a__x") == 1
        assert flat.get("b__y") == 2

    def test_all_vars_empty_store(self):
        store = VariableStore()
        assert store.all_vars() == {}


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

class TestVariableStoreRender:
    def test_render_simple_interpolation(self):
        store = VariableStore()
        store.set("env", "name", "Claude")
        result = store.render("Hello {{ env__name }}!")
        assert result == "Hello Claude!"

    def test_render_with_extra_kwargs(self):
        store = VariableStore()
        result = store.render("{{ greeting }}, world!", greeting="Hi")
        assert result == "Hi, world!"

    def test_render_extra_overrides_store(self):
        store = VariableStore()
        store.set("ns", "val", "from-store")
        # Extra kwargs can add new vars
        result = store.render("{{ ns__val }} {{ extra }}", extra="from-extra")
        assert "from-store" in result
        assert "from-extra" in result

    def test_render_missing_var_does_not_raise(self):
        """Undefined variables should not raise; they produce empty string."""
        store = VariableStore()
        result = store.render("prefix {{ missing_var }} suffix")
        # Jinja2 Undefined renders as empty string
        assert "prefix" in result
        assert "suffix" in result

    def test_render_no_variables(self):
        store = VariableStore()
        assert store.render("static text") == "static text"

    def test_render_with_dispatch_result_applied(self):
        """Core 'live update' property: apply a DispatchResult, then render."""
        store = VariableStore()
        result = DispatchResult(
            ok=True, kind="skill", name="grep_skill", output="found 3 matches"
        )
        apply_results(store, [result])
        rendered = store.render(
            "Tool ran: {{ dispatch__last__ok }} output={{ dispatch__last__output }}"
        )
        assert "True" in rendered
        assert "found 3 matches" in rendered


# ---------------------------------------------------------------------------
# apply_results
# ---------------------------------------------------------------------------

class TestApplyResults:
    def test_single_ok_result(self):
        store = VariableStore()
        r = DispatchResult(ok=True, kind="role", name="planner", output={"plan": [1, 2]})
        apply_results(store, [r])
        assert store.get("dispatch", "planner__ok") is True
        assert store.get("dispatch", "planner__output") == {"plan": [1, 2]}
        assert store.get("dispatch", "planner__error") is None

    def test_last_pointer_updated(self):
        store = VariableStore()
        r1 = DispatchResult(ok=True, kind="role", name="alpha", output="a")
        r2 = DispatchResult(ok=False, kind="mcp", name="beta", error="boom")
        apply_results(store, [r1, r2])
        # last should point to r2
        assert store.get("dispatch", "last__name") == "beta"
        assert store.get("dispatch", "last__ok") is False
        assert store.get("dispatch", "last__error") == "boom"

    def test_empty_results_does_not_crash(self):
        store = VariableStore()
        apply_results(store, [])
        assert store.all_vars() == {}

    def test_multiple_results_all_stored(self):
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="role", name="a", output=1),
            DispatchResult(ok=True, kind="mcp", name="b", output=2),
            DispatchResult(ok=True, kind="skill", name="c", output=3),
        ]
        apply_results(store, results)
        assert store.get("dispatch", "a__output") == 1
        assert store.get("dispatch", "b__output") == 2
        assert store.get("dispatch", "c__output") == 3

    def test_name_with_dash_is_normalised(self):
        """Dashes in tool names are replaced with underscores for valid Jinja2 ids."""
        store = VariableStore()
        r = DispatchResult(ok=True, kind="skill", name="my-skill", output="x")
        apply_results(store, [r])
        # key uses underscore
        assert store.get("dispatch", "my_skill__ok") is True

    def test_name_with_dot_is_normalised(self):
        store = VariableStore()
        r = DispatchResult(ok=True, kind="mcp", name="fs.read", output="data")
        apply_results(store, [r])
        assert store.get("dispatch", "fs_read__ok") is True

    def test_live_update_renders_correctly(self):
        """Full round-trip: dispatch → apply → render → reflected in next turn."""
        store = VariableStore()
        result = DispatchResult(ok=True, kind="role", name="coder", output="patch.diff")
        apply_results(store, [result])
        next_prompt = store.render(
            "Previous tool output: {{ dispatch__coder__output }}. Continue."
        )
        assert "patch.diff" in next_prompt
