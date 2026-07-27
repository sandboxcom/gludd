# general_ludd.governance

Governance collection: human governance systems — roles and module_utils that
enable agents to navigate the institutions, records, and instruments by which
populations are administered across 18 domains.

## Module Utils (`plugins/module_utils/`)

Each module provides a self-contained knowledge domain with data, lookup
functions, and search capabilities:

- `authority_registry.py` — issuing authority registry (passports, IDs, permits)
- `borders.py` — border crossing data, checkpoints, demilitarized zones
- `civic_services.py` — civic service definitions and lookup by country
- `classification_markings.py` — classification banner formats, caveats, systems
- `conflicts_treaties.py` — active conflicts, treaty database, alliances
- `decision_makers.py` — decision-maker profiles, influence networks, proclivity
- `elections_voting.py` — elections, voting methods, electoral systems
- `governing_bodies.py` — international bodies, councils, organizations
- `info_classification.py` — information classification, FOIA, clearance equivalence
- `international_relations.py` — diplomatic relations, sanctions, alliances
- `jurisdictions.py` — jurisdictional codes (ISO 3166), subdivisions, parents
- `legal_systems.py` — legal systems, court hierarchies, rights charters
- `licenses_permits.py` — professional licenses, permits, export controls
- `military_service.py` — conscription, military branches, veteran benefits
- `postal_delivery.py` — postal codes, courier tracking, customs declarations
- `public_finance.py` — government budgets, sovereign debt, pension systems
- `tax_currency.py` — tax systems, tax authorities, currencies (ISO 4217)

## Roles (`roles/`)

Ansible roles that wrap the module_utils for playbook-based governance lookups:

- `borders` — border crossing information
- `civic_service_finder` — find civic services by name and country
- `civic_services` — civic service data and lookup
- `conflicts` — active conflicts and military engagements
- `conflicts_treaties_lookup` — treaty and conflict database queries
- `currencies` — currency information and conversion data
- `decision_maker_lookup` — decision-maker authority and proclivity
- `decision_makers` — decision-maker profiles and influence mapping
- `governance_navigator` — natural language query routing across domains
- `governing_bodies` — international governing body lookup
- `info_classification` — classification system and banner formats
- `info_classification_check` — FOIA procedures and clearance checks
- `licenses_permits` — license types and permit requirements
- `lookup_governing_body` — governing body search by ID or alias
- `military_service` — military conscription and branches
- `navigate_borders` — border crossing and region lookup
- `postal_delivery` — postal formats, couriers, customs
- `tax_currency_info` — tax system and currency data
- `tax_systems` — national tax authority information
- `treaties` — international treaties and agreements
