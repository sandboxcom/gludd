# Feature: Human Governance Systems Collection

**Status: IN PROGRESS** | **Created: 2026-07-16** | **Target: v0.1.0-beta.3**

## 1. Overview

Ansible collection `general_ludd.governance` providing roles and modules that
enable agents to navigate human governance systems — the institutions, records,
and instruments by which populations are administered across borders, jurisdictions,
and time. A "governance system" here means any formal mechanism a state or
quasi-state uses to classify, tax, move, conscript, license, or otherwise govern
people and goods.

The collection sits alongside `general_ludd.business` (private entities) and
`general_ludd.security` (information security) and is the canonical home for
public-sector / state-facing automation: borders, governing bodies, tax systems,
currencies, conflicts, treaties, civic services, decision-makers, information
classification, postal/delivery, military service, and licenses/permits.

**Coverage**: 12 domains, organized into 6 implementation phases.

## 2. Domains (12)

| Domain | Role (snake_case) | Purpose |
|--------|-------------------|---------|
| Borders | `borders` | Crossing points, visa/waiver regimes, admission records, refusals, exit data |
| Governing bodies | `governing_bodies` | Legislatures, executives, judiciaries, agencies, regulator directories, mandates |
| Tax systems | `tax_systems` | Obligations, filings, withholding, jurisdictional rules, revenue authorities |
| Currencies | `currencies` | Legal tender, FX regimes, monetary authorities, sanctions currencies, CBDCs |
| Conflicts | `conflicts` | Armed conflicts, sanctions, embargoes, no-fly lists, conflict-status lookups |
| Treaties | `treaties` | Bilateral/multilateral agreements, ratification status, reservations, reservations |
| Civic services | `civic_services` | IDs, registrations, social services, records offices, civil registries |
| Decision-makers | `decision_makers` | Office-holders, succession, signing authority, mandate provenance |
| Information classification | `info_classification` | Classification markings, clearance levels, declassification schedules, caveats |
| Postal/delivery | `postal_delivery` | Address normalization, postal codes, courier tracking, customs declarations |
| Military service | `military_service` | Conscription registers, service records, veteran status, reserve obligations |
| Licenses/permits | `licenses_permits` | Driving, professional, business, export, building permits and their status |

## 3. Knowledge Modules (planned, P1 skeleton)

| Module | Content |
|--------|---------|
| `jurisdictions.py` | Jurisdiction identifiers (ISO 3166-1/2, FIPS, GLEIF), hierarchy, sovereignty status |
| `classification_markings.py` | Cross-system classification markings map (US/UK/NATO/EU + caveats) |
| `authority_registry.py` | Mapping of issuing authorities to instrument types (passport, license, treaty, etc.) |

## 4. Implementation Plan

| Phase | Scope | Domains |
|-------|-------|---------|
| **P1** | Scaffold + core modules: `galaxy.yml`, README, role skeletons, jurisdiction/classification/authority modules | (none live) |
| **P2** | Borders + governing bodies | `borders`, `governing_bodies` |
| **P3** | Tax + currency | `tax_systems`, `currencies` |
| **P4** | Conflicts + treaties | `conflicts`, `treaties` |
| **P5** | Civic services + decision-makers | `civic_services`, `decision_makers` |
| **P6** | Information classification + access + remaining domains | `info_classification`, `postal_delivery`, `military_service`, `licenses_permits` |

Each phase ships its roles with `tasks/main.yml` skeletons plus module_utils and
unit tests under `tests/unit/`. A phase is "done" only when `make test` is green
and the role is callable via FQCN `general_ludd.governance.<role>`.

## 5. Files

```text
collections/ansible_collections/general_ludd/governance/
├── galaxy.yml
├── README.md
├── roles/
│   ├── borders/, governing_bodies/, tax_systems/, currencies/,
│   ├── conflicts/, treaties/, civic_services/, decision_makers/,
│   ├── info_classification/, postal_delivery/, military_service/,
│   └── licenses_permits/
├── plugins/module_utils/        (empty in P1)
└── tests/unit/                   (empty in P1)
```

## 6. Dependencies

- `general_ludd.business` (entity research for decision-makers, authorities)
- `general_ludd.security` (classification caveats overlap)
- No external Ansible Galaxy dependencies in P1.

## 7. Open Questions (resolve per-phase)

- Source-of-truth dataset for jurisdiction identifiers (ISO vs GLEIF vs FIPS).
- Clearance-level normalization across NATO/US/UK/EU — needs an authority table.
- Whether `conflicts` covers non-state actors or state-only.
