"""Compatibility imports for the collection-owned native SearXNG runtime.

The travel collection is the single implementation source so it remains
usable inside an Ansible execution environment without a Gludd source checkout.
This module preserves the pre-S38 Python import path during rolling upgrades.
"""

from __future__ import annotations

from ansible_collections.general_ludd.travel.plugins.module_utils.searxng_runtime import (
    NativeSearxRuntime,
    RemoteSearxAdapter,
    SearxError,
    SearxLifecycleError,
    SearxSearchError,
    SearxUnavailableError,
    default_namespace,
    validate_namespace,
    validate_query,
    validate_search_inputs,
)

__all__ = [
    "NativeSearxRuntime",
    "RemoteSearxAdapter",
    "SearxError",
    "SearxLifecycleError",
    "SearxSearchError",
    "SearxUnavailableError",
    "default_namespace",
    "validate_namespace",
    "validate_query",
    "validate_search_inputs",
]
