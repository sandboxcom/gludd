# Gate-concurrency hook narrowing (apply to main-tree hook)

Part of `fix/gate-lock-decoupling`. The Makefile + test changes in this branch make
`test-count`, `collect-check`, and single-file `test-unit TESTFILE=...` lock-free
(their own `mktemp -d /tmp/gludd-unit-XXXXXX` basetemp; collection-only `--co` for
the count/collect targets). The **harness hook** must be narrowed to match, or it
will keep head-of-line-blocking parallel builders on a sibling's full gate.

This file is checked in because the worktree sandbox cannot edit the shared-checkout
hook directly. The orchestrator applies it to the **main tree**:
`/Users/shawnwilson/gludd/.claude/hooks/gate_concurrency_pretool.sh`.

## The problem

The hook's Step-1 matcher (the live version, 2026-06-18) is:

```python
pattern = r"^make\s+(gate|test|test-unit|test-e2e|test-count|test-and-commit|qa)(\s|$)"
print("yes" if re.match(pattern, cmd) else "no")
```

It matches `make test-count` and `make test-unit TESTFILE=...`. Step-2 then blocks
via `pgrep -f "pytest"` — which fires whenever ANY sibling worktree is running a full
`pytest tests/` gate. Result: every lightweight builder command is denied while one
gate runs, serializing all parallel builders and stranding their commits.

## The fix — replace the Step-1 matcher block with

```python
# Full-suite targets that take the global gate lock / large basetemp.
full_suite = r"^make\s+(gate|test|test-e2e|test-and-commit|qa|validate)(\s|$)"
is_full = bool(re.match(full_suite, cmd))
# `make test-unit` WITHOUT TESTFILE= runs the whole tests/unit/ dir on xdist
# (shared-basetemp territory) — still block it. WITH TESTFILE= it is lock-free
# (own /tmp/gludd-unit-XXXXXX basetemp) and must NOT be blocked.
if re.match(r"^make\s+test-unit(\s|$)", cmd) and not re.search(r"\bTESTFILE\s*=", cmd):
    is_full = True
print("yes" if is_full else "no")
```

### What this changes vs. the current pattern

- REMOVED from the always-block list: `test-count` (collection-only `--co`) and the
  generic `test-unit` (now conditionally handled).
- `test-unit` is blocked **only** when no `TESTFILE=` is present (bare `make test-unit`
  runs all of `tests/unit/` on xdist with a shared basetemp — still needs serializing).
- `test-unit TESTFILE=...` is now EXEMPT: it has its own `/tmp/gludd-unit-XXXXXX`
  basetemp and never touches `/tmp/gludd-gate.lock`, so concurrent single-file runs
  are collision-free.
- Still blocked (unchanged): `make gate`, bare `make test`, `make test-e2e`,
  `make test-and-commit`, `make qa`, and (added) `make validate` (also runs the full
  suite). `collect-check` was never in the list and stays exempt.

## Also kill the `wait-pytest-clear` PreToolUse wrapper for lock-free targets

Observed live (`make ps-pytest`): the harness wraps make calls with
`make wait-pytest-clear`, a `pgrep`-loop that sleeps until NO pytest is running.
That wrapper must apply the SAME exemption — it should not gate `make test-count`,
`make collect-check`, or `make test-unit TESTFILE=...`. Otherwise these lock-free
commands still serialize behind a sibling gate even though the deny-hook now lets
them through. Apply the same `full_suite` / `TESTFILE=` predicate before invoking
`wait-pytest-clear`.
