"""Governance knowledge package — exposes the governance collection's
knowledge modules (borders, governing bodies, treaties, tax/currency,
civic services, decision makers) to the Python application layer and CLI,
plus the governance contracts (policies, rules, compliance models).

The data lives in the ansible collection's ``module_utils`` directory; this
package provides a loader that dynamically imports those modules by file
path so the CLI can access them without requiring the collection to be on
``sys.path``.
"""

from __future__ import annotations

from general_ludd.governance.contracts import (
    AuditTrail,
    ComplianceModel,
    ComplianceReport,
    GovernancePolicy,
    GovernanceRule,
    Policy,
    PolicyRegistry,
    Rule,
)
from general_ludd.governance.core import CapabilityRouter, ComplianceChecker, PolicyEngine
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
    "AuditTrail",
    "CapabilityRouter",
    "ComplianceChecker",
    "ComplianceModel",
    "ComplianceReport",
    "GovernancePolicy",
    "GovernanceRule",
    "Policy",
    "PolicyEngine",
    "PolicyRegistry",
    "Rule",
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
