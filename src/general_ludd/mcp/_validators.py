"""Reusable MCP model constraints backed by Pydantic's schema primitives."""

from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

TrimmedNonEmptyStr = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, strict=True),
]
