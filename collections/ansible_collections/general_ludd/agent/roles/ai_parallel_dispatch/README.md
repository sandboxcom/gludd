# ai_parallel_dispatch role

Fan out **N AI-model calls in parallel** with Ansible **native async**, then
**barrier-join** them until *all* — or a *required subset* — return. A promise
system built on the same execution engine as `ansible-playbook`:

- Each `general_ludd.agent.gludd_model_call` task launched with `async: <t>` +
  `poll: 0` returns **immediately** with an `ansible_job_id`. **That id is the
  promise.**
- A looped `ansible.builtin.async_status` with `until` / `retries` / `delay` is
  the **await**: it polls every promise's job file (`~/.ansible_async`) until the
  join condition is satisfied.

gludd runs ansible-core's `PlaybookExecutor` in a forked child
(`CoreAnsibleRunner._execute_with_core`) — the same engine as `ansible-playbook`
— so `async:` / `poll:` / `async_status` and `meta: flush_handlers` all behave
natively.

## Promise / barrier semantics

| Concept  | Mechanism                                                                 |
|----------|---------------------------------------------------------------------------|
| Promise  | `gludd_model_call` with `async: {{ async_timeout }}` + `poll: 0` → ajob id |
| Await    | `async_status` looped over the job ids with `until`/`retries`/`delay`      |
| Join     | `until` counts FINISHED jobs in the **wait-set**; final `assert` is truth  |
| Harvest  | only FINISHED, non-failed jobs fold into `_apd_results` keyed by call name |

### Join policies (`join_policy`)

- **`all`** (default) — every dispatched call must finish.
- **`required`** — only calls flagged `required: true` must finish; the barrier
  returns as soon as all required ids are done. Optional calls are harvested if
  ready, otherwise simply absent. **Fail-closed:** `required` with *zero* calls
  flagged required treats **every** call as required (an empty required set is
  never silently accepted as "done").
- **`any`** — the first call to finish satisfies the join.

The final `assert` in `tasks/main.yml`
(`required_returned | length == required_names | length`) is the **single source
of truth** — `failed_when: false` on the barrier keeps a timed-out *optional* job
from hard-failing, while a timed-out *required* job fails the play.

## In-flight concurrency cap (why batched, not unbounded)

The daemon gateway's budget cap is **TOCTOU** under concurrency: `check_budget`
then `record_spend` is not atomic, and the generation path passes no
`estimated_cost` / `budget_remaining`, so the pre-call gate can be bypassed by a
concurrent burst. The role therefore **never fires unbounded**:

`dispatch_calls` is sliced with the Jinja `batch(max_in_flight)` filter; each
slice is launched **and fully barrier-joined** (via `dispatch_batch.yml`) before
the next slice launches. So at any instant at most `max_in_flight`
`/admin/models/call` POSTs are in flight, regardless of `len(dispatch_calls)`.

> **Honest note:** `loop_control.max_in_flight` does **not** throttle `poll: 0`
> fire-and-forget tasks — they each return an ajob id instantly. **Batching the
> *await*** (one include per slice, fully drained before the next) is the only
> real throttle.

`max_in_flight` defaults to `4`. Set it to `1` to fully serialize (strictest
budget safety: even `max_in_flight` concurrent POSTs can each pass `check_budget`
before any `record_spend` lands, leaving up to `max_in_flight - 1` calls of
possible overspend until the gateway pre-call gate is fixed).

## GLUDD_PLAYBOOK_TIMEOUT constraint

Total wall-clock is bounded by **`GLUDD_PLAYBOOK_TIMEOUT`** (default 300s;
**SIGKILL** on the fork-child group). Every `async:` deadline **plus** the
barrier `retries * delay` **plus** the sum over sequential batches must fit
inside it. The input `assert` enforces all of:

- `async_timeout >= call_request_timeout` — so the module's own HTTP timeout (not
  the async wrapper) owns the deadline and the module exits cleanly with usage
  data instead of being killed mid-flight.
- `async_timeout < playbook_timeout`
- `barrier_retries * barrier_delay < playbook_timeout` (per-batch poll budget)
- **sum-over-batches:**
  `ceil(N / max_in_flight) * (barrier_retries * barrier_delay) < playbook_timeout`

Because batches run **sequentially**, the sum-over-batches assert fails **fast**
with a remediation message rather than getting SIGKILLed mid-poll. If you have
many calls, lower `barrier_retries`/`barrier_delay` or raise `max_in_flight`
(fewer batches) — otherwise the role refuses to run.

## Key variables

| Variable | Default | Notes |
|---|---|---|
| `dispatch_calls` | `[]` | list of `{name, prompt, model_profile|route_task_type, max_tokens?, required?}` |
| `max_in_flight` | `4` | concurrency cap (await is batched); `1` = fully serial = budget-safest |
| `join_policy` | `all` | `all` \| `required` \| `any` |
| `async_timeout` | `150` | `async:` on each call (>= `call_request_timeout`) |
| `call_request_timeout` | `120` | `gludd_model_call`'s own HTTP request timeout |
| `barrier_retries` | `60` | `async_status` retries per batch (the poll count) |
| `barrier_delay` | `2` | seconds between `async_status` polls |
| `playbook_timeout` | `env GLUDD_PLAYBOOK_TIMEOUT \| 300` | wall-clock ceiling the asserts check |
| `dispatch_max_tokens` | `2048` | per-call `max_tokens` when a call omits its own |
| `artifact_dir` | `/tmp/gludd-ai-parallel-dispatch` | output dir for `ai_parallel_dispatch.json` |
| `enable_handler_variant` | `false` | also run the `handlers/` + `meta: flush_handlers` demo |
| `daemon_url` | `http://localhost:8000` | daemon base url |
| `psk` | `""` | shared secret; `no_log` everywhere when non-empty |

Each call item:

```yaml
dispatch_calls:
  - { name: planner,  prompt: "Plan X",   route_task_type: planning, required: true }
  - { name: reviewer, prompt: "Review X", model_profile: mock-profile, required: true }
  - { name: poet,     prompt: "Haiku X",  model_profile: mock-profile, required: false }
```

`name` is the unique result key (asserted unique). `required` is honored only
under `join_policy: required`.

## Output artifact

`{{ artifact_dir }}/ai_parallel_dispatch.json`:

```json
{
  "role": "ai_parallel_dispatch",
  "join_policy": "required",
  "max_in_flight": 2,
  "dispatched": ["planner", "reviewer", "poet"],
  "required": ["planner", "reviewer"],
  "returned": ["planner", "reviewer", "poet"],
  "required_returned": ["planner", "reviewer"],
  "satisfied": true,
  "results": { "planner": { "text": "...", "usage": {"total_tokens": 5}, "...": "..." } }
}
```

A downstream play reads `_apd_results['<name>'].text` for the required keys it
cares about.

## Handler / `meta: flush_handlers` variant

`enable_handler_variant: true` runs `tasks/handler_barrier.yml` +
`handlers/main.yml`: a dispatching task notifies one handler per call, then
`meta: flush_handlers` is the explicit **barrier** that drains every queued
dispatch (each handler launches its model call `async`/`poll: 0` and appends its
job id to a JSONL ledger); after the flush the ledger is `async_status`-re-joined.

**This variant is weaker than the async path** and is **off by default**:

- `flush_handlers` runs handlers **sequentially**, in notify order — no
  concurrency cap, so the budget-TOCTOU `max_in_flight` defense is forfeited.
- **No required-subset early return** — flush always drains every notified
  handler.
- Weaker result collection — a handler's `register` is only reliably readable
  after flush, hence the JSONL ledger re-join.

Prefer `tasks/main.yml` (async) for capped concurrency, ordered harvest, and
required-subset early return. The handler variant is retained only because the
`meta: flush_handlers` approach was explicitly requested.

## Security / honesty caveats

- `psk` is `no_log` whenever non-empty. **With an empty PSK** (as in the molecule
  mock) prompts/results are **not** redacted in `-vvv` output or in
  `~/.ansible_async` job files — fine for the mock; prefer always-on `no_log`
  for sensitive prompts in production.
- Budget is **throttled, not atomic** — see the concurrency section. `1` is the
  only fully budget-safe `max_in_flight` until the gateway pre-call gate lands.
- Optional jobs left running when a `required`/`any` barrier returns early are
  **not** explicitly cancelled; they rely on their own `async:` deadline to be
  SIGKILLed by ansible, and their spend still hits the TOCTOU window.
- `async_status` status files live in `~/.ansible_async`; a **full disk** (a
  known gludd ENOSPC failure mode) breaks status writes and makes every job look
  unfinished — burning all retries, then failing the join.

## Example

```yaml
- name: Fan out three model calls, require two
  ansible.builtin.include_role:
    name: general_ludd.agent.ai_parallel_dispatch
  vars:
    daemon_url: "http://localhost:8000"
    join_policy: required
    max_in_flight: 2
    dispatch_calls:
      - { name: planner,  prompt: "Plan the change",   route_task_type: planning, required: true }
      - { name: reviewer, prompt: "Review the change", route_task_type: review,   required: true }
      - { name: poet,     prompt: "Write a haiku",      model_profile: mock,       required: false }
```
