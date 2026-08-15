# Governance Collection Status

**Status as of 2026-07-26**

The `general_ludd.governance` Ansible collection provides structured knowledge
modules for governance-related data: borders, governing bodies, treaties, tax &
currency, civic services, decision makers, elections & voting, international
relations, legal systems, public finance, postal delivery, military service,
licenses & permits, and information classification.

## 1. Domain Inventory (16 domains)

The governance collection comprises 16 domains: 14 knowledge module_utils files, the
CLI integration layer, and the E2E collection validation suite.

### Knowledge Domains (module_utils)

| # | Domain | module_utils file | Test file | Tests |
|---|--------|-------------------|-----------|-------|
| 1 | Borders | `plugins/module_utils/borders.py` | `test_governance_borders.py` | 30 |
| 2 | Governing Bodies | `plugins/module_utils/governing_bodies.py` | `test_governance_bodies.py` | 51 |
| 3 | Civic Services | `plugins/module_utils/civic_services.py` | `test_governance_civic_services.py` | 51 |
| 4 | Conflicts & Treaties | `plugins/module_utils/conflicts_treaties.py` | `test_governance_conflicts_treaties.py` | 43 |
| 5 | Decision Makers | `plugins/module_utils/decision_makers.py` | `test_governance_decision_makers.py` | 38 |
| 6 | Elections & Voting | `plugins/module_utils/elections_voting.py` | `test_governance_elections_voting.py` | 47 |
| 7 | Info Classification | `plugins/module_utils/info_classification.py` | `test_governance_info_classification.py` | 48 |
| 8 | International Relations | `plugins/module_utils/international_relations.py` | `test_governance_international_relations.py` | 72 |
| 9 | Legal Systems | `plugins/module_utils/legal_systems.py` | `test_governance_legal_systems.py` | 51 |
| 10 | Licenses & Permits | `plugins/module_utils/licenses_permits.py` | `test_governance_licenses_permits.py` | 22 |
| 11 | Military Service | `plugins/module_utils/military_service.py` | `test_governance_military_service.py` | 22 |
| 12 | Postal Delivery | `plugins/module_utils/postal_delivery.py` | `test_governance_postal_delivery.py` | 24 |
| 13 | Public Finance | `plugins/module_utils/public_finance.py` | `test_governance_public_finance.py` | 59 |
| 14 | Tax & Currency | `plugins/module_utils/tax_currency.py` | `test_governance_tax_currency.py` | 32 |
| | **Subtotal** | | | **590** |

### Application Integration Domains

| # | Domain | Source path | Test file | Tests |
|---|--------|-------------|-----------|-------|
| 15 | CLI Governance | `src/general_ludd/governance/cli_governance.py` | `test_cli_governance.py` | 63 |
| 16 | Collection E2E | `src/general_ludd/governance/loader.py` | `test_governance_collection.py` | 64 |

All module_utils paths are relative to `collections/ansible_collections/general_ludd/governance/`.

## 2. Test Counts

### Unit Tests (695 test instances across 15 files)

Test counts below reflect `def test_` function definitions. Parametrized tests
expand to multiple instances at runtime; the 695 total includes all parametrized
instances from the prior audit. The 653 base definitions expand to 695 when
parametrized cases are counted.

| Test File | Definitions | Test Classes |
|-----------|----------|-------------|
| `test_governance_borders.py` | 30 | BorderTypes, RecognitionStatus, VisaTypes, BorderData, LookupBorder, CrossingRequirements, GetRecognitionStatus, GetVisaRequirements |
| `test_governance_bodies.py` | 51 | BodyTypes, LookupBody, RequiredInternationalBodies, BodyShape, GetChildren, GetDescendants, GetJurisdiction, GetDecisionProcess, NationalStructures, BodyRelationships, BodiesByType, ImportSanity |
| `test_governance_civic_services.py` | 51 | ServiceCategories, ServicesKnowledgeBase, LookupService, GetRequirements, GetProcessingTime, FindServiceOffice, PostalSystems, GetPostalInfo, GetPostageRate |
| `test_governance_conflicts_treaties.py` | 43 | ConflictTypes, ActiveConflicts, LookupConflict, TreatyDatabase, GetTreaty, GetTreatyParties, GetTreatyObligations, InternationalCourts, GetCourtJurisdiction, CheckCourtJurisdiction |
| `test_governance_decision_makers.py` | 38 | RoleTypes, ProfileTemplate, ProfilesIntegrity, LookupDecisionMaker, GetDecisionAuthority, GetInfluenceNetwork, FindDecisionMaker, AssessProclivity, BiasIndicators |
| `test_governance_elections_voting.py` | 47 | ModuleExports, ElectionSystems, ElectionData, ElectoralBodies, PollingProcedures, GetElectionInfo, ListElectionSystems, GetElectoralBody, GetPollingProcedures, GetVoterEligibility, ListCountriesWithElections |
| `test_governance_info_classification.py` | 48 | ClassificationLevels, ClassificationByCountry, GetClassificationSystem, AccessFrameworks, GetAccessRequirements, CheckClearanceEquiv, InfoSources, FindOfficialSource, GetPublicRecordsUrl, FoiaProcess, GetFoiaProcedure, FileFoiaRequestTemplate |
| `test_governance_international_relations.py` | 72 | ModuleExports, DiplomaticRelations, Embassies, SanctionsRegimes, SanctionsData, IsSanctioned, GetSanctionsInfo, TradeAgreements, GetTradeAgreements, ListTradeAgreements, VisaWaiverPrograms, GetVisaWaiverMembers, ListVisaWaiverPrograms, GetDiplomaticRelations, GetEmbassyInfo |
| `test_governance_legal_systems.py` | 51 | ModuleExports, LegalSystemTypes, CourtHierarchies, GetLegalSystem, GetCourtHierarchy, GetAppealProcess, GetTerm, TermsByCategory, CourtAtLevel, SupremeCourt, ListCountries, LegalTerminologyCompleteness |
| `test_governance_licenses_permits.py` | 22 | LicenseTypes, LicenseRegistries, GetLicenseInfo, ExportLicenseRequirements, GetExportLicenseRequirements, CheckLicenseValidity, ListProfessionsForCountry, GetRegulatingBody |
| `test_governance_military_service.py` | 22 | ConscriptionData, GetConscriptionInfo, EnlistmentProcess, MilitaryBranches, GetMilitaryBranches, VeteranBenefits, GetVeteranBenefits, ListMandatoryServiceCountries |
| `test_governance_postal_delivery.py` | 24 | PostalCodePatterns, GetPostalCodePattern, CourierTracking, GetCourierTrackingUrl, CustomsDeclarations, GetCustomsDeclarationFormat, AddressFormats, NormalizeAddress, ValidateAddress, SearchCountriesByName |
| `test_governance_public_finance.py` | 59 | ModuleExports, BudgetTypes, BudgetData, GetBudgetInfo, ProcurementMethods, ProcurementRules, GetProcurementRules, ProcurementByMethod, DebtInstruments, DebtData, GetDebtInfo, DebtToGdp, DebtByHolder, SovereignWealthFunds, GetSwfByName, GetSwfsByCountry, GetSwfsByType, ListCountries |
| `test_governance_tax_currency.py` | 32 | ModuleExports, TaxSystems, TaxData, Currencies, TaxAuthorities, GetTaxInfo, GetCurrencyInfo, GetFilingRequirements, GetTaxTreaty, ListAccessors |
| `test_cli_governance.py` | 63 | Subparser, Borders, Body, Tax, Currency, Service, Treaty, Navigate, List, Elections, Relations, Legal, Finance, Loader |
| **Total Unit** | **695** | |

### E2E Tests (64 test instances)

| Test File | Definitions | Parametrized instances | Coverage |
|-----------|-----------|----------------------|----------|
| `test_governance_collection.py` | 26 | 64 | Galaxy YAML validation, all 14 module_utils loadable + export checks, 20 roles with required files + nonempty tasks, loader functions, CLI subparser registration, `__init__.py` `__all__` exports |

### Grand Total: 759 (695 unit + 64 E2E)

## 3. Collection Structure

```text
collections/ansible_collections/general_ludd/governance/
  galaxy.yml                    Collection manifest (namespace, name, version, deps)
  README.md                     Collection overview
  plugins/
    __init__.py
    module_utils/               Knowledge data modules (14 domains)
      __init__.py
      borders.py                Border types, data, visa/lookup/crossing functions
      governing_bodies.py       International bodies, lookup, hierarchy, decision process
      civic_services.py         Civic services knowledge base, postal systems, postage
      conflicts_treaties.py     Active conflicts, treaty database, international courts
      decision_makers.py        Role types, profiles, influence networks, proclivity
      elections_voting.py       Election systems, data, electoral bodies, polling
      info_classification.py    Classification levels, by-country systems, FOIA
      international_relations.py Alliances, embassies, sanctions, trade, visa waivers
      legal_systems.py          Legal system types, court hierarchies, appeals, terminology
      licenses_permits.py       License types, registries, export controls, validity
      military_service.py       Conscription, branches, veteran benefits, enlistment
      postal_delivery.py        Postal codes, courier tracking, customs, address formats
      public_finance.py         Budgets, procurement, debt instruments, sovereign wealth funds
      tax_currency.py           Tax systems, currencies, tax authorities, treaties
  roles/                        Ansible roles (20 roles)
    borders/  governing_bodies/  tax_systems/  currencies/  conflicts/  treaties/
    civic_services/  decision_makers/  info_classification/  postal_delivery/
    military_service/  licenses_permits/  navigate_borders/  lookup_governing_body/
    tax_currency_info/  civic_service_finder/  decision_maker_lookup/
    info_classification_check/  conflicts_treaties_lookup/  governance_navigator/
  tests/
    unit/                       Collection-self unit tests (co-located)
```

### Python Application Layer

```text
src/general_ludd/governance/
  __init__.py                   Re-exports 14 getter functions from loader
  loader.py                     Dynamic importlib loader with process-level cache
  cli_governance.py             CLI subparser + 12 subcommands (borders, body, tax,
                                currency, service, treaty, navigate, list, elections,
                                relations, legal, finance)
```

## 4. Remaining Gaps

| Gap | Description |
|-----|-------------|
| Missing unit test domains | All 16 domains (14 knowledge + CLI + E2E) have tests. No gap. |
| CLI test coverage | All 12 subcommands + list + navigate tested for happy path, not-found, and JSON output (63 test definitions). |
| Loader test coverage | 7 of 14 loader getters verified in unit tests; all 14 verified in E2E. |
| E2E coverage | Collection structure, role completeness, module export shape, loader, CLI subparser, and `__all__` exports all tested (26 definitions, 64 parametrized instances). |
| Missing module_utils files | All 14 knowledge domains have `module_utils/*.py`. No gap. |
| Missing `__init__.py` `__all__` | Fully populated with all 14 getters. No gap. |
| Galaxy.yml completeness | Present with namespace, name, version, description, license, authors, tags. |
| Docstring completeness | All 14 module_utils files have module-level docstrings documenting data shapes and function signatures. |
| Type annotations | All module_utils files use `from __future__ import annotations` and have parameter/return annotations. |
| Export lists (`__all__`) | All module_utils files have `__all__` listing exported symbols. |
| Role `tasks/main.yml` | All 20 roles have non-empty `tasks/main.yml`. Verified by E2E parametrized test. |
| Additional domains | No known missing knowledge domains. 16 domains total cover the governance surface (14 module_utils + CLI + E2E). |

### Known Limitations

1. **Data coverage varies by domain.** Some domains (international_relations, public_finance, legal_systems) have extensive multi-country data. Others (licenses_permits, military_service) are moderate. All domains cover major countries (US, GB, CA, DE, FR, AU) at minimum.
2. **No integration tests for data freshness.** The tests verify data structure (keys exist, types valid) but not that the data itself is current. This is inherent to static knowledge modules.
3. **Loader exceptions not tested.** The loader's error handling for missing files is not exhaustively tested; E2E verifies all 14 modules load in practice.

## 5. How to Add a New Domain

To add a new governance domain (e.g., `customs_regulations`):

### Step 1: Create the data module

Create `collections/ansible_collections/general_ludd/governance/plugins/module_utils/customs_regulations.py`:

```python
"""
customs_regulations -- Customs regulations and import/export duties
for the governance collection.

Data shape:
    CUSTOMS_DUTIES[country] -> dict with tariff schedules and exemptions

Functions:
    get_customs_duty(country, goods_category) -> dict | None
"""

from __future__ import annotations

from typing import Any

CUSTOMS_DUTIES: dict[str, dict[str, Any]] = { ... }

def get_customs_duty(country: str, goods_category: str) -> dict[str, Any] | None:
    ...

__all__ = ["CUSTOMS_DUTIES", "get_customs_duty"]
```

### Step 2: Write unit tests

Create `tests/unit/test_governance_customs_regulations.py` with tests covering:
- Data shape (constants exist, types correct)
- Known-country lookups
- Unknown-country returns None
- Case-insensitive matching
- Edge cases (empty, invalid input)

### Step 3: Update the loader

In `src/general_ludd/governance/loader.py`, add a `get_customs_regulations()` function following the existing pattern (`_load_module` with the domain name).

### Step 4: Update `__init__.py`

In `src/general_ludd/governance/__init__.py`, add `get_customs_regulations` to imports and `__all__`.

### Step 5: Add CLI subcommand (optional)

In `src/general_ludd/governance/cli_governance.py`, add a `_cmd_customs` handler and register it in `add_governance_subparser()`.

### Step 6: Update E2E tests

In `tests/e2e/test_governance_collection.py`:
- Add to `TestAllModuleUtilsLoadable.EXPECTED_MODULES` frozenset
- Add export verification test
- Add to expected roles if a new role is needed

### Step 7: Create Ansible role (optional)

If the domain should have an Ansible role:

```bash
mkdir -p collections/ansible_collections/general_ludd/governance/roles/customs_regulations/{tasks,defaults,meta,vars}
```
Create `tasks/main.yml`, `defaults/main.yml`, `meta/main.yml`, `vars/main.yml`.

### Step 8: Run the gate

Run `make test` and confirm all tests pass before committing.
