# ai_parallel_dispatch

Fan out multiple AI-model calls (`gludd_model_call`) in parallel using Ansible
**native async** (`async:` + `poll: 0` → `ansible_job_id`) and **barrier-join**
them with `async_status` until all — or a required subset — return.

## Promise / await semantics

- **Promise.** Each `gludd_model_call` is launched with `async: <t>` + `poll: 0`,
  so it returns *immediately* with an `ansible_job_id`. That id is the promise.
- **Await.** The barrier polls each promise's async job file until it is
  `finished`. The barrier is the await.
- **Join policy** (`join_policy`):
  - `all` — every dispatched call must finish.
  - `required` — only calls flagged `required: true` must finish; the barrier
    **early-returns** as soon as the required subset is done (optional calls are
    harvested if already finished). Fail-closed: `required` with no call flagged
    `required: true` treats *every* call as required.
  - `any` — the barrier **early-returns** as soon as the first call finishes.

### Why the barrier is an outer retry loop, not `until` on a looped task

In Ansible, an `until`/`retries`/`delay` placed on a **looped** task is evaluated
**per loop item** against that item's own registered result, and `retries` apply
**per item**. The aggregate `.results` list is only assembled *after* the whole
loop finishes. So an `until` that references the aggregate (e.g. "stop when the
required subset across the batch is finished") never sees a complete list during
evaluation — it is always stale/undefined — and per-item retries multiply the
worst-case wall-clock to `len(batch) * retries * delay`, blowing the timeout
budget.

This role therefore drives the barrier as an **outer retry loop**
(`dispatch_batch.yml` loops `barrier_sweep.yml` over a retry-index range). Each
pass runs **one** non-retrying `async_status` sweep over all job ids, harvests
newly-finished jobs, and recomputes a single boolean `_apd_barrier_done` over the
wait-set. Once true, later passes are skipped — true cross-job early-return for
`any`/`required`. The retry budget is therefore **per batch**, not per item:
worst-case barrier wall-clock per batch is `barrier_retries * barrier_delay`.

The handler/`flush_handlers` variant's re-join *does* use `until` on a looped
`async_status`, but it checks the **per-item** scalar `_apd_h_poll.finished`
(which *is* populated per iteration) — the correct idiom; it does not reference an
aggregate.

## Concurrency cap (budget rationale)

The daemon gateway's budget cap is **TOCTOU**: `check_budget` then `record_spend`
is not atomic, and the generation path passes no estimated cost, so an unbounded
burst of concurrent `/admin/models/call` requests can blow the budget. The role
therefore **batches** the fan-out: it launches at most `max_in_flight` promises,
barrier-joins that slice, then launches the next. In-flight concurrency is
hard-capped at `max_in_flight` regardless of `len(dispatch_calls)`. The handler
variant gets the same cap by notifying + flushing in `max_in_flight`-sized waves.

### Spend vs concurrency

The cap bounds **concurrent** spend, not **total** spend. Early-return
(`any`/`required`) only saves *barrier wall-clock*: the async jobs for optional /
losing calls keep running to completion on the host and still consume their budget
— there is no daemon-side cancel. If you need to bound total spend, don't dispatch
optional calls you won't use.

## Timeout budget — `GLUDD_PLAYBOOK_TIMEOUT`

`playbook_timeout` mirrors `GLUDD_PLAYBOOK_TIMEOUT` (default 300s). When the
playbook overruns it, the operator SIGKILLs the fork-child group mid-run. The
role's input `assert` fails fast — *before* launching anything — unless **all** of:

- `async_timeout >= call_request_timeout` — the module, not the async wrapper,
  owns the request deadline.
- `async_timeout < GLUDD_PLAYBOOK_TIMEOUT`.
- `barrier_retries * barrier_delay < GLUDD_PLAYBOOK_TIMEOUT`.
- **`barrier_retries * barrier_delay >= async_timeout`** — *no premature
  abandonment.* A promise that runs longer than the polling window but within its
  async budget would otherwise be dropped by the harvest even though it could
  still finish. The barrier must wait at least as long as the async budget it
  launched.
- **`ceil(N / max_in_flight) * max(barrier_retries*barrier_delay, async_timeout)
  < GLUDD_PLAYBOOK_TIMEOUT`** — the sum-over-batches ceiling. Batches run
  sequentially; per-batch wall-clock is the *larger* of the polling window and the
  async budget (a launched job keeps running up to `async_timeout` independent of
  polling). Lower `async_timeout`/`barrier_retries`/`barrier_delay` or raise
  `max_in_flight` (fewer batches) to fit.

With the defaults (`async_timeout: 120`, `barrier_retries: 60`,
`barrier_delay: 2` → window 120s): per batch 120s; `ceil(N/4)` sequential batches
all fit under 300s.

## Partial-failure / artifact

Finished-but-failed jobs and jobs that timed out before the barrier window are
dropped from `_apd_results`; the `required`/`all` join `assert` then fails when a
required name is missing (failed/timed-out jobs are **not** silently treated as
success). The artifact (`{{ artifact_dir }}/ai_parallel_dispatch.json`) records
`returned`, `required`, `required_returned`, and **`dropped`** (dispatched but not
harvested) so a consumer can tell "all required returned, optionals dropped" apart
from "everything returned".

## Key variables

See `defaults/main.yml` for the full annotated list. Most-used:

| var | default | meaning |
|-----|---------|---------|
| `dispatch_calls` | `[]` | list of `{name, prompt, model_profile|route_task_type, max_tokens?, required?}` |
| `max_in_flight` | `4` | concurrency cap (in-flight async jobs per batch / wave) |
| `join_policy` | `all` | `all` \| `required` \| `any` |
| `async_timeout` | `120` | `async:` budget per call (seconds) |
| `barrier_retries` × `barrier_delay` | `60` × `2` | per-batch barrier polling window (seconds) |
| `playbook_timeout` | `GLUDD_PLAYBOOK_TIMEOUT` or `300` | SIGKILL ceiling the assert checks |
| `enable_handler_variant` | `false` | also run the `handler_barrier.yml` flush_handlers demo |

## Validation

Structural tests: `make test-unit
TESTFILE='tests/unit/test_ai_parallel_dispatch_role.py'`.

Runtime (the only thing that exercises real `async_status`/sweep/early-return
behavior against the mock daemon):
`make molecule-test SCENARIO=role_ai_parallel_dispatch`.
