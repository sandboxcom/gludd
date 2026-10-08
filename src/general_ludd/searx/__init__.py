"""Native SearXNG lifecycle and search integration."""
from __future__ import annotations

from general_ludd.searx.native import (
    NativeSearxRuntime,
    RemoteSearxAdapter,
    SearxError,
    SearxLifecycleError,
    SearxSearchError,
    SearxUnavailableError,
)
from general_ludd.searx.server import SearXServer

__all__ = [
    "NativeSearxRuntime",
    "RemoteSearxAdapter",
    "SearXServer",
    "SearxError",
    "SearxLifecycleError",
    "SearxSearchError",
    "SearxUnavailableError",
]
