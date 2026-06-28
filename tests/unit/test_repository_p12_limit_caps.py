"""Guard for P12: explicit caller limits in list/claim repository methods are
themselves capped at _DEFAULT_LIST_LIMIT so a huge `limit=` can't load an
unbounded result set, and unbounded list reads gain a cap.

These are defensive `min(limit, _DEFAULT_LIST_LIMIT)` wraps (and one added
`.limit(_DEFAULT_LIST_LIMIT)`); a source-level guard pins them against removal
(the methods need >1000 seeded rows to exercise functionally, which is not worth
the fixture cost for a one-line defensive cap).
"""

from __future__ import annotations

import inspect

from general_ludd.db import repository as repo_mod


def _method_src(qualname_parts: tuple[str, str]) -> str:
    cls = getattr(repo_mod, qualname_parts[0])
    return inspect.getsource(getattr(cls, qualname_parts[1]))


def test_default_list_limit_defined() -> None:
    assert isinstance(repo_mod._DEFAULT_LIST_LIMIT, int)
    assert repo_mod._DEFAULT_LIST_LIMIT > 0


def test_claim_runnable_caps_limit() -> None:
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in _method_src(("TodoRepository", "claim_runnable"))


def test_claim_unreviewed_caps_limit() -> None:
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in _method_src(
        ("TaskReturnRepository", "claim_unreviewed")
    )


def test_list_recent_caps_limit() -> None:
    assert "min(limit, _DEFAULT_LIST_LIMIT)" in _method_src(("BenchmarkRepository", "list_recent"))


def test_list_active_is_bounded() -> None:
    src = _method_src(("ProjectRepository", "list_active"))
    assert ".limit(_DEFAULT_LIST_LIMIT)" in src
