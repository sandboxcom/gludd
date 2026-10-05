"""Governance knowledge package.

Exposes the governance collection's knowledge modules (borders, governing
bodies, treaties, tax/currency, civic services, decision makers) to the
Python application layer and CLI.

The data lives in the ansible collection's ``module_utils`` directory; this
package provides a loader that dynamically imports those modules by file
path so the CLI can access them without requiring the collection to be on
``sys.path``. Governance contracts remain importable from
``general_ludd.governance.contracts`` / ``.core`` directly.
"""

from __future__ import annotations

from general_ludd.governance.loader import (
    get_authority_registry,
    get_borders,
    get_civic_services,
    get_classification_markings,
    get_conflicts_treaties,
    get_decision_makers,
    get_elections_voting,
    get_governing_bodies,
    get_info_classification,
    get_international_relations,
    get_jurisdictions,
    get_legal_systems,
    get_licenses_permits,
    get_military_service,
    get_postal_delivery,
    get_public_finance,
    get_tax_currency,
)

__all__ = [
    "get_authority_registry",
    "get_borders",
    "get_civic_services",
    "get_classification_markings",
    "get_conflicts_treaties",
    "get_decision_makers",
    "get_elections_voting",
    "get_governing_bodies",
    "get_info_classification",
    "get_international_relations",
    "get_jurisdictions",
    "get_legal_systems",
    "get_licenses_permits",
    "get_military_service",
    "get_postal_delivery",
    "get_public_finance",
    "get_tax_currency",
]
