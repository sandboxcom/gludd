"""Shared resource ceilings for every dispatch surface."""

from typing import Final

# D-16: one model response or HTTP request must never fan out into an
# unbounded number of tool calls. Ownership lives below transport adapters so
# the HTTP router, worker, event loop, and tool loop share one dependency.
MAX_CALLS_PER_REQUEST: Final[int] = 20

__all__ = ["MAX_CALLS_PER_REQUEST"]
