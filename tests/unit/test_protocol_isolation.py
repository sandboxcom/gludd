"""Regression tests for process-global typing protocol isolation."""

from __future__ import annotations

import asyncio
import inspect
import sys

import anyio
import anyio.from_thread
import pytest

from general_ludd.issue_sources.gitlab_issues import HTTPResponse, HTTPTransport

pytestmark = pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="typing.get_protocol_members + AwaitableOrContextManager require Python 3.12+",
)

if sys.version_info >= (3, 12):
    from starlette._utils import AwaitableOrContextManager


def test_gitlab_protocols_do_not_corrupt_starlette_awaitability() -> None:
    """Importing an adapter must not mutate the shared Protocol metaclass."""
    from typing import get_protocol_members

    async def _is_event_awaitable() -> bool:
        return inspect.isawaitable(anyio.Event())

    assert get_protocol_members(HTTPResponse) == frozenset({"status_code", "json"})
    assert get_protocol_members(HTTPTransport) == frozenset({"__call__"})
    assert get_protocol_members(AwaitableOrContextManager) == frozenset({"__aenter__", "__aexit__", "__await__"})
    assert anyio.from_thread.isawaitable is inspect.isawaitable
    assert not asyncio.run(_is_event_awaitable())
