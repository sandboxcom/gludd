# Remediation System Design

## The problem

Blocked tasks accumulate. A todo that hits `BLOCKED_ON_HUMAN` and never gets
resolved will sit in that status forever. A `permission_escalation` human-todo
that the operator forgot about blocks the originating agent indefinitely. A
task that has been re-queued four times — each retry hitting the same missing
AWS credentials — burns tokens on every attempt. The project stalls, silently,
because nothing in the system surfaces the stall or kicks it back into motion.

The remediation system keeps projects moving by detecting these stuck states
and applying one of three remediation strategies:

  1. **Dispatch a fresh agent** with a note telling it the prior attempt
     blocked and to try a different approach.
  2. **Schedule a cron-style retry** in N hours (giving the operator time to
     resolve the block).
  3. **File a high-priority human-todo** with the blocker summary so the
     operator is re-pinged.

## Architecture

```text
                     ┌──────────────────────────┐
                     │  hourly schedule entry   │
                     │  (work_type=blocker_scan)│
                     └────────────┬─────────────┘
                                  │ spawns QUEUED child each hour
                                  ▼
              ┌─────────────────────────────────────────┐
              │  agent role: blocker_scan               │
              │  → POST /admin/remediation/remediate    │
              └────────────────────┬────────────────────┘
                                   ▼
            ┌──────────────────────────────────────────┐
            │  BlockerDetector.scan()                  │   ← read-only
            │    • BLOCKED_ON_HUMAN todos > threshold  │
            │    • chronic re-queues (run_count > N)   │
            │    • stale open human-todos              │
            └────────────────────┬─────────────────────┘
                                 ▼
            ┌──────────────────────────────────────────┐
            │  RemediationDispatcher.remediate(task)   │   ← write
            │    dispatch_agent | schedule_retry |     │
            │    file_human_todo | no_action           │
            └────────────────────┬─────────────────────┘
                                 ▼
            ┌──────────────────────────────────────────┐
            │  RemediationActionRepository.record()    │   ← audit trail
            │  (one row per action, ok/fail/no-action) │
            └──────────────────────────────────────────┘
```

## The detector — `src/general_ludd/remediation/blocker_detector.py`

Three signal sources, all gated by operator-tunable thresholds:

  1. **Todos in `BLOCKED_ON_HUMAN` status older than the per-category
     threshold.** The linked `HumanTodoModel` (matched via
     `parent_agent_todo_id`) classifies the blocker kind.
  2. **Chronically re-queued todos** — `run_count` exceeds
     `max_requeues_before_chronic`. These have been retried multiple times;
     the system retries once more with a note.
  3. **Stale open human-todos** — human-todos that have been `open` for more
     than the threshold without resolution. These are escalated to a
     high-priority reminder.

### Classification logic

| Signal | blocker_kind | suggested_remediation |
|---|---|---|
| linked human-todo category=`permission_escalation` | `permission_escalation` | `schedule_retry` |
| linked human-todo category=`input_request` | `human_input` | `file_human_todo` |
| linked human-todo (other categories) | `human_input` | `file_human_todo` |
| chronic re-queue, no human-todo | `resource_contention` | `dispatch_agent` |
| stale open human-todo, category=`permission_escalation` | `permission_escalation` | `schedule_retry` |
| stale open human-todo (other) | `human_input` | `file_human_todo` |

The dispatcher may override the suggestion (e.g. an operator policy that
forces permission escalations to `file_human_todo` instead of retry).

### Thresholds

Configured via `RemediationConfig` (dataclass; loaded from
`config/remediation.yml` at daemon startup or read live via
`daemon_state["remediation_config"]`):

| Field | Default | Rationale |
|---|---|---|
| `human_input_block_hours` | 24 | A day is long enough for an operator to triage. |
| `permission_escalation_block_hours` | 4 | Permissions are usually quick — escalate fast. |
| `max_requeues_before_chronic` | 3 | Three retries with the same outcome = systemic. |
| `chronic_lookback_days` | 7 | A week captures recurring patterns without over-weighting. |
| `min_chronic_incidents` | 5 | Below this is noise, not a pattern. |
| `retry_delay_hours` | 4 | How long `schedule_retry` waits before re-pinging. |

Defaults are deliberately conservative so a healthy project does nothing.

## The dispatcher — `src/general_ludd/remediation/dispatcher.py`

Reads `BlockedTask` findings and applies the strategy. Side effects:

  - **`dispatch_agent`** — creates a fresh QUEUED todo with the original
    task spec plus a `[remediation]` note. The event loop picks it up on
    the next tick; the normal claim → dispatch → review pipeline runs.
  - **`schedule_retry`** — creates a SCHEDULED todo firing in
    `retry_delay_hours`. The TodoScheduler promotes it to QUEUED when due.
  - **`file_human_todo`** — creates a high-priority `HumanTodoModel` with
    the blocker summary. Category is preserved (`permission_escalation`
    or generic `blocker`); `parent_agent_todo_id` is set when known.
  - **`no_action`** — no side effect; the audit row records the decision.

Every action — including `no_action` and failed actions — is persisted as a
`RemediationActionModel` row so the operator can query the full history via
`GET /admin/remediation/history` and `gludd remediation history`.

## The scheduler — hourly scan

The schedule entry is seeded by `make init` via
`scripts/seed_blocker_scan_schedule.py`. It registers a cron-template todo
with `cron="0 * * * *"` and `work_type=blocker_scan`. Every hour the
TodoScheduler spawns a QUEUED child; the dispatched agent role calls
`POST /admin/remediation/remediate`, which runs the detector and applies
remediation for each finding in one DB transaction.

The scan is also runnable on demand via:

  - `POST /admin/remediation/remediate` (daemon endpoint)
  - `gludd remediation scan` (CLI; read-only)
  - directly instantiating `BlockerDetector.scan()` from Python

## Chronic-blocker reporting — `src/general_ludd/remediation/reporter.py`

`BlockerDetector.chronic_blockers()` groups recent `BLOCKED_ON_HUMAN`
audit-trail events by `(task_type, blocker_kind)` and surfaces pairs whose
incident count crosses `min_chronic_incidents` over `chronic_lookback_days`.
The report is returned as a stable dict shape and surfaced via:

  - `GET /admin/remediation/chronic-blockers` (PSK-gated)
  - `gludd remediation chronic-blockers [--json]`

### Worked example

A deploy task is blocked 5 times in a week on missing AWS credentials:

  1. The agent dispatches the deploy; it fails on missing credentials.
  2. The agent files a `permission_escalation` human-todo. The parent todo
     transitions to `BLOCKED_ON_HUMAN`.
  3. After 4h the detector surfaces it; `schedule_retry` fires. The operator
     approves the credentials; the next dispatch works.
  4. This repeats 5 times across the week (different deploys, same root
     cause: static credentials that keep expiring).
  5. `gludd remediation chronic-blockers` flags the pair
     `(task_type=infra, blocker_kind=permission_escalation, count=5)`.
  6. The operator reads the report, sets up OIDC instead of static creds,
     and the blocks stop.

The chronic-blocker report is the systemic-issue signal; the per-incident
remediations keep individual projects moving in the meantime.

## Operator surface

| Command | Effect |
|---|---|
| `gludd remediation scan [--project ID]` | Run the detector once, print findings. |
| `gludd remediation chronic-blockers [--project ID] [--json]` | Print the chronic-blocker report. |
| `gludd remediation history [--project ID] [--since DATE]` | Audit trail of past remediation actions. |
| `gludd remediation config show` | Print current thresholds. |
| `gludd remediation config edit` | Open `config/remediation.yml` in `$EDITOR`. |
| `GET /admin/remediation/scan` | Same as CLI `scan`. |
| `POST /admin/remediation/remediate` | Run detector + apply remediation. |
| `GET /admin/remediation/chronic-blockers` | Same as CLI `chronic-blockers`. |
| `GET /admin/remediation/history` | Same as CLI `history`. |
| `GET /admin/remediation/config` | Same as CLI `config show`. |

All `/admin/*` endpoints require the daemon PSK.

## File map

| Path | Purpose |
|---|---|
| `src/general_ludd/remediation/__init__.py` | Public surface re-exports. |
| `src/general_ludd/remediation/blocker_detector.py` | `BlockerDetector`, `BlockedTask`, `ChronicBlocker`, `RemediationConfig`. |
| `src/general_ludd/remediation/dispatcher.py` | `RemediationDispatcher`, `RemediationAction`, `RemediationActionKind`. |
| `src/general_ludd/remediation/reporter.py` | `chronic_blocker_report`. |
| `src/general_ludd/db/models.py::RemediationActionModel` | Audit-trail table. |
| `src/general_ludd/db/repository.py::RemediationActionRepository` | Audit-trail CRUD. |
| `src/general_ludd/routers/remediation.py` | PSK-gated daemon endpoints. |
| `src/general_ludd/cli_remediation.py` | `gludd remediation` CLI. |
| `scripts/seed_blocker_scan_schedule.py` | Idempotent hourly-schedule seeder (`make init`). |
| `tests/unit/test_blocker_detector.py` | 8 detector tests. |
| `tests/unit/test_remediation_dispatcher.py` | 3 dispatcher tests. |
| `tests/integration/test_remediation_scheduler.py` | 2 integration tests. |
