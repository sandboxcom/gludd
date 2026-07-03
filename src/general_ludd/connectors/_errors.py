"""Shared connector error classes.

Every connector that raises an ``SSRFError`` or ``ConnectorConfigError``
should import from here instead of defining its own local copy.
"""

from __future__ import annotations


class SSRFError(ValueError):
    """Raised when ``base_url`` points at a forbidden (internal) host."""


class ConnectorConfigError(ValueError):
    """Invalid connector config or a blocked ``base_url`` host."""
