"""Cross-OS event type map, log paths, event bus APIs per platform.

Maps event categories (auth, kernel, app, network, filesystem, security)
to the platform-specific log source, API, and canonical path.
"""

from __future__ import annotations

from typing import TypedDict


class OSEventSource(TypedDict):
    platform: str
    category: str
    source_name: str
    log_path: str
    query_api: str


OS_EVENT_MAP: list[OSEventSource] = []
