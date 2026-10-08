"""Shared error taxonomy for native and remote SearXNG adapters."""


class SearxError(RuntimeError):
    """Base error for the Gludd SearX integration."""


class SearxUnavailableError(SearxError):
    """Raised when the official upstream Python package is unavailable."""


class SearxLifecycleError(SearxError):
    """Raised when runtime startup, health, or cleanup fails."""


class SearxSearchError(SearxError):
    """Raised when a search cannot return a valid response."""
