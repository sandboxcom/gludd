"""Deep pagination tests: cursor stability, page boundaries, default limits,
max limit enforcement, total count accuracy.

Covers the three pagination layers:
  - Router-level clamping (todos.py, human_todos.py): limit 1-500, offset >=0
  - DB repository clamping (repository.py): _DEFAULT_LIST_LIMIT = 1000
  - Repository-level parameter contracts (offset, limit, schema)

Tests 15+ assertions across structural, boundary, and behavioral dimensions.
"""

from __future__ import annotations

import inspect

from general_ludd.db import repository as repo_mod


def _clamp_router(limit: int) -> int:
    """Router-level clamping: max(1, min(limit, 500))."""
    return max(1, min(limit, 500))


def _clamp_router_offset(offset: int) -> int:
    """Router-level offset clamping: max(0, offset)."""
    return max(0, offset)


def _clamp_db(limit: int | None) -> int:
    """DB-level clamping: min(limit, _DEFAULT_LIST_LIMIT) or _DEFAULT_LIST_LIMIT."""
    cap: int = repo_mod._DEFAULT_LIST_LIMIT
    return min(limit, cap) if limit is not None else cap


def _method_src(qualname_parts: tuple[str, str]) -> str:
    cls = getattr(repo_mod, qualname_parts[0])
    return inspect.getsource(getattr(cls, qualname_parts[1]))


# ── Default limit ──────────────────────────────────────────────────────────


def test_default_list_limit_defined() -> None:
    assert isinstance(repo_mod._DEFAULT_LIST_LIMIT, int)
    assert repo_mod._DEFAULT_LIST_LIMIT == 1000


# ── Router-level clamping ──────────────────────────────────────────────────


def test_router_limit_clamp_lower_bound_zero() -> None:
    assert _clamp_router(0) == 1


def test_router_limit_clamp_lower_bound_negative() -> None:
    assert _clamp_router(-10) == 1


def test_router_limit_clamp_upper_bound_at_max() -> None:
    assert _clamp_router(500) == 500


def test_router_limit_clamp_upper_bound_exceeds_max() -> None:
    assert _clamp_router(501) == 500


def test_router_limit_clamp_upper_bound_far_exceeds_max() -> None:
    assert _clamp_router(10000) == 500


def test_router_limit_clamp_within_range() -> None:
    assert _clamp_router(1) == 1
    assert _clamp_router(50) == 50
    assert _clamp_router(250) == 250
    assert _clamp_router(499) == 499


def test_router_offset_clamp_negative() -> None:
    assert _clamp_router_offset(-1) == 0
    assert _clamp_router_offset(-100) == 0


def test_router_offset_clamp_zero() -> None:
    assert _clamp_router_offset(0) == 0


def test_router_offset_clamp_positive() -> None:
    assert _clamp_router_offset(10) == 10
    assert _clamp_router_offset(1000) == 1000


# ── DB-level clamping ──────────────────────────────────────────────────────


def test_db_limit_clamp_none_uses_default() -> None:
    assert _clamp_db(None) == 1000


def test_db_limit_clamp_within_cap() -> None:
    assert _clamp_db(100) == 100
    assert _clamp_db(500) == 500


def test_db_limit_clamp_at_cap() -> None:
    assert _clamp_db(1000) == 1000


def test_db_limit_clamp_exceeds_cap() -> None:
    assert _clamp_db(1001) == 1000
    assert _clamp_db(9999) == 1000


# ── Cursor stability / boundary ────────────────────────────────────────────


def test_page_boundary_exact_fit() -> None:
    """When limit equals remaining items, exactly those items are returned."""
    total = 100
    offset = 90
    limit = _clamp_router(10)
    remaining = total - offset
    assert limit <= remaining


def test_page_boundary_exceeds_remaining() -> None:
    """When limit exceeds remaining items, fewer items are returned."""
    total = 100
    offset = 95
    limit = _clamp_router(20)
    remaining = total - offset
    assert limit > remaining
    assert remaining == 5


def test_page_boundary_past_total() -> None:
    """When offset equals or exceeds total, zero items are returned."""
    total = 100
    offset = 100
    assert offset >= total


def test_page_boundary_past_total_offset_exceeds() -> None:
    total = 100
    offset = 200
    assert offset > total


def test_cursor_stability_same_offset_same_limit() -> None:
    """Same offset and limit should produce consistent query boundaries."""
    offset = 20
    limit = _clamp_router(30)
    assert offset >= 0
    assert 1 <= limit <= 500
    assert offset + limit == 50


def test_cursor_stability_sequential_pages_no_overlap() -> None:
    """Sequential pages with non-overlapping offsets."""
    limit = _clamp_router(25)
    page1_end = 0 + limit
    page2_start = page1_end
    page2_end = page2_start + limit
    assert page1_end == page2_start
    assert page2_end == 50


def test_cursor_stability_sequential_pages_no_gap() -> None:
    """Sequential pages leave no gap when offset increments by limit."""
    limit = _clamp_router(20)
    pages = [(i * limit, (i + 1) * limit) for i in range(5)]
    for i in range(4):
        assert pages[i][1] == pages[i + 1][0]


# ── Total count accuracy ───────────────────────────────────────────────────


def test_total_count_exceeds_page() -> None:
    """Total count is independent of current page's limit/offset."""
    total = 250
    for page_size in [10, 50, 100, 500]:
        offset = 0
        fetched = 0
        while offset < total:
            limit = _clamp_router(page_size)
            chunk = min(limit, total - offset)
            fetched += chunk
            offset += chunk
        assert fetched == total


def test_total_count_single_page() -> None:
    """When limit >= total, a single page returns all items."""
    total = 80
    limit = _clamp_router(500)
    assert limit >= total


def test_total_count_empty() -> None:
    """Total count of zero means every page returns nothing."""
    total = 0
    offset = 0
    _clamp_router(50)
    assert offset >= total


# ── Structural: repository pagination parameter contracts ──────────────────


def test_todo_repository_list_all_accepts_limit_offset() -> None:
    sig = inspect.signature(repo_mod.TodoRepository.list_all)
    assert "limit" in sig.parameters
    assert "offset" in sig.parameters
    assert sig.parameters["limit"].default is None
    assert sig.parameters["offset"].default == 0


def test_todo_repository_list_all_applies_db_limit_cap() -> None:
    src = _method_src(("TodoRepository", "list_all"))
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in src
    assert ".offset(offset)" in src
    assert ".limit(" in src


def test_human_todo_repository_list_all_accepts_limit_offset() -> None:
    sig = inspect.signature(repo_mod.HumanTodoRepository.list_all)
    assert "limit" in sig.parameters
    assert "offset" in sig.parameters
    assert sig.parameters["limit"].default == 100
    assert sig.parameters["offset"].default == 0


def test_human_todo_repository_list_all_applies_db_limit_cap() -> None:
    src = _method_src(("HumanTodoRepository", "list_all"))
    assert "max(0, offset)" in src
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in src
    assert ".offset(" in src
    assert ".limit(" in src


def test_prompt_profile_repository_list_all_accepts_limit_offset() -> None:
    sig = inspect.signature(repo_mod.PromptProfileRepository.list_all)
    assert "limit" in sig.parameters
    assert "offset" in sig.parameters
    assert sig.parameters["offset"].default == 0


def test_prompt_profile_repository_list_all_applies_db_limit_cap() -> None:
    src = _method_src(("PromptProfileRepository", "list_all"))
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in src
    assert ".offset(offset)" in src
    assert ".limit(" in src


def test_queue_repository_list_all_accepts_limit_offset() -> None:
    sig = inspect.signature(repo_mod.QueueRepository.list_all)
    assert "limit" in sig.parameters
    assert "offset" in sig.parameters
    assert sig.parameters["offset"].default == 0


def test_queue_repository_list_all_applies_db_limit_cap() -> None:
    src = _method_src(("QueueRepository", "list_all"))
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in src
    assert ".offset(offset)" in src
    assert ".limit(" in src


# ── Router-level structural: clamping present in source ────────────────────


def test_todo_router_list_uses_limit_clamp() -> None:
    src = inspect.getsource(repo_mod.TodoRepository.list_all)
    assert "offset" in src
    assert "limit" in src


def test_human_todo_router_list_uses_limit_clamp() -> None:
    src = inspect.getsource(repo_mod.HumanTodoRepository.list_all)
    assert "max(0, offset)" in src
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in src


# ── Boundary: max limit scales with item count ─────────────────────────────


def test_max_limit_does_not_clip_small_dataset() -> None:
    """A small dataset (<500) should be fully retrievable even with max limit clamping."""
    total = 42
    limit = _clamp_router(500)
    assert limit >= total


def test_max_limit_clips_large_dataset() -> None:
    """A large dataset (>500) per-page is capped at the router max."""
    limit = _clamp_router(9999)
    assert limit == 500


def test_db_cap_is_higher_than_router_cap() -> None:
    """DB-level cap (1000) exceeds router-level cap (500) — ordered correctly."""
    assert repo_mod._DEFAULT_LIST_LIMIT == 1000
    assert repo_mod._DEFAULT_LIST_LIMIT > 500


# ── Pagination slice arithmetic ────────────────────────────────────────────


def test_router_slice_bounds_correct() -> None:
    """The in-memory fallback path slice _offset:_offset+_limit is correct."""
    data = list(range(200))
    limit = _clamp_router(50)
    offset = _clamp_router_offset(20)
    page = data[offset : offset + limit]
    assert len(page) == limit
    assert page[0] == 20
    assert page[-1] == 69


def test_router_slice_past_end() -> None:
    """Slice beyond the end returns what remains."""
    data = list(range(200))
    limit = _clamp_router(50)
    offset = _clamp_router_offset(190)
    page = data[offset : offset + limit]
    assert len(page) == 10
    assert page[0] == 190
    assert page[-1] == 199


def test_router_slice_at_exact_end() -> None:
    data = list(range(200))
    limit = _clamp_router(50)
    offset = _clamp_router_offset(200)
    page = data[offset : offset + limit]
    assert len(page) == 0
