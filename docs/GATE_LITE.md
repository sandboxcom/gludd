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
