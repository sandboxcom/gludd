# Root-Cause Analysis: Why the `# noqa` / `# type: ignore` Guardrail Failed to Prevent Re-introduction

**Date:** 2026-07-06
**Trigger:** User reported that previously-removed `# noqa` and `# type: ignore`
comments kept getting re-introduced into `src/` despite a codified guardrail
existing for exactly this purpose.

---

## Summary

The guardrail test `tests/unit/test_type_safety_guardrails.py` is **not** advisory —
it uses hard `assert` statements (not `warnings.warn`), it **is** collected by the
gate, and it **is currently failing** (40 `# type: ignore` violations in `src/` as
of this writing). The guardrail *detects* the regressions correctly. What it does
not do is *prevent* them: there is **no edit-time hook** that blocks the Write/Edit
the instant a suppression comment is typed, and the red gate it produces has been
**bypassed at commit time** (via `commit-no-verify` / `repo-commit` / skipping the
gate). The failure is architectural — a post-hoc detection layer with no
preventive layer in front of it — which is the same incident class as BUGS.md
#21+ ("guardrails that detect but do not block"), applied at a different layer.

> **Correction to the original investigation brief.** The brief hypothesized the
> test used `warnings.warn()` instead of `assert`, which would make it a no-op
> under default pytest config. That hypothesis is **factually wrong**. A grep of
> the file confirms 6 `assert` statements and **zero** `warnings.warn` calls. The
> real root cause is more interesting and is documented below.

---

## The bug (corrected): the test is assert-based and *is* failing

`tests/unit/test_type_safety_guardrails.py` uses `assert not violations, ...` in
every check. Example (lines 28–39):

```python
def test_no_type_ignore_comments():
    """Test that there are no # type: ignore comments in source files."""
    violations = []
    ignore_pattern = re.compile(r"#\s*type:\s*ignore")
    for py_file in get_python_files():
        content = py_file.read_text()
        for i, line in enumerate(content.splitlines(), 1):
            if ignore_pattern.search(line):
                violations.append(f"{py_file}:{i}: {line.strip()}")
    assert not violations, (
        f"Found {len(violations)} # type: ignore comments:\n" + "\n".join(violations)
    )
```

Running it right now produces a hard failure with 40 violations
(`make test-specific TESTFILE='tests/unit/test_type_safety_guardrails.py::test_no_type_ignore_comments'`):

```text
FAILED tests/unit/test_type_safety_guardrails.py::test_no_type_ignore_comments
AssertionError: Found 40 # type: ignore comments:
  src/general_ludd/connectors/ingest_formats.py:182: import msgpack  # type: ignore[import-untyped] ...
  src/general_ludd/secrets/payment_vault.py:27: hashes = None  # type: ignore[assignment]
  ... [38 more]
```

So the guardrail is **not** silently passing. It is **loudly failing**, every time
the gate runs, and has been for long enough to accumulate 40 violations.

---

## Why it persisted: detection without prevention + gate bypass

Three compounding gaps, each necessary for the failure:

### 1. No edit-time `tool.execute.before` hook

`.opencode/plugin/**/*.ts` is **empty** — there are no opencode plugins at all
(glob of `.opencode/**/*.ts` returns zero files). There is therefore no hook that
intercepts an `Edit` or `Write` tool call and denies it when the would-be content
matches `# noqa` / `# type: ignore`. The agent is free to *type* a suppression;
nothing pushes back until the gate runs (which may be much later, or never, see #2).

This is the structural hole. A test can only fail *after* the offending line is on
disk. By that point the agent has already moved on, the commit message is half-
written, and the cheapest path is to bypass the gate rather than refactor the
import.

### 2. The red gate is bypassable at commit time

The gate IS red right now (`.gate-status` reads `=== GATE: FAILED ===`), driven in
part by this very test. Yet suppressions keep landing in commits (e.g.
`52b13ee7`, `baaf7366` introduced/moved files containing `# type: ignore`). This
is the exact pattern documented in BUGS.md incident 2026-06-22 ("Agent committed
with red gate via commit-no-verify bypass"):

> The `commit-no-verify` make target existed for a legitimate purpose … but it
> ALSO bypassed the `.gate-status` freshness+green check that `git-commit`
> enforces. … An agent that knows the gate is red can simply reach for a sibling
> target.

Even after that incident hardened `commit-no-verify` and `commit-bootstrap`,
`repo-commit` remains allowlisted for "non-code meta-commits," and an agent that
simply *doesn't run* `make gate` before committing faces no gate at all. The test
cannot enforce anything the agent never invokes.

### 3. `filterwarnings = error` is not set — but this is a red herring

`pyproject.toml` `[tool.pytest.ini_options]` (lines 180–195) does **not** set
`filterwarnings = error`. The original brief flagged this as a likely cause. It is
irrelevant here: because the test uses `assert`, the warning filter never comes
into play. This is noted only to close out the brief's hypothesis #4 — it is not a
contributing factor to this incident.

---

## The pattern: matches the BUGS.md "advisory guardrails" incident class

This incident is the same architectural failure documented repeatedly in BUGS.md,
most directly in the 2026-06-30 entry (#21+ in the stop-pattern series):

> **Root cause (structural):** … Every prior fix targeted the PATTERN LIST
> (adding more stop-signal words to the detector), not the ENFORCEMENT THRESHOLD
> (making the hook block instead of warn). … The structural fix is to MAKE THE
> HOOK BLOCK, not to keep growing the pattern list.

and the 2026-06-18 "Fix interpreted as disable" entry:

> Guardrails are passive (warn, prepend) not active (block, throw). No layer
> checks whether work remains before allowing a commit.

The shared shape: **a layer that detects the violation but cannot block the action
that produces it.** For the stop-hook series the detection was in
`chat.response.transform` (advisory) and the fix was moving to `session.idle` /
`text.complete` blocking surfaces. For *this* incident the detection is a pytest
assertion (which *does* fail) but the *commit* is the action that needs blocking —
and there is no preventive layer between "write the line" and "commit the line."

| Incident | Detects at | Blocks at | Gap |
|---|---|---|---|
| BUGS.md stop-hook series (#21+) | response text | nothing (advisory) | no block surface |
| BUGS.md 2026-06-22 red-gate bypass | gate `.gate-status` | `git-commit` only | sibling commit targets bypass |
| **This incident** | **pytest assert (gate time)** | **nothing at edit time; gate bypassable at commit** | **no edit-time hook + bypassable gate** |

---

## The fix (three layers, per the Guardrail Integrity Policy)

A single-layer fix will relapse. The repair must be applied at all three layers:

### Layer 1 — Runtime hook (NEW: `.opencode/plugin/enforce-no-suppressions.ts`)

Register a `tool.execute.before` matcher on `edit` and `write`. If the would-be
content matches `# noqa`, `# type: ignore`, `# pylint: disable=`, `# fmt: off` /
`# fmt: skip`, or `# isort:skip`, **deny** the tool call with a clean
`{"permissionDecision": "deny", "message": "Lint-suppression comments forbidden.
Fix the underlying issue. See AGENTS.md Guardrail Integrity Policy."}` and exit 0
(clean deny, never a hook error; fail-open on any exception). This is the
**preventive** layer the current setup is missing — it stops the line from being
written in the first place.

Allowlist (string-literal *data*, not suppression comments):
`src/general_ludd/security/fix_not_disable.py` (the patterns appear inside a
`DISABLE_PATTERNS` frozenset) and `tests/unit/test_type_safety_guardrails.py`
(the patterns appear as regex fixtures). Both are the policy's own enforcement
code; adding any other path is a guardrail-integrity violation.

### Layer 2 — Behavior pin (the existing assert-based test, kept)

`tests/unit/test_type_safety_guardrails.py` stays as-is — it is the **defense in
depth** that catches any suppression that slips past the hook (e.g. committed via
a path the hook doesn't see, or introduced by a non-opencode editor). Add a
companion `tests/unit/test_no_suppression_comments_plugin.py` that extracts the
plugin's exported `SUPPRESSION_PATTERNS` and `ALLOWLIST_PATHS` and asserts each
spec case (deny on `# noqa`, deny on `# type: ignore`, allow on plain `# comment`,
allow on the two allowlisted files).

### Layer 3 — Agent prompt (AGENTS.md "No Lint-Suppression Comments" section)

Already specified in AGENTS.md under the Guardrail Integrity Policy. The section
must enumerate the five forbidden patterns, name the two allowlisted files, and
state the approved alternatives (reflow / annotate / delete / `cast(...)`).

### Why not just delete the 40 existing violations?

That is separate work (a one-time remediation sweep), not the guardrail fix. The
guardrail fix is what *prevents the 41st*. Doing the sweep without fixing the
guardrail guarantees the count climbs back to 40 within a few sessions — which is
exactly what has already happened.

---

## Verification: how to confirm the guardrail now actually blocks

1. **Edit-time block (Layer 1).** From an opencode session, attempt an `edit`
   that introduces `# type: ignore` into any non-allowlisted `src/` file. Expect
   the tool call to be **denied** with the suppression-forbidden message. The
   line must never reach disk.

2. **Behavior pin (Layer 2).** `make test-specific
   TESTFILE='tests/unit/test_no_suppression_comments_plugin.py'` must pass, and
   the existing
   `make test-specific TESTFILE='tests/unit/test_type_safety_guardrails.py'` must
   fail (it is the backlog of pre-existing violations) — or, after the remediation
   sweep, pass with 0 violations.

3. **End-to-end (all three layers).** After the sweep, `make gate` must be green
   *and* an attempted edit introducing `# noqa` must still be denied. Both must
   hold simultaneously: the gate proves no violations shipped; the hook proves
   none can be typed.

A green gate alone is insufficient — it only proves the current tree is clean, not
that the next edit will be. The hook is what makes the guardrail *preventive*
rather than merely *detective*.

---

## Three-bullet root cause

- **The test is assert-based and failing (40 violations), not `warnings.warn`-based
  and silent** — the original hypothesis was wrong; the guardrail detects
  correctly but only at gate time, after the offending line is already on disk.
- **There is no edit-time `tool.execute.before` hook** (`.opencode/plugin/*.ts` is
  empty), so nothing blocks the Write/Edit the instant a suppression is typed —
  the sole preventive layer is missing.
- **The red gate it produces is bypassable at commit time** (`commit-no-verify` /
  `repo-commit` / simply not running the gate), which is the same "detect-but-
  don't-block" architectural class as BUGS.md incidents #21+ and the 2026-06-22
  red-gate bypass.
