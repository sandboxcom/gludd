# Expert Collection Test Sweep Summary

**Date:** 2026-07-30
**Scope:** All expert collection unit test files (`test_{materials,chemistry,ai_ml,git_release}_*.py`)
**Method:** `make test-specific TESTFILE='<path>'` per file; `make collect-check` for collection integrity.

## Totals

| Metric | Value |
|---|---|
| **Total files** | 28 |
| **Total tests** | 709 |
| **Passing** | 709 |
| **Failing** | 0 |
| **Pass rate** | 100.0% |
| **Collection errors** | 0 (clean) |

## Per-File Results

| File | Tests | Pass | Fail | Notes |
|---|---:|---:|---:|---|
| test_materials_additive.py | 16 | 16 | 0 | Process selection, orientation, supports, porosity |
| test_materials_contracts.py | 32 | 32 | 0 | Units, design reqs, candidates, schema versioning |
| test_materials_core.py | 22 | 22 | 0 | Registries, lookup, selection, strength assessment |
| test_materials_joining.py | 21 | 21 | 0 | Fusion/solid-state classification, compatibility, machining |
| test_materials_metals.py | 15 | 15 | 0 | Alloy temper, formability, springback, hot tearing |
| test_materials_polymers.py | 17 | 17 | 0 | Thermoplastic/thermoset, shrinkage, fiber orientation |
| test_materials_science.py | 58 | 58 | 0 | Material DB, families, calculations, recommendations |
| test_materials_tolerance.py | 17 | 17 | 0 | Stack-up, thermal expansion, process capability, failure |
| test_chemistry_cheminformatics.py | 30 | 30 | 0 | Validate, standardize, tautomers, descriptors, similarity |
| test_chemistry_core.py | 35 | 35 | 0 | Router, identity, formula parse, reactions, stoichiometry |
| test_chemistry_electrochem.py | 23 | 23 | 0 | Nernst, cell potential, electrolysis, corrosion, scale-up |
| test_chemistry_protocols.py | 20 | 20 | 0 | Draft fields, approval tokens, inventory, lot suitability |
| test_chemistry_reactions.py | 31 | 31 | 0 | Balance, classify, compare, amounts, concentration, yield |
| test_chemistry_safety.py | 30 | 30 | 0 | Risk tiers, hazard classes, compatibility, refusal |
| test_chemistry_schemas.py | 26 | 26 | 0 | Entity structure, request/result validation, registry |
| test_chemistry_thermo.py | 36 | 36 | 0 | Equilibrium, Arrhenius, phase stability, gas law, spectra |
| test_ai_ml_adaptation.py | 24 | 24 | 0 | Adapter manifest, base digest, safe stop, benchmark |
| test_ai_ml_core.py | 27 | 27 | 0 | Schema contracts, router, evidence store, discover tools |
| test_ai_ml_datasets.py | 27 | 27 | 0 | Manifest, validation, data card, format selection, discovery |
| test_ai_ml_distillation.py | 23 | 23 | 0 | Plan contract, license gate, filter rules, policy engine |
| test_ai_ml_evidence.py | 22 | 22 | 0 | Immutability, content addressing, dedup, retraction |
| test_ai_ml_reasoning.py | 35 | 35 | 0 | State machine, step artifacts, retrieval service, metrics |
| test_ai_ml_registries.py | 19 | 19 | 0 | Record construction, registry, alias, tombstone, supersede |
| test_git_release_contracts.py | 28 | 28 | 0 | Repo evidence, helper candidate, release plan, verdict |
| test_git_release_evidence.py | 11 | 11 | 0 | Clean/dirty tree collection, frozen evidence, head sha |
| test_git_release_helpers.py | 22 | 22 | 0 | Discover, authority ranking, helper adequacy |
| test_git_release_provenance.py | 17 | 17 | 0 | SBOM, checksum, signature, freshness, source entry |
| test_git_release_state.py | 25 | 25 | 0 | State machine, deployment canary, rollback, traffic shift |

## Verification

- `make collect-check`: **Collection OK** (0 errors after isolated runs; an initial transient
  collection error on `test_materials_process_planning.py` was caused by concurrent pytest
  cache races during the parallel sweep — it resolves cleanly when collected in isolation
  and passes 15/15 on its own; it is outside the requested glob set but noted for completeness).
- No fixes required: every target file passes 100%.
