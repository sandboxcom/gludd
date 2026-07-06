# Blocking Process Fix — Cited Report

**Date:** 2026-07-06
**Scope:** Eliminate every pattern where a long-running process blocks the
orchestrator main thread (and therefore all subagent dispatch).

---

## Root cause (one paragraph)

The orchestrator main thread is the *only* non-delegatable resource. While it
is blocked inside a `bash` tool call, **no subagent can be dispatched** — the
10-agent floor collapses to zero for the full wall-clock duration of that call.
A 40-minute `make gate` is therefore not "40 minutes of slow work"; it is 40
minutes of *zero throughput*, because the entire pipeline is starved.
(`AGENTS.md` → "Main-thread command restriction (ANTI-STALL RULE)" and
"CRITICAL: Long-Running Operations MUST Be Backgrounded".)

The fix has three mechanical primitives, all of which already exist in this
repo:

| Primitive | What it does | Source |
|---|---|---|
| `nohup ... &` | Detach the child from the controlling terminal so it survives shell exit and runs concurrently with the parent. | `man nohup` (POSIX.1-2008); used by `Makefile:2079` (`gate-background`) and `Makefile:1289` (`test-bg`) |
| `setsid` | Start the child in a *new session*, fully decoupled from any tty / process group, so signals sent to the parent (Ctrl-C, SIGHUP) never reach it. | `man setsid` (util-linux); the recommended escalation when `nohup` alone leaves the job reattachable via job control |
| Job control (`&`, `jobs`, `fg`, `bg`, `disown`) | Shell-level concurrency within one tty. Insufficient for the orchestrator because the `bash` tool call still *waits* on the foreground pipeline unless the PID is detached and the recipe returns. | `man bash` → "Job Control" |

`nohup` + `&` + redirect is the primitive this repo's Makefile uses; `setsid`
is the stronger variant for ops that must survive even a `kill -TERM -PGID`
on the parent session.

---

## Blocking patterns identified in this repo

| # | Pattern | Typical wall time | Why it blocks |
|---|---|---|---|
| 1 | `make gate` | ~40 min | Composite: lint + typecheck + collect + test + smoke, all serial in one recipe (`Makefile:272`) |
| 2 | `make test-unit` | ~27 min | Full unit suite under xdist (`Makefile:222`) |
| 3 | `make test-specific TESTFILE=...` | 2–5 min | Single-file pytest, still holds the bash tool for the whole run (`Makefile:229`) |
| 4 | `make test-e2e` | >5 min | End-to-end suite, needs live services (`Makefile:450`) |
| 5 | `make ansible-syntax` | ~1 min | Loops every playbook through `ansible-playbook --syntax-check` (`Makefile:475`) |
| 6 | `make lint`, `make typecheck` | cold-cache: 60–120s | Cold uv / mypy startup dominates (`Makefile:210`, `Makefile:216`) |
| 7 | `make qa`, `make validate` | composite, >10 min | Wrap multiple slow targets |
| 8 | `make smoke` | 1–3 min | Smoke phase of gate (`Makefile:828`) |
| 9 | `make molecule-test-all` | unbounded | One scenario per playbook, serial (`Makefile:504`) |
| 10 | Single subagent task >5 min | variable | Holds one floor slot indefinitely; nobody reaps or kills it |

---

## The 12 actionable items

Each item: **Pattern → Fix → Citation → Verification.**

### 1. `make gate` MUST run via `make gate-background`, never foreground.

`gate-background` (`Makefile:2058-2093`) wraps `make gate` in
`nohup $(MAKE) gate > .gate-logs/gate-<ts>.log 2>&1 &`, writes the PID to
`.gate-background.pid`, and returns in <1 s. A sidecar subshell sleeps
`GATE_TIMEOUT` (default 3600 s) and SIGTERM→SIGKILLs the gate if it hangs.
- **Citation:** `AGENTS.md` → "Background-gate workflow (canonical way to run a long gate)"; `Makefile:2054-2093`.
- **Verify:** `make gate-status-check` shows `RUNNING (pid=...)` and the current `=== GATE PHASE: <name> ===` marker.

### 2. Poll gate status from a *subagent*, never the main thread.

`gate-status-check` (`Makefile:2096-2111`) is non-blocking (<50 ms): prints
`RUNNING`/`FINISHED`, the live phase, and the last 20 log lines. Polling it
on the main thread still burns the only non-delegatable resource.
- **Citation:** `background-test-runner` skill, Rule 3: *"Poll from subagents, not the main thread"* (`.opencode/skills/background-test-runner/SKILL.md:76-78`).
- **Verify:** dispatch a read-only research subagent whose only call is `make gate-status-check` on a 60 s loop.

### 3. `make test-unit` and `make test-specific` MUST run via `make test-bg`.

`test-bg` (`Makefile:1285-1293`) accepts `TESTFILE=` or `FILES=` and launches
pytest under `nohup ... &` with output to `.gate-logs/test-bg-<ts>.log`.
- **Citation:** `background-test-runner` skill §1-2 (`.opencode/skills/background-test-runner/SKILL.md:17-34`); skill Rule 1: *"Never run a test that takes >30s in the foreground"* (`SKILL.md:73-74`).
- **Verify:** `make gate-logs` lists the new `test-bg-*.log` with status `incomplete` → `PASS`/`FAIL`.

### 4. `make test-e2e` MUST be backgrounded via `make test-bg FILES='tests/e2e/'`.

Same primitive as item 3, scoped to the e2e directory. No dedicated
`test-e2e-bg` target exists yet — the `FILES=` form of `test-bg` covers it
without new Makefile surface.
- **Citation:** `Makefile:1288-1289` (the `FILES=` branch); `AGENTS.md` → "Main-thread command restriction" lists `make test-e2e` as forbidden on main thread.
- **Verify:** `make test-bg FILES='tests/e2e/'` then `make gate-logs`.

### 5. `make ansible-syntax` → dispatch to a subagent, do NOT run on main thread.

At ~60 s it is below the hard 30 s plugin threshold but still blocks the floor
for a full minute. A subagent running `make ansible-syntax` is isolated,
returns a terse pass/fail, and frees the main thread immediately.
- **Citation:** `AGENTS.md` → "Main-thread command restriction": the only allowed main-thread commands are `make ci-verdict-fast`, `make ship-commit`, and task dispatches.
- **Verify:** subagent result message contains `ansible-syntax: PASS` or a cited failure list.

### 6. `make lint`, `make typecheck` → wrap in `make task CMD='...'` (5-min timeout).

The generic `task` target (`Makefile:237-250`) enforces a
`GLUDD_TASK_TIMEOUT` (default 300 s) wall-clock cap with SIGTERM→SIGKILL
escalation. This is the sanctioned way to give *any* command a deadline when
no `-background` variant exists.
- **Citation:** `Makefile:233-250` (the `task` target); `AGENTS.md` → "Subagent dispatch reliability rules" §1: *"Every dispatched task MUST have a timeout."*
- **Verify:** `make task CMD='make typecheck'` exits 0 or 124 (timeout), never hangs.

### 7. `make qa` and `make validate` MUST NOT run foreground — they are composite long targets.

Both chain multiple slow phases. The only sanctioned composite-equivalent is
`make gate-background` (which is itself the canonical composite: lint +
typecheck + collect + test + smoke). If a custom composite is needed, build
a new `-background` target modeled on `gate-background`.
- **Citation:** `AGENTS.md` → "Long-Running Operations MUST Be Backgrounded" lists `make qa` and `make validate` explicitly; plugin `enforce-make.ts` denies them on the main thread.
- **Verify:** plugin deny message includes `SUGGESTION: make gate-background + make gate-status-check`.

### 8. `make smoke` and `make molecule-test-all` → background via `nohup ... &` recipe.

Neither has a dedicated `-background` target. Follow the canonical pattern
from `gate-background` (`Makefile:2079`): `nohup $(MAKE) <target> >
.gate-logs/<target>-<ts>.log 2>&1 & echo $$! > .gate-logs/<target>.pid`.
- **Citation:** `Makefile:2079` (the canonical `nohup` recipe); `AGENTS.md` → "Long-Running Operations MUST Be Backgrounded" §1.
- **Verify:** new `.gate-logs/smoke-*.log` appears and is visible in `make gate-logs`.

### 9. Every subagent task gets a 5-minute deadline via `enforce-deadline.ts` + `task_watchdog.py`.

The deadline plugin records dispatch timestamps to
`/tmp/gludd-task-deadlines.json`; the `task_watchdog.py` daemon
(`make task-watchdog-start`, `Makefile:2248`) polls every 5 s and SIGTERM→SIGKILLs
any task over `GLUDD_TASK_TIMEOUT_MS` (default 300 000 ms).
- **Citation:** `AGENTS.md` → "Subagent dispatch reliability rules" §1 (detection + killing two-layer system); `Makefile:2248-2280`.
- **Verify:** `make task-watchdog-status` shows the daemon alive; `/tmp/gludd-task-killed.json` records any breach.

### 10. Start the task watchdog at session boot — never assume it is running.

`make watchdog-auto` (referenced in `AGENTS.md` Session Start Protocol step 0)
starts both the session-idle watchdog and the task-deadline watchdog. Without
it, item 9's kill layer is inert.
- **Citation:** `AGENTS.md` → "Session Start Protocol" step 0: *"Run `make watchdog-auto`"*; `AGENTS.md` → "Subagent dispatch reliability rules" §1 (killing layer).
- **Verify:** `make task-watchdog-status` prints `RUNNING (pid=...)`.

### 11. Bias subagent tasks toward *uniform short duration* so the floor refills in waves, not stragglers.

A 30 s task and a 5 min task in the same wave leaves the floor at 1–2 agents
for 4+ minutes. Split long tasks; prefer 10 × ~2 min tasks over 2 × ~10 min.
- **Citation:** `AGENTS.md` → "Steady-state dispatch" §4: *"Prefer uniform-duration tasks"*; `background-test-runner` skill Rule 2.
- **Verify:** live agent count (via `scripts/agent_liveness.py`) stays ≥10 between waves.

### 12. Process subagent results in <5 s and dispatch the next wave *immediately* — no analysis prose between waves.

Any prose between waves is main-thread grind that drops the floor. The
integrator digests results; the orchestrator dispatches.
- **Citation:** `AGENTS.md` → "Steady-state dispatch" §1: *"Process results FAST... do NOT write ANY analysis prose between waves"*; `AGENTS.md` → "Message-shape mechanical rule".
- **Verify:** no assistant response between two dispatch waves contains >3 lines of non-citation prose.

### 13. Verify every background op's terminal marker before declaring success.

A backgrounded job that "completed" may have been *killed* (timeout, OOM).
The truth is the `=== GATE: PASSED ===` / `FAIL` marker in the log and the
`.gate-status` file — never the rest event.
- **Citation:** `AGENTS.md` → "Agent At-Rest / Re-Dispatch Policy" §1: *"A background task that 'completed' may have been KILLED, not finished — check its actual exit code."*
- **Verify:** `make gate-status-check` prints `FINISHED: PASS` (or `FAIL`/`GATE_TIMEOUT`).

### 14. Background never means invisible — every backgrounded op must stream a heartbeat.

`nohup ... > /dev/null 2>&1 &` is forbidden by the observability invariant.
The `gate-background` recipe writes to `.gate-logs/gate-<ts>.log`, the gate
emits `=== GATE PHASE: <name> ===` markers, and `gate-status-check` shows the
last 20 lines on every poll. New `-background` targets must follow the same
shape.
- **Citation:** `AGENTS.md` → "No Unseen Events (observability invariant)" §1: *"Stream or heartbeat — never go dark."*
- **Verify:** `.gate-logs/<target>-*.log` is non-empty and grows between polls.

### 15. Never wire an auto-relaunching watcher around a long background op.

A subagent that re-dispatches the gate on every "completed" event will respawn
it ~6× and OOM the host (2026-06-18 incident). Long ops are owned by the *main
loop* via `run_in_background` / `gate-background`, polled exactly once per
cycle, never auto-relaunched.
- **Citation:** `AGENTS.md` → "Agent At-Rest / Re-Dispatch Policy" §1 (the ZOMBIE rule).
- **Verify:** no `Task`/`Workflow` dispatch whose prompt contains "re-run on completion" or equivalent.

---

## Source index

| Source | Location | Used for |
|---|---|---|
| `man nohup` (POSIX.1-2008) | system | detach-child primitive |
| `man setsid` (util-linux) | system | full-session detach primitive |
| `man bash` → "Job Control" | system | why shell-level `&` alone is insufficient |
| `AGENTS.md` → "Main-thread command restriction (ANTI-STALL RULE)" | repo root | the allow-list of main-thread commands |
| `AGENTS.md` → "Background-gate workflow" | repo root | canonical gate-background pattern |
| `AGENTS.md` → "CRITICAL: Long-Running Operations MUST Be Backgrounded" | repo root | 30 s threshold + plugin enforcement |
| `AGENTS.md` → "Pipeline Orchestration Model" / "Steady-state dispatch" | repo root | 10-agent floor, uniform-duration, fast result processing |
| `AGENTS.md` → "Subagent dispatch reliability rules" | repo root | 5-min deadline, two-layer kill |
| `AGENTS.md` → "Session Start Protocol" | repo root | `make watchdog-auto` step 0 |
| `AGENTS.md` → "No Unseen Events" | repo root | heartbeat requirement |
| `AGENTS.md` → "Agent At-Rest / Re-Dispatch Policy" | repo root | killed-vs-finished, ZOMBIE rule |
| `.opencode/skills/background-test-runner/SKILL.md` | repo | `test-bg` workflow + rules |
| `Makefile:2058-2156` | repo | `gate-background`, `gate-status-check`, `gate-tail`, `gate-logs`, `gate-kill` |
| `Makefile:1285-1293` | repo | `test-bg` (TESTFILE + FILES) |
| `Makefile:237-250` | repo | generic `task` target with timeout |
| `Makefile:2248-2280` | repo | `task-watchdog-start/status/stop/log` |
| `Makefile:272`, `210`, `216`, `229`, `450`, `475`, `828`, `504` | repo | the slow targets being replaced |
| `.opencode/plugin/enforce-make.ts` (referenced in AGENTS.md) | repo | long-running-foreground deny + `SUGGESTION` directive |
| `.opencode/plugin/enforce-deadline.ts` (referenced in AGENTS.md) | repo | 5-min task deadline detection |
| `scripts/task_watchdog.py` (referenced in AGENTS.md) | repo | deadline kill layer |

---

## Summary table: pattern → sanctioned replacement

| Blocking pattern | Sanctioned replacement | Items |
|---|---|---|
| `make gate` (40 min) | `make gate-background` + subagent poll of `gate-status-check` | 1, 2, 13, 14 |
| `make test-unit` (27 min) | `make test-bg FILES='tests/unit/'` | 3 |
| `make test-specific` (2–5 min) | `make test-bg TESTFILE=...` | 3 |
| `make test-e2e` (>5 min) | `make test-bg FILES='tests/e2e/'` | 4 |
| `make ansible-syntax` (~1 min) | dispatch to a subagent | 5 |
| `make lint` / `make typecheck` (cold 60–120 s) | `make task CMD='...'` (5-min cap) | 6 |
| `make qa` / `make validate` (composite) | `make gate-background` (composite equivalent) | 7 |
| `make smoke` / `make molecule-test-all` | new `nohup`-based `-background` target | 8 |
| Long subagent (>5 min) | `enforce-deadline.ts` + `task_watchdog.py` | 9, 10 |
| Uneven wave durations | uniform short tasks | 11 |
| Inter-wave prose | <5 s digest, immediate re-dispatch | 12 |
| False-success on killed bg jobs | verify terminal marker | 13 |
| Silent background ops | heartbeat to `.gate-logs/` | 14 |
| Auto-relaunching watchers | own long ops via main loop only | 15 |
