---
{
  "name": "mp-tdd",
  "description": "Test-driven development with red-green-refactor loop. Vertical slices, not horizontal. Adapted from mattpocock/skills.",
  "tags": [
    "mattpocock",
    "testing",
    "tdd",
    "engineering"
  ],
  "category": "engineering"
}
---

# TDD

Test-driven development with a red-green-refactor loop.

**Anti-pattern: Horizontal Slices** — Don't write all tests first then all implementation. Use vertical slices (tracer bullets): one test, one implementation, repeat.

**Workflow**:
1. Planning — confirm interface changes, behaviors to test, design for testability
2. Tracer Bullet — one test, confirm the path works
3. Incremental Loop — RED then GREEN for each behavior, one at a time, minimal code only
4. Refactor — after all tests pass, extract duplication, deepen modules. Never refactor while RED

Tests verify behavior through public interfaces, not implementation details. Good tests are integration-style (read like specs). Bad tests are coupled to implementation.

