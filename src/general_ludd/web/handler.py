"""Dispatch handler factory wiring the web toolkit into DynamicDispatcher.

``make_web_handler`` returns a ``(name, args) -> dict`` callable suitable for the
``web_handler=`` slot of :class:`~general_ludd.dispatch.dynamic_dispatcher.DynamicDispatcher`.
It routes a tool name in ``{fetch_raw, fetch_parsed, search_gather, crawl_site,
render_js}`` to the flat function in :mod:`general_ludd.web.tools` and returns
``WebResult.model_dump(mode="json")`` so the result is JSON-serialisable over
``/api/dispatch``.

An async variant runs the SYNC toolkit via ``asyncio.to_thread`` so it never
blocks the daemon event loop (the SafeFetcher/crawl/search are synchronous).
Capability gating (deny-by-default ``web`` kind) is enforced by the dispatcher
BEFORE this handler is ever invoked.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from general_ludd.web import tools
from general_ludd.web.policy import DEFAULT_POLICY, WebPolicy
from general_ludd.web.search import SearchProvider
from general_ludd.web.types import WebError, WebResult

#: The tool names this handler accepts -> the flat function that serves them.
#: Typed as a generic callable returning WebResult so the dispatch call sites
#: keep a precise return type (the per-tool signatures vary, so the args are Any).
_ToolFn = Callable[..., WebResult]
_TOOLS: dict[str, _ToolFn] = {
    "fetch_raw": tools.fetch_raw,
    "fetch_parsed": tools.fetch_parsed,
    "search_gather": tools.search_gather,
    "crawl_site": tools.crawl_site,
    "render_js": tools.render_js,
}


def _dispatch(name: str, args: dict[str, Any], *, policy: WebPolicy,
              provider: SearchProvider | None) -> WebResult:
    fn = _TOOLS.get(name)
    if fn is None:
        return WebResult(
            ok=False, url=str(args.get("url") or args.get("query") or name),
            error=WebError.PARSE_ERROR, detail=f"unknown web tool {name!r}",
        )
    kwargs = dict(args)
    # Positional-first arg per tool; rest keyword.
    if name in ("fetch_raw", "fetch_parsed", "render_js"):
        url = kwargs.pop("url", None)
        if not isinstance(url, str):
            return WebResult(ok=False, url=str(url), error=WebError.PARSE_ERROR,
                             detail="missing 'url' argument")
        kwargs.setdefault("policy", policy)
        return fn(url, **kwargs)
    if name == "crawl_site":
        seed = kwargs.pop("seed_url", None) or kwargs.pop("url", None)
        if not isinstance(seed, str):
            return WebResult(ok=False, url=str(seed), error=WebError.PARSE_ERROR,
                             detail="missing 'seed_url' argument")
        kwargs.setdefault("policy", policy)
        return fn(seed, **kwargs)
    # search_gather
    query = kwargs.pop("query", None)
    if not isinstance(query, str):
        return WebResult(ok=False, url=f"search:{query}", error=WebError.PARSE_ERROR,
                         detail="missing 'query' argument")
    kwargs.setdefault("policy", policy)
    if provider is not None:
        kwargs.setdefault("provider", provider)
    return fn(query, **kwargs)


def make_web_handler(
    *,
    policy: WebPolicy = DEFAULT_POLICY,
    search_provider: SearchProvider | None = None,
) -> Callable[[str, dict[str, Any]], dict[str, Any]]:
    """Build a sync ``(name, args) -> dict`` web dispatch handler."""

    def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = _dispatch(name, args, policy=policy, provider=search_provider)
        return result.model_dump(mode="json")

    return handler


def make_async_web_handler(
    *,
    policy: WebPolicy = DEFAULT_POLICY,
    search_provider: SearchProvider | None = None,
) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    """Build an async handler that runs the sync toolkit via ``to_thread``.

    Keeps the daemon event loop unblocked — the SafeFetcher/crawl/search are
    synchronous and must not run inline on the loop.
    """

    async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        result = await asyncio.to_thread(
            _dispatch, name, args, policy=policy, provider=search_provider
        )
        return result.model_dump(mode="json")

    return handler
