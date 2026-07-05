"""Integration tests for VariableStore → prompt re-render cycle.

Proves the dynamic-dispatcher feature (70% → 85%): DispatchResult → VariableStore
→ Jinja2 template re-render so the model sees fresh reality in its next prompt.
"""

from __future__ import annotations

from general_ludd.dispatch.dynamic_dispatcher import DispatchResult
from general_ludd.dispatch.variable_store import VariableStore, apply_results

# ---------------------------------------------------------------------------
# VariableStore — set / get across namespaces
# ---------------------------------------------------------------------------


class TestVariableStoreSetGet:
    def test_set_and_get_same_namespace(self):
        store = VariableStore()
        store.set("dispatch", "tool_a__ok", True)
        assert store.get("dispatch", "tool_a__ok") is True

    def test_set_and_get_different_namespaces(self):
        store = VariableStore()
        store.set("dispatch", "result", 42)
        store.set("project", "version", "0.1.0")
        assert store.get("dispatch", "result") == 42
        assert store.get("project", "version") == "0.1.0"

    def test_get_missing_key_returns_default(self):
        store = VariableStore()
        assert store.get("dispatch", "nonexistent") is None
        assert store.get("dispatch", "nonexistent", default="fallback") == "fallback"

    def test_get_missing_namespace_returns_default(self):
        store = VariableStore()
        assert store.get("unknown", "key", default=99) == 99

    def test_set_overwrites_existing_key(self):
        store = VariableStore()
        store.set("dispatch", "x", 1)
        store.set("dispatch", "x", 2)
        assert store.get("dispatch", "x") == 2

    def test_get_namespace_returns_shallow_copy(self):
        store = VariableStore()
        store.set("ns", "a", 1)
        store.set("ns", "b", 2)
        ns_copy = store.get_namespace("ns")
        assert ns_copy == {"a": 1, "b": 2}
        # Mutating the copy does not affect the store
        ns_copy["a"] = 99
        assert store.get("ns", "a") == 1

    def test_get_namespace_missing_returns_empty_dict(self):
        store = VariableStore()
        assert store.get_namespace("missing") == {}


# ---------------------------------------------------------------------------
# VariableStore — all_vars flattening
# ---------------------------------------------------------------------------


class TestVariableStoreAllVars:
    def test_all_vars_flattens_with_double_underscore(self):
        store = VariableStore()
        store.set("dispatch", "tool_a__ok", True)
        store.set("dispatch", "tool_a__output", "hello")
        store.set("project", "name", "gludd")
        flat = store.all_vars()
        assert flat["dispatch__tool_a__ok"] is True
        assert flat["dispatch__tool_a__output"] == "hello"
        assert flat["project__name"] == "gludd"

    def test_all_vars_empty_store_returns_empty_dict(self):
        store = VariableStore()
        assert store.all_vars() == {}

    def test_all_vars_multiple_namespaces_no_collision(self):
        store = VariableStore()
        store.set("ns1", "key", "v1")
        store.set("ns2", "key", "v2")
        flat = store.all_vars()
        assert flat["ns1__key"] == "v1"
        assert flat["ns2__key"] == "v2"


# ---------------------------------------------------------------------------
# VariableStore — render
# ---------------------------------------------------------------------------


class TestVariableStoreRender:
    def test_render_simple_template(self):
        store = VariableStore()
        store.set("dispatch", "tool_a__ok", True)
        store.set("dispatch", "tool_a__output", "done")
        result = store.render("Result: {{ dispatch__tool_a__ok }} — {{ dispatch__tool_a__output }}")
        assert "True" in result
        assert "done" in result

    def test_render_with_extra_kwargs(self):
        store = VariableStore()
        store.set("ns", "greeting", "hello")
        result = store.render("{{ ns__greeting }}, {{ name }}!", name="world")
        assert result == "hello, world!"

    def test_render_extra_overrides_stored_var(self):
        store = VariableStore()
        store.set("ns", "val", "stored")
        result = store.render("{{ ns__val }}", ns__val="override")
        assert result == "override"

    def test_render_with_undefined_variable_produces_empty_string(self):
        """Missing variables resolve to empty string (Jinja2 Undefined), not a hard error."""
        store = VariableStore()
        result = store.render("before {{ missing_var }} after")
        assert result == "before  after"

    def test_render_partial_template_with_some_missing(self):
        store = VariableStore()
        store.set("ctx", "known", "yes")
        result = store.render("known={{ ctx__known }} missing={{ ctx__missing }} end")
        assert "known=yes" in result
        # ctx__missing is undefined → empty string
        assert "missing= end" in result or "missing=end" in result

    def test_render_ssti_payload_is_sandboxed(self):
        """{{ ().__class__ }} should fail safely in SandboxedEnvironment.

        The sandbox blocks dunder attribute access; render fails and returns
        the raw template (fail-open). Python internals like 'object' or
        '__bases__' must NOT be present in the output — the sandbox prevented
        evaluation.
        """
        store = VariableStore()
        template = "{{ ().__class__.__mro__ }}"
        result = store.render(template)
        # Fail-open: raw template is returned.
        assert result == template
        assert "object" not in result

    def test_render_ssti_payload_2(self):
        """{{ config }} / {{ self }} should be blocked by the sandbox."""
        store = VariableStore()
        result = store.render("{{ config }}")
        # Jinja2 sandbox blocks access to 'config' — either empty string or
        # the raw template is returned; neither case exposes internals.
        assert "SECRET_KEY" not in result

    def test_render_failure_returns_raw_template(self):
        """On render failure, the raw template string is returned (fail-open)."""
        store = VariableStore()
        template = "{{ ().__class__.__mro__ }}"
        result = store.render(template)
        # The sandbox should block SSTI, triggering the exception handler.
        # Result must be the raw template, not an empty string.
        assert result == template


# ---------------------------------------------------------------------------
# apply_results
# ---------------------------------------------------------------------------


class TestApplyResults:
    def test_apply_single_success_result(self):
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="fs.read", output="file contents", error=None),
        ]
        apply_results(store, results)
        assert store.get("dispatch", "fs_DOT_read__ok") is True
        assert store.get("dispatch", "fs_DOT_read__output") == "file contents"
        assert store.get("dispatch", "fs_DOT_read__error") is None

    def test_apply_single_error_result(self):
        store = VariableStore()
        results = [
            DispatchResult(ok=False, kind="role", name="validator", output=None, error="validation failed"),
        ]
        apply_results(store, results)
        assert store.get("dispatch", "validator__ok") is False
        assert store.get("dispatch", "validator__output") is None
        assert store.get("dispatch", "validator__error") == "validation failed"

    def test_apply_multiple_results(self):
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="a", output="out-a", error=None),
            DispatchResult(ok=False, kind="skill", name="b", output=None, error="err-b"),
        ]
        apply_results(store, results)
        assert store.get("dispatch", "a__ok") is True
        assert store.get("dispatch", "a__output") == "out-a"
        assert store.get("dispatch", "b__ok") is False
        assert store.get("dispatch", "b__error") == "err-b"

    def test_apply_results_empty_list_noop(self):
        store = VariableStore()
        apply_results(store, [])
        assert store.all_vars() == {}

    def test_apply_results_last_family_for_single_result(self):
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="only", output="result", error=None),
        ]
        apply_results(store, results)
        assert store.get("dispatch", "last__ok") is True
        assert store.get("dispatch", "last__output") == "result"
        assert store.get("dispatch", "last__error") is None
        assert store.get("dispatch", "last__name") == "only"
        assert store.get("dispatch", "last__kind") == "mcp"

    def test_apply_results_last_family_for_multiple(self):
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="first", output="a", error=None),
            DispatchResult(ok=False, kind="skill", name="second", output=None, error="fail"),
        ]
        apply_results(store, results)
        # last__* should reflect the LAST result
        assert store.get("dispatch", "last__ok") is False
        assert store.get("dispatch", "last__output") is None
        assert store.get("dispatch", "last__error") == "fail"
        assert store.get("dispatch", "last__name") == "second"
        assert store.get("dispatch", "last__kind") == "skill"
        # But the first result is still stored under its own key
        assert store.get("dispatch", "first__ok") is True
        assert store.get("dispatch", "first__output") == "a"

    def test_apply_results_safe_name_replaces_dots_and_dashes(self):
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="tool.name-here", output="x", error=None),
        ]
        apply_results(store, results)
        # Dots → _DOT_, dashes → _DASH_
        assert store.get("dispatch", "tool_DOT_name_DASH_here__ok") is True

    def test_apply_results_and_render_cycle(self):
        """Full cycle: apply results → render template with dispatch vars."""
        store = VariableStore()
        results = [
            DispatchResult(ok=True, kind="mcp", name="filesystem", output="3 files found", error=None),
            DispatchResult(ok=False, kind="skill", name="scan", output=None, error="timeout"),
        ]
        apply_results(store, results)
        template = (
            "Last call '{{ dispatch__last__name }}' "
            "{{ 'succeeded' if dispatch__last__ok else 'failed' }}. "
            "Filesystem output: {{ dispatch__filesystem__output }}"
        )
        rendered = store.render(template)
        assert "failed" in rendered
        assert "Last call 'scan'" in rendered  # last__name is "scan", not "filesystem"
        assert "3 files found" in rendered


# ---------------------------------------------------------------------------
# VariableStore.render — Jinja2 control flow
# ---------------------------------------------------------------------------


class TestVariableStoreRenderControlFlow:
    def test_render_jinja2_conditional_true(self):
        store = VariableStore()
        store.set("dispatch", "a__ok", True)
        result = store.render("{% if dispatch__a__ok %}GREEN{% else %}RED{% endif %}")
        assert result == "GREEN"

    def test_render_jinja2_conditional_false(self):
        store = VariableStore()
        store.set("dispatch", "a__ok", False)
        result = store.render("{% if dispatch__a__ok %}GREEN{% else %}RED{% endif %}")
        assert result == "RED"

    def test_render_jinja2_loop(self):
        store = VariableStore()
        store.set("ctx", "items", ["a", "b", "c"])
        result = store.render("{% for item in ctx__items %}{{ item }}{% if not loop.last %},{% endif %}{% endfor %}")
        assert result == "a,b,c"

    def test_render_jinja2_filter(self):
        store = VariableStore()
        store.set("ctx", "text", "  hello  ")
        result = store.render("{{ ctx__text | trim }}")
        assert result == "hello"

    def test_render_empty_template_returns_empty_string(self):
        store = VariableStore()
        assert store.render("") == ""
