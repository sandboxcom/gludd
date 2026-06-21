# gate_concurrency_pretool.sh — narrowing spec (2026-06-18)

## Problem

The original blocked-target list included `test-count`, `test-unit` (even with `TESTFILE=`),
and `collect-check`.  These are lock-free operations that never touch the shared pytest
basetemp, so blocking them when a gate is running is unnecessarily restrictive and breaks
the development workflow (e.g. an agent doing a quick collection check while the gate runs).

## Narrowing rules

### BLOCKED (invoke the full pytest suite or full gate pipeline)

| Target | Reason |
|--------|--------|
| `gate` | Full gate pipeline — lint + typecheck + full suite |
| `test` (bare) | Full test suite with xdist workers |
| `test-e2e` | End-to-end tests, shares basetemp |
| `test-and-commit` | Runs full suite before committing |
| `qa` | lint + typecheck + test + healthcheck |
| `validate` | Full validation pipeline including full suite |
| `test-unit` (bare, no `TESTFILE=`) | Runs all unit tests via xdist |

### EXEMPTED (lock-free; safe to run concurrently with an in-flight gate)

| Target | Reason |
|--------|--------|
| `test-count` | Collection only (`--co -q`), no test execution, no basetemp writes |
| `collect-check` | Same as test-count — collection probe only |
| `test-unit TESTFILE=...` | Single-file run with unique basetemp; isolation guaranteed |
| `test-iso` | Always uses unique basetemp by design |
| `test-hooks` | Pure bash, never launches pytest |
| `test-stop-hooks` | Pure bash, never launches pytest |

## Detection logic

Exemptions are checked **before** the blocked list to avoid false positives:

1. If `cmd` matches `make test-unit TESTFILE=<non-empty>` → **exempt**.
2. If `cmd` matches `make (test-count|collect-check|test-iso|test-hooks|test-stop-hooks)` → **exempt**.
3. If `cmd` matches `make (gate|test|test-unit|test-e2e|test-and-commit|qa|validate)` → **blocked**.

## Implementation file

`.claude/hooks/gate_concurrency_pretool.sh` — the `is_gate` python3 snippet.
