"""Breadcrumb navigation for TUI."""

from __future__ import annotations

from typing import Any


def push_breadcrumb(state: dict[str, Any], view: str) -> None:
    bc: list[str] = state.get("breadcrumb", ["main"]) or ["main"]
    if bc[-1] != view:
        bc.append(view)
    state["breadcrumb"] = bc


def pop_breadcrumb(state: dict[str, Any]) -> str:
    bc: list[str] = state.get("breadcrumb", ["main"]) or ["main"]
    if len(bc) > 1:
        bc.pop()
    state["breadcrumb"] = bc
    return bc[-1]


def render_breadcrumb(breadcrumb: list[str]) -> str:
    return " > ".join(breadcrumb)
