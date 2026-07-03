"""Shared injectable-transport Protocol classes.

All connector modules may import these structural Protocols so they do not
need to redefine identical ``HttpResponse`` / ``_Response`` / ``Response``
shapes locally.  Every Protocol here is ``@runtime_checkable`` so
``isinstance`` checks work correctly in tests.

Transport Protocols (``HttpTransport`` / ``_Transport`` / ``Transport``) are
NOT included here because their signatures vary per connector — each module
defines the exact transport contract it needs.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HttpResponse(Protocol):
    """Structural contract for an HTTP response object.

    Compatible with ``requests`` / ``httpx`` responses and trivially
    mockable in tests.  Connectors that previously defined
    ``_Response`` or ``Response`` with the same shape should import
    this in place of their local definition.
    """

    status_code: int

    @property
    def text(self) -> str:
        ...

    def json(self) -> Any:  # pragma: no cover - structural typing only
        ...
