# Feature: End-to-End Test Generation Agent

**Status: IMPLEMENTED** | **Created: 2026-07-14** | **Implemented: 2026-07-16** | **Target: v0.1.0-beta.2**

## 1. Overview

A gludd subagent (`e2e-test-gen`) that analyzes source modules, generates
realistic E2E tests exercising public interfaces (API, CLI, TUI), and verifies
code-path coverage via coverage.py. Scenarios are grounded in real-world usage
patterns sourced from blogs, issue trackers, and forums.

## 2. Architecture — Ansible Collection

Implemented as an Ansible collection (`general_ludd.e2e_test_gen`) at
`collections/ansible_collections/general_ludd/e2e_test_gen/`. Five-role pipeline:

```text
analyze_code_paths → generate_scenarios → validate_scenarios → write_e2e_tests → verify_coverage
```

### 2.1 Role: `analyze_code_paths`

Wraps `CodePathAnalyzer` (tree-sitter AST walk) to extract public functions,
classes, and methods from a Python source module. Calls daemon `/admin/code/graph`
and `/admin/code/search` endpoints (powered by core `code_intelligence`:
`ASTBlockExtractor`, `CodeSearch`, `CallGraph`) for call-graph cross-referencing.

**Produces:** `module_symbols.json` — `{functions: [...], classes: [...], call_graph: {...}}`

### 2.2 Role: `generate_scenarios`

Maps code-path symbols to realistic E2E test scenarios from the scenario catalog
(CRUD lifecycle, auth flow, timeout handling, concurrent edits, daemon restart).
Uses keyword heuristics (`PATTERN_KEYWORDS`) to match public API names to
scenario patterns. Emits `GeneratedScenario` records with step sequences and
coverage targets.

**Produces:** `scenarios.json` — `{scenarios: [{name, steps: [...], coverage_targets: [...]}]}`

### 2.3 Role: `validate_scenarios`

Cross-references generated scenarios against real-world usage via
`ResearcherAgent` through the daemon `/admin/research` API endpoint. Searches
GitHub issues, Stack Overflow, blogs. Computes confidence scores per scenario
from source corroboration. Prunes implausible scenarios below
`confidence_threshold` (default 0.4).

**Produces:** `validated_scenarios.json` — `{valid: [...], discarded: [...], research_queries: [...]}`

### 2.4 Role: `write_e2e_tests`

Generates pytest test files from validated scenarios. Emits tests using project
fixtures (`TestClient`, `_run_cli`, `tmp_path`) with AAA structure. Each
scenario becomes a test file; each step within a scenario maps to test assertions.

**Produces:** `test_e2e_generated_*.py` files in `output_dir`, `generated_tests.json` manifest

### 2.5 Role: `verify_coverage`

Runs `pytest --cov` on generated test files against the target source module.
Checks coverage against `coverage_threshold` (default 85%). Verifies expected
code paths are hit. Produces structured coverage report.

**Produces:** `coverage_report.json` — `{verdict, coverage_percent, symbol_level: {...}}`

## 3. Leveraging Core Facilities

The roles delegate to existing gludd subsystems rather than re-implementing:

| Role | Core Facility Used | How |
|------|-------------------|-----|
| `analyze_code_paths` | `code_intelligence` (`ASTBlockExtractor`, `CodeSearch`, `CallGraph`) | Daemon `/admin/code/graph` + `/admin/code/search` endpoints |
| `generate_scenarios` | `test_generation.scenario_generator` | Python module invoked directly |
| `validate_scenarios` | `ResearcherAgent` (SearXNG) | Daemon `/admin/research` POST endpoint |
| `write_e2e_tests` | `test_generation` Python modules | Emits pytest files with project fixtures |
| `verify_coverage` | `pytest-cov` | Standard pytest invocation |

## 4. Collection Structure

```text
collections/ansible_collections/general_ludd/e2e_test_gen/
├── galaxy.yml                              # version 0.1.0
├── README.md
└── roles/
    ├── analyze_code_paths/
    │   ├── tasks/main.yml
    │   ├── defaults/main.yml
    │   ├── meta/main.yml
    │   ├── vars/main.yml
    │   └── README.md
    ├── generate_scenarios/
    │   ├── tasks/main.yml
    │   ├── defaults/main.yml
    │   ├── meta/main.yml
    │   ├── vars/main.yml
    │   └── README.md
    ├── validate_scenarios/
    │   ├── tasks/main.yml
    │   ├── defaults/main.yml
    │   ├── meta/main.yml
    │   ├── vars/main.yml
    │   └── README.md
    ├── write_e2e_tests/
    │   ├── tasks/main.yml
    │   ├── defaults/main.yml
    │   ├── meta/main.yml
    │   ├── vars/main.yml
    │   └── README.md
    └── verify_coverage/
        ├── tasks/main.yml
        ├── defaults/main.yml
        ├── meta/main.yml
        ├── vars/main.yml
        └── README.md
```

## 5. Knowledge Modules (existing)

| Module | Location | Content |
|--------|----------|---------|
| `test_scenarios.py` | `src/general_ludd/agents/test_generation/knowledge/` | Catalog of common E2E patterns (CRUD, auth, timeout, concurrent, daemon) |
| `code_path_analyzer.py` | `src/general_ludd/agents/test_generation/` | Tree-sitter AST walk producing `ModuleSymbols` |
| `scenario_generator.py` | `src/general_ludd/agents/test_generation/` | Maps symbols→scenarios via keyword matching |

## 6. Implementation Plan

| Phase | Scope | Deliverable | Status |
|-------|-------|-------------|--------|
| P1 | Collection scaffolding + `analyze_code_paths` role | galaxy.yml, README, 5 roles with tasks/defaults/meta/vars/README | COMPLETE |
| P2 | `generate_scenarios` + `validate_scenarios` roles wired to Python modules | End-to-end pipeline test with sample module | COMPLETE |
| P3 | `write_e2e_tests` + `verify_coverage` roles | Generated test run + coverage report | COMPLETE |
| P4 | Self-test: agent generates passing test for its own code_path_analyzer.py | >=85% coverage proven | COMPLETE |

**Evidence:** All 5 roles have tasks/defaults/meta/vars/README.yml in `collections/ansible_collections/general_ludd/e2e_test_gen/roles/`. Pipeline roles wired to existing Python modules (`test_scenarios.py`, `code_path_analyzer.py`, `scenario_generator.py`) in `src/general_ludd/agents/test_generation/`.

## 7. Dependencies

No new dependencies. Existing: `tree-sitter`, `tree-sitter-python`, `pytest-cov`,
`httpx` (all in pyproject.toml dev deps). Web research via existing ResearcherAgent.
Code intelligence via existing `ASTBlockExtractor`/`CodeSearch`/`CallGraph`.
