---
{
  "name": "medium-code-reviewer",
  "description": "Automated code quality review and simplification. Checks for simplifiable logic, single responsibility violations, duplicated patterns, performance inefficiencies, dead code, and naming issues. Fixes before presenting. Source: unicodeveloper/medium-2026.",
  "tags": [
    "code-review",
    "quality",
    "simplification",
    "refactoring",
    "medium-2026"
  ],
  "category": "quality"
}
---

# Code Reviewer

Run a structured review pass over code before presenting it to the user.

## Review Checklist

For every code change, check:

1. **Simplifiable logic** — Can nested conditionals be flattened? Can loops be
   comprehensions? Can early returns reduce indentation?
2. **Single responsibility** — Does each function do ONE thing? Functions longer
   than 30 lines likely do too much.
3. **Duplicated patterns** — Logic duplicated more than twice should be extracted
   to a utility. Repeated fetch patterns should be generalized.
4. **Performance** — Unnecessary re-renders, N+1 queries, blocking I/O in async
   context, unnecessary allocations.
5. **Dead code** — Unused imports, unreachable branches, commented-out code,
   unused variables.
6. **Naming** — Does the name communicate intent? Avoid `data`, `result`, `info`,
   `tmp`. Use domain-specific names.

## Process

1. After writing or modifying code, run this review pass
2. Fix everything found — the user sees the second draft, not the first
3. Present the reviewed code with a summary of what was simplified

## Example

Before review: separate fetch functions for users and posts with identical
error handling and response parsing.

After review: extracted `fetchResource(path)` utility with error handling,
specific functions become one-liners that delegate.

## Anti-patterns

- Flagging issues without fixing them
- Adding comments to explain bad code instead of refactoring it
- Reviewing only the changed lines without context of surrounding code

## Install reference

Anthropic simplify skill: `npx skills add anthropics/claude-code --skill simplify`
Community: `npx claude-code-templates@latest --skill development/code-reviewer`
