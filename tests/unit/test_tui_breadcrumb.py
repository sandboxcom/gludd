"""Deep tests for breadcrumb navigation module."""

from __future__ import annotations

from general_ludd.tui.breadcrumb import pop_breadcrumb, push_breadcrumb, render_breadcrumb


class TestPushBreadcrumb:
    def test_push_adds_to_empty_state(self):
        state: dict = {}
        push_breadcrumb(state, "projects")
        assert state["breadcrumb"] == ["main", "projects"]

    def test_push_adds_to_existing_stack(self):
        state = {"breadcrumb": ["main", "projects"]}
        push_breadcrumb(state, "models")
        assert state["breadcrumb"] == ["main", "projects", "models"]

    def test_push_duplicate_last_view_is_noop(self):
        state = {"breadcrumb": ["main", "projects"]}
        push_breadcrumb(state, "projects")
        assert state["breadcrumb"] == ["main", "projects"]

    def test_push_none_breadcrumb_seeds_main(self):
        state = {"breadcrumb": None}
        push_breadcrumb(state, "workers")
        assert state["breadcrumb"] == ["main", "workers"]

    def test_push_empty_list_seeds_main(self):
        state = {"breadcrumb": []}
        push_breadcrumb(state, "agents")
        assert state["breadcrumb"] == ["main", "agents"]

    def test_push_preserves_existing_non_main_base(self):
        state = {"breadcrumb": ["custom_root"]}
        push_breadcrumb(state, "hooks")
        assert state["breadcrumb"] == ["custom_root", "hooks"]

    def test_push_many_views_chains_correctly(self):
        state: dict = {}
        for view in ("projects", "models", "workers", "config"):
            push_breadcrumb(state, view)
        assert state["breadcrumb"] == ["main", "projects", "models", "workers", "config"]

    def test_push_duplicate_interleaved(self):
        state: dict = {}
        push_breadcrumb(state, "projects")
        push_breadcrumb(state, "models")
        push_breadcrumb(state, "projects")
        assert state["breadcrumb"] == ["main", "projects", "models", "projects"]


class TestPopBreadcrumb:
    def test_pop_from_single_returns_main(self):
        state = {"breadcrumb": ["main"]}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_from_two_entries_returns_first(self):
        state = {"breadcrumb": ["main", "projects"]}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_from_deep_stack_returns_parent(self):
        state = {"breadcrumb": ["main", "projects", "models", "config"]}
        result = pop_breadcrumb(state)
        assert result == "models"
        assert state["breadcrumb"] == ["main", "projects", "models"]

    def test_pop_multiple_times_returns_to_main(self):
        state = {"breadcrumb": ["main", "a", "b", "c"]}
        pop_breadcrumb(state)
        pop_breadcrumb(state)
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_from_none_breadcrumb_returns_main(self):
        state = {"breadcrumb": None}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_from_empty_state_returns_main(self):
        state: dict = {}
        result = pop_breadcrumb(state)
        assert result == "main"
        assert state["breadcrumb"] == ["main"]

    def test_pop_never_empties_stack(self):
        state: dict = {}
        for _ in range(10):
            result = pop_breadcrumb(state)
        assert result == "main"
        assert len(state["breadcrumb"]) >= 1


class TestRenderBreadcrumb:
    def test_render_single_entry(self):
        assert render_breadcrumb(["main"]) == "main"

    def test_render_two_entries(self):
        assert render_breadcrumb(["main", "projects"]) == "main > projects"

    def test_render_deep_stack(self):
        assert render_breadcrumb(["main", "a", "b", "c"]) == "main > a > b > c"

    def test_render_empty_list(self):
        assert render_breadcrumb([]) == ""

    def test_render_with_spaces_in_names(self):
        assert render_breadcrumb(["main", "edit config"]) == "main > edit config"

    def test_render_roundtrip_with_push_pop(self):
        state: dict = {}
        push_breadcrumb(state, "projects")
        push_breadcrumb(state, "models")
        bc = state["breadcrumb"]
        assert render_breadcrumb(bc) == "main > projects > models"
        pop_breadcrumb(state)
        assert render_breadcrumb(state["breadcrumb"]) == "main > projects"
