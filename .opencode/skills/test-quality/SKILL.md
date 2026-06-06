# Test Quality Skill

When writing tests, follow these rules. This is NOT advisory.

## CRITICAL: Test Quality Policy

### 1. Tests Must Prove Behavior

Every test must make at least one assertion about observable behavior.
Tests that only check existence (file exists, import works) are
insufficient unless they are explicitly plumbing/smoke tests.

GOOD: `assert router.route() returns decision with correct model_id`
BAD: `assert router is not None`

### 2. Three-Act Structure

Every test must follow Arrange-Act-Assert:
- **Arrange**: Set up the specific state needed for this test case
- **Act**: Execute ONE operation or call ONE method
- **Assert**: Verify the observable outcome matches expectations

Do not skip Arrange. Do not combine multiple Acts in one test.

### 3. Coverage Layers

Tests must exist at all three levels for user-facing features:

| Level | Directory | Tests What | Example |
|-------|-----------|------------|---------|
| Unit | `tests/unit/` | Individual functions/classes in isolation | `test_parse_agents_md_extracts_title()` |
| Integration | `tests/integration/` | 2+ subsystems together | `test_worktree_monitor_creates_todo_from_real_agents_md()` |
| E2E | `tests/e2e/` | Through daemon API as a user would | `test_e2e_worktree_monitor_end_to_end()` |

Unit tests must cover >85% of statements per file.
Integration tests must cover the happy path AND at least one failure path.
E2E tests must test through the actual user-facing interface (CLI or HTTP API).

### 4. Edge Cases Required

For every function, at minimum test:
- Happy path (normal input, expected output)
- Empty input (None, "", [], {})
- Invalid input (wrong type, out of range)
- Error path (exception raised, fallback triggered)
- Boundary (max/min values, edge of range)

### 5. No Mock-Only Tests

Tests that ONLY test mocks are worthless. A test must verify
that the SYSTEM produces the right output, not that a mock
was called with certain arguments.

GOOD: `assert result.content == "expected output"` (tests system behavior)
ACCEPTABLE: `mock_post.assert_called_once_with(...)` (only as secondary assertion)
BAD: Only checking mock calls with no behavior assertion

### 6. Realistic Data

Use realistic test data, not placeholders:
- Use actual file paths that match the production structure
- Use realistic JSON payloads
- Use valid git worktree paths for worktree tests
- Use real markdown content for AGENTS.md parsing tests

### 7. Test Names Must Describe Behavior

Test names must use the pattern: `test_<subject>_<condition>_<expected_result>`

GOOD: `test_monitor_detects_abandoned_worktree_when_no_commits`
GOOD: `test_monitor_ignores_active_worktree_with_recent_commits`
BAD: `test_monitor_1`
BAD: `test_monitor_works`

### 8. One Assertion Concept Per Test

Each test should verify one logical concept. If you need
multiple assertions for the same concept (e.g., checking
multiple fields on a result), that's fine. But don't mix
"it returns the right data" with "it logs the right message"
in the same test.

### 9. TDD Is Mandatory

You MUST write a failing test BEFORE writing implementation code.
No exceptions.

Workflow:
1. Identify the behavior you need.
2. Write a test that fails because the behavior does not exist yet.
3. Run the test — confirm it FAILS.
4. Write the minimal implementation to make it pass.
5. Run the test — confirm it PASSES.
6. Refactor if needed, keeping tests green.

Do not skip steps. Do not write implementation and then retroactively
add tests. This is enforced by the TDD guardrail in enforce-make.ts.

### 10. Coverage Not Gaming

Coverage metrics serve the tests, not the other way around.
Do not write tests just to hit uncovered lines. Write tests
that verify behavior. If a line is uncovered, ask: "Is this
behavior testable? If not, why does it exist?"

Lines that should exist but are hard to test:
- Add a test that exercises the behavior indirectly
- Document why direct testing is impractical

Lines that should not exist:
- Remove dead code instead of testing it
