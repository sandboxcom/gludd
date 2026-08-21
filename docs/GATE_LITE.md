# Gate vs Gate-Lite

## Phases Compared

| Phase          | `make gate` | `make gate-lite` |
|----------------|-------------|-----------------|
| lint           | yes         | yes             |
| env-writes     | yes         | yes             |
| typecheck      | yes         | yes             |
| collect-check  | yes         | yes             |
| skills-fm      | —           | yes             |
| test           | full suite (xdist 8w) | `tests/unit` only (2w, -x) |
| smoke          | yes         | yes             |

## When to Use Each

- **`make gate`**: required before commits (gated by `_gate-fresh-check`).
  Runs the full test suite via `scripts/run_gate.sh` (xdist, 8 workers). Can
  OOM locally; use `make gate-background` to run it detached. This is the
  canonical gate — CI is the gate of record.
- **`make gate-lite`**: fast local feedback between commits. Same lint/
  typecheck/collect/smoke phases but replaces the full test suite with a
  2-worker `tests/unit` only run (fail-fast, isolated basetemp). NOT a commit
  prerequisite. Writes `.gate-lite-status`.

## Background Workflow

```text
make gate-background        # launches gate detached, writes .gate-background.pid
make gate-status-check      # probe: running/pass/fail + current phase + tail
make gate-tail              # live log follow (Ctrl-C to stop)
make gate-logs              # list all runs with mtime + PASS/FAIL/incomplete
make gate-kill              # SIGTERM → SIGKILL
make gate-lite-background   # same pattern for gate-lite; .gate-lite-background.pid
make gate-lite-status-check # probe for gate-lite
```

Both emit `=== GATE PHASE: <phase> ===` or `=== GATE-LITE PHASE: <phase> ===`
markers so progress is observable even when backgrounded.

## Environment-write diagnostics

Reviewed 2026-08-20. A long-lived
[Make practitioner thread](https://stackoverflow.com/questions/7252189/suppress-all-make-output-except-for-errors-and-warnings/9082044)
notes that redirecting Make output to `/dev/null` discards subprocess output,
while a [GitHub Actions practitioner report](https://github.com/orgs/community/discussions/120541)
shows the operational cost of workflow failures without a clear error message.
The `env-writes` phase had exactly that defect in `gate`, `gate-lite`, and
`gate-refresh`: the checker's actionable path and line diagnostics were thrown
away even though its exit status was recorded.

All three paths now use the existing streaming-command adapter. The checker
output remains live and is copied to a worktree-local `.gate-logs/*-env-writes`
file, while the adapter returns the child status unchanged so PASS/FAIL and the
gate failure marker retain their prior semantics. The checker reports at most
50 individual violations plus the exact total, bounding terminal and log growth
without suppressing the result.

This is a zero-downtime diagnostic change: it creates no service, process, or
schema migration and does not change the validation rule. Rollback restores the
three invocations and checker reporter together. Logs remain confined to the
current worktree, each checker process is owned synchronously by its gate, and
the existing gate process/time bounds remain authoritative.
