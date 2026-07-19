"""Governance knowledge package — exposes the governance collection's
knowledge modules (borders, governing bodies, treaties, tax/currency,
civic services, decision makers) to the Python application layer and CLI.

The data lives in the ansible collection's ``module_utils`` directory; this
package provides a loader that dynamically imports those modules by file
path so the CLI can access them without requiring the collection to be on
``sys.path``.
"""

from __future__ import annotations

from general_ludd.governance.loader import (
    get_borders,
    get_civic_services,
    get_conflicts_treaties,
    get_governing_bodies,
    get_tax_currency,
)

__all__ = [
    "get_borders",
    "get_civic_services",
    "get_conflicts_treaties",
    "get_governing_bodies",
    "get_tax_currency",
]
