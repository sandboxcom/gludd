# Time, Timers, and Scoped Notifications — Design (2026-07-10)

Status: **design-complete, not yet implemented.** Line-anchored against current
`master`. Re-confirm line numbers with a Read before implementing — they drift.
Style/format mirrors `docs/design/WAVE_C_DESIGNS_2026-07-10.md`; implement as a
Wave-C-adjacent batch, land as a single-writer sequence (parser → timer entity
→ scheduler phase → scope router → ansible surface), `make gate-async` after
each, before claiming any finding closed.

Three capabilities, one keystone dependency chain: (1) a natural-language time
resolver the model and roles call directly, (2) a Timer entity that reuses the
existing cron-scheduler machinery rather than inventing a parallel scheduler,
and (3) a scope-routing layer on top of the existing (currently flat)
notification primitives (`EventBus`, `HookSystem`, `AgentMessageRepository`,
`HumanTodoRepository`).

---

## SURVEY — what exists today

### 1. Time handling — no natural-language parsing anywhere

`pyproject.toml:59-61` — the only date/time dependency in the tree is
`croniter>=2.0.0` ("Cron expression parsing and next-run computation for the
integrated todo scheduler"). `make grep Q='dateparser'`,
`Q='relativedelta'` → **no matches** anywhere in `src/` or `tests/`. There is
no `pytimeparse`, no `dateutil`, no natural-language datetime resolver of any
kind. Every existing time computation is either (a) `datetime.now(UTC)` /
`timedelta` arithmetic done inline by callers, or (b) croniter evaluating a
5-field cron expression.

The one real "compute a future datetime" primitive is
`event_loop/scheduler.py:62-85 _next_cron_dt(expr, after, tz_name)`: resolves
`ZoneInfo(tz_name)` (raises `ValueError` on unknown tz), converts `after` to
local wall-clock, runs `croniter(expr, after_local).get_next(datetime)`, then
converts back to UTC. This is invoked from two places:
`event_loop/scheduler.py:187` (the tick loop, computing the *next* fire after
a cron template fires) and inline in `routers/todos.py:278-299`
(`POST /api/todos/scheduled`, computing the *initial* `next_run_at` from a
model/human-supplied cron string at creation time — duplicated logic, not
delegated to the scheduler module).

Nothing resolves an expression like `"5 weeks and 3 hours from today"`,
`"next Tuesday at 9am"`, or `"end of Q3"` to a datetime. A model wanting to
set a future time today has to do its own date arithmetic in the prompt/tool
call and pass a literal ISO datetime — exactly the gap this design closes.

### 2. Scheduling/timer — cron-todos only, no standalone timer/deferred-action primitive

`db/models.py:227-248` — `TodoModel`'s scheduling columns:
`scheduled_at` (one-shot fire time), `cron` (5-field expr, presence makes the
row a recurring TEMPLATE), `schedule_timezone` (default `"UTC"`),
`next_run_at`, `last_run_at`, `run_count`, `max_runs`, `schedule_paused`.
Indexed at `db/models.py:256-262` (`ix_todos_scheduled_lookup` on
`status, schedule_paused, next_run_at, scheduled_at` — exactly the columns
`TodoRepository.list_due_scheduled` filters on, per the comment there).

`event_loop/scheduler.py:88-234 TodoScheduler.tick(now)` is the *only* timer
mechanism in the codebase:
- One-shot (`cron is None`, `scheduled_at` set): promotes `SCHEDULED→QUEUED`
  once `now >= scheduled_at` (`:134-153`).
- Recurring (`cron` set): the template row **stays** `SCHEDULED` forever; each
  tick that finds it due (a) advances `next_run_at` via `_next_cron_dt` and
  bumps `run_count`/`last_run_at` (`:197-214`), (b) spawns a **plain QUEUED
  child clone** (`_build_child_data`, `:237-253`, copying only the
  `_CLONE_FIELDS` execution fields at `:34-59` — scheduling fields are
  deliberately NOT copied onto the child) via `repo.create` (`:218-232`), (c)
  retires the template to `CANCELLED` once `run_count >= max_runs`
  (`:159-178`). Advance-then-spawn ordering is deliberate (comment `:180-184`):
  a crash between the two writes yields a missed occurrence, never a
  duplicate.

`event_loop/loop.py:88-106 PHASE_ORDER` — 17 phases; `"run_scheduler"` sits at
index 5, **before** `"claim_runnable_todos"` (index 6) so a todo promoted this
tick is eligible for claiming the same tick (enforced by
`tests/unit/test_scheduler_wiring.py:25-26`).

There is **no** separate Timer/deferred-action table, no "fire an arbitrary
callback/notification after N minutes" primitive divorced from the todo
lifecycle. Everything that needs "do X later" today has to be modeled as a
schedulable todo — which is workable but conflates "work to execute" with
"a bell that rings." `make grep Q='class TimerModel'` → no matches, confirming
no prior art to collide with.

### 3. Notification/event routing — flat broadcast, not scope-based, three disjoint primitives

**`events/bus.py:15-159 EventBus`** — `subscribe(event_type, callback)`
(`:23-28`) keys subscribers by a string type (or `"*"` wildcard, `:50`);
`publish(event)` (`:37-87`) delivers to **every** subscriber of that type plus
every wildcard subscriber — no scoping of any kind. `events/types.py:30-37`
`Event` dataclass carries only `type`, `payload`, `source`, `correlation_id`,
`timestamp`, `event_id` — **no `project_id`/`task_id`/`agent_id` fields**, so
today an event cannot even carry a scope, let alone be routed by one. In
practice `EventBus.subscribe` has exactly **one** production call site
(`event_loop/loop.py:418`, for `"config_reloaded"`) — the bus is real
infrastructure but barely used.

**`events/hooks.py` `HookSystem`** (`:100-159`) — a second, disjoint
mechanism: `register_callback(event_name, callback)` and
`register_webhook(event_name, url, ...)` (`:130-159`), keyed by a raw
`event_name` string, SSRF-guarded (`_ensure_safe_webhook_url`, `:24-37`,
delegating to the canonical `security/ssrf.is_url_blocked`), payload-redacted
before egress (`_redact_payload`, `:53-79`, strips `api_key`/`token`/`secret`/
`password`/`credential`/`authorization`-like keys), fire-and-forget with
tracked pending futures (`:108-112`, `test_webhook_fire_tracking.py`). This is
the right place to hang an external "sink" for a scoped notification, but
again: no scope concept, just an event-name string key.

**`db/repository.py:1417-1573 AgentMessageRepository`** — `send`/`inbox`/
`ack`/`purge_expired`/`unread_counts`. `inbox()` (`:1434-1467`) takes a flat
`recipient: str` (a role or agent name — per the model docstring,
`db/models.py:588-596`, "addressed to a single recipient (a role/agent name)
or the literal string `broadcast`") plus an **optional** `project_id` that,
when given, is OR'd with `project_id IS NULL` (`:1456-1462`) — i.e. the
*only* existing scoping is "this project's messages + unscoped/global ones,"
not a general scope model. `db/models.py:588-621 AgentMessageModel` has a
nullable `project_id` FK but no task/role-distinct column — "role" and
"agent" are the same string space today (send to `recipient="reviewer"` to
reach "the reviewer role", `recipient="coder-3"` to reach one instance — the
system doesn't distinguish them structurally, it's just naming convention).

**`db/repository.py:1927-2060 HumanTodoRepository`** / `db/models.py:842-892
HumanTodoModel` — filters only by `category`/`agent_id`/`priority`/`status`
(`list_open`, `:2009-2024`; `list_all`, `:2026-2050`). **`HumanTodoModel` has
no `project_id` column at all** — confirmed by reading the full column list
(`:857-892`) and by `routers/human_todos.py:69-78 CreateHumanTodoRequest`
(agent_id/title/body/category/priority/parent_agent_todo_id/session_id/
due_at/tags — no project_id). This is a **real gap**: a project-scoped human
notification cannot be filtered at the DB layer today; the only path to a
project is indirectly, via `parent_agent_todo_id → TodoModel.project_id`, and
only when a parent is set.

**Net finding:** three independent, unscoped delivery primitives
(EventBus, HookSystem-webhooks, AgentMessage inbox) plus one
partially-project-scoped one (HumanTodo, via an indirect join only), and zero
shared "who should know about this" concept. This design adds exactly that
layer on top, without replacing any of the three.

### Existing MCP tool + Ansible module scaffolding to reuse

`mcp/builtins.py:37-66` (`RUN_PROJECT_CHECK_TOOL`/`WEB_RETRIEVE_TOOL` as
`MCPTool` dataclasses with `input_schema`), `:101-118`
(`BuiltinToolHandler.__call__` dispatches by `tool_name`), `:233-`
(`register_builtins(client, ...)` registers the synthetic `gludd-builtin`
server) — this is the exact scaffold for adding `resolve_time` and
`set_timer` as model-callable tools with zero new transport plumbing.

Ansible module precedent: `collections/.../plugins/modules/gludd_message.py`
(state=send/receive/ack wrapping `/api/messages`, `GluddClient` PSK-authed
HTTP, check-mode-safe) and `gludd_human_todo.py` (state=present/done/
dismissed wrapping `/api/human-todos`) are the templates for the new
`gludd_time` and `gludd_timer` modules. **Naming collision to avoid:**
`gludd_schedule.py` already exists and does something unrelated (topological
concurrency-safe batching of work items via `POST /api/schedule` — nothing to
do with cron/time), so the new time-setting module must be named
`gludd_timer`, not `gludd_schedule`.

`collection-roles` lists no existing time/timer/notification role (closest is
`task_deadline_check`, which is a different, narrower check). No
`set_timer_and_notify` or `timekeeper` role exists today.

Alembic: `docs/design/WAVE_C_DESIGNS_2026-07-10.md` §C-TODOMODEL already
claims migration `027` (down_revision `"026"`) for blob-cap changes to
`TodoModel`. If that lands first, the new `timers` table migration here is
**`028`**, chained after it — coordinate ordering at implementation time, do
not both claim `027`.

---

## DESIGN

### T-1 — `general_ludd/timekit/` natural-language time resolver

**New files:** `src/general_ludd/timekit/__init__.py`, `resolver.py`.

**Dependency:** add `"dateparser>=1.2.0"` to `pyproject.toml` dependencies
(after the `croniter` entry, `pyproject.toml:61`), with a comment mirroring
the existing dep-rationale style (e.g. "# Natural-language / relative date
resolution for timekit — the model expresses times like '5 weeks and 3 hours
from today' rather than doing its own date arithmetic."). Add a
`[[tool.mypy.overrides]]` entry for `dateparser.*` alongside the existing
`croniter.*` override (`pyproject.toml:174`) since dateparser ships no
`py.typed` marker. Run `make relock` after the pyproject edit (per
`Makefile:227-231`) — do not hand-edit `uv.lock`.

**API:**
```python
@dataclass(frozen=True)
class TimeResolutionError:
    expr: str
    reason: str  # never silently guesses; typed error, not a wrong datetime

def resolve_time(
    expr: str,
    *,
    base: datetime | None = None,   # defaults to datetime.now(UTC)
    tz: str = "UTC",                # IANA name; validated via zoneinfo.ZoneInfo
) -> datetime | TimeResolutionError: ...

def resolve_duration(expr: str) -> timedelta | TimeResolutionError: ...
```

**Fail-safe contract (binding):** `resolve_time`/`resolve_duration` **never**
raise on bad input and **never** return a best-guess wrong time. Wrap
`dateparser.parse(expr, settings={...})` in a narrow `try/except Exception`;
a `None` return from dateparser (its own "couldn't parse" signal) **and** any
exception both become `TimeResolutionError(expr, reason=...)`. This mirrors
the existing fail-closed convention in `_next_cron_dt`
(`event_loop/scheduler.py:74-75`, raising `ValueError` on an unknown
timezone) but returns a typed value instead of raising, because this
function is called directly from model-facing tool code where an exception
would abort the tool loop rather than hand the model a recoverable error to
react to (same rationale as `mcp/builtins.py`'s `{"error": ...}` dict
returns, e.g. `:171-178`, `:205-206`).

**dateparser settings:** `PREFER_DATES_FROM: "future"` (an agent saying
"Tuesday at 9am" almost always means the next one, not the last one),
`RETURN_AS_TIMEZONE_AWARE: True`, `TIMEZONE: tz`, `RELATIVE_BASE: base`. For
compound relative expressions dateparser's `dateparser.search` /
its built-in relative-date parser already handles "5 weeks and 3 hours from
today" and "in 90 minutes" natively (verify at implementation time with a
literal parse of that exact string — pin the expected datetime in the test
per the test plan below). "End of Q3" is **not** natively handled by
dateparser's relative parser — add a small pre-processing table in
`resolver.py` for a fixed set of business-calendar idioms
(`"end of q[1-4]"`, `"start of q[1-4]"`, `"end of month"`, `"end of year"`)
that computes the boundary directly and only falls through to
`dateparser.parse` when no idiom matches — document the idiom list in the
module docstring so it's discoverable, and return `TimeResolutionError` for
an unrecognized idiom-shaped-but-unmatched string rather than silently
falling through to a nonsensical dateparser guess.

**Exposure:**
1. **MCP tool** — new `RESOLVE_TIME_TOOL = MCPTool(name="resolve_time", ...)`
   in `mcp/builtins.py`, input_schema `{expr: str, base: str|null,
   tz: str default "UTC"}`, dispatched from
   `BuiltinToolHandler.__call__` (add a branch at `mcp/builtins.py:113-118`
   alongside the existing two) calling `timekit.resolve_time` and returning
   either `{"resolved_at": iso, "tz": tz}` or `{"error": reason, "expr": expr}`
   — same shape convention as `_run_project_check`/`_web_retrieve`.
2. **Ansible module** — new `gludd_time.py` (mirrors `gludd_message.py`'s
   `GluddClient` HTTP-wrapper skeleton) calling a new
   `POST /api/time/resolve` endpoint (thin FastAPI wrapper around
   `timekit.resolve_time`, registered in a new `routers/time.py` alongside
   the existing `routers/messages.py`/`routers/human_todos.py` pattern) so
   Ansible roles can compose time resolution declaratively without shelling
   out to Python. `register_facts={"gludd_resolved_time": ...}` on success.

**Tests:** `tests/unit/test_timekit_resolver.py` — exact-string pins for
"5 weeks and 3 hours from today", "next Tuesday at 9am", "in 90 minutes",
"end of Q3" (each against a frozen `base`); unparseable input (e.g. "banana")
→ `TimeResolutionError`, never a raised exception, never a datetime; unknown
timezone → `TimeResolutionError` (mirrors `_next_cron_dt`'s `ValueError`
path); `resolve_duration` round-trips against `resolve_time(base=X) - X`.

---

### T-2 — Timer entity, reusing the scheduler machinery

**New:** `db/models.py` `TimerModel` (new table `timers`), alembic migration
`028_add_timers.py` (down_revision `"027"` per the ordering note above, or
current tip at implementation time — re-verify), `db/repository.py`
`TimerRepository`.

**Columns (mirrors the `TodoModel` scheduling-column shape,
`db/models.py:227-248`, so the same due-lookup index pattern applies):**
```text
timer_id        str PK (f"TIMER-{uuid4().hex[:8].upper()}")
fire_at         DateTime(timezone=True), NOT NULL   -- resolved via timekit
scope_type      String(16), NOT NULL   -- global|project|task|agent|role|event_type
scope_id        String(128), NULLABLE  -- required for all scope_type except global
message         Text, NOT NULL         -- notification body/payload (JSON string)
event_type      String(64), default "timer_fired"  -- the EventType the fire emits
recurring_cron  String(256), NULLABLE  -- 5-field cron; presence = recurring TEMPLATE
schedule_timezone String(64), default "UTC"
next_fire_at    DateTime(timezone=True), NULLABLE   -- advanced on each recurring fire
run_count       Integer, default 0
max_runs        Integer, NULLABLE
status          String(16), default "pending"  -- pending|fired|cancelled
created_by      String(128), NOT NULL           -- agent/human id that set it
created_at / updated_at  (mirrors _utcnow default/onupdate pattern used throughout db/models.py)
```
Index `ix_timers_due_lookup` on `(status, next_fire_at, fire_at)` — same
shape as `ix_todos_scheduled_lookup` (`db/models.py:256-262`) for the same
reason (the due-timer query filters exactly these columns).

**Reuse, don't duplicate, the scheduler:** rather than a parallel cron
engine, `TimerRepository` calls the **same** `_next_cron_dt` helper from
`event_loop/scheduler.py:62-85` for `recurring_cron` advancement — export it
(drop the leading underscore or add a thin public wrapper) so both
`TodoScheduler` and the new `TimerScheduler` share one croniter/zoneinfo
implementation instead of two. Concretely:
- Add `TimerScheduler` (new `event_loop/timer_scheduler.py`, same shape as
  `TodoScheduler` in `event_loop/scheduler.py:88-234`): `tick(now)` finds due
  timers (`status="pending" AND COALESCE(next_fire_at, fire_at) <= now`,
  matching the `TodoRepository.list_due_scheduled` due-predicate style at
  `db/repository.py:395-405`), for each:
  - **One-shot** (`recurring_cron is None`): publish the scoped notification
    (T-3), set `status="fired"`. One-shot analog of
    `scheduler.py:134-153`'s promote-and-done.
  - **Recurring** (`recurring_cron` set): advance-then-fire ordering
    identical to `scheduler.py:180-232`'s "advance first, so a crash yields a
    missed occurrence not a duplicate fire" invariant — update
    `next_fire_at`/`run_count`/`last_run_at`-equivalent **before** publishing
    the notification; retire to `status="cancelled"` at `max_runs` exactly
    like `scheduler.py:159-178`.
- Wire `TimerScheduler.tick()` into `event_loop/loop.py` as a **new phase**
  `"fire_timers"`, inserted immediately after `"run_scheduler"` (currently
  index 5, `event_loop/loop.py:94`) — timers are the same "temporal
  promotion" family as cron-todos, and firing before `claim_runnable_todos`
  means a timer whose payload enqueues a todo (see below) is claimable the
  same tick, exactly the invariant `test_scheduler_wiring.py:25-26` already
  encodes for `run_scheduler`. `PHASE_ORDER` goes **17→18** (or 18→19 if
  C-SPD1's `flush_spend_ledger` insertion has already landed — check
  `event_loop/loop.py:88-106` at implementation time and update whichever
  phase-count assertions are current: `test_obj04_event_loop.py:31`,
  `test_event_loop.py:533-534`, `test_audit_gaps_e2e.py:52`,
  `test_event_loop_session_per_tick.py:44-46` — same four call sites C-SPD1
  already identified).
- **Timer payload can optionally create a todo**, not just a notification: a
  `payload_todo` field (JSON `dict` matching `AddTodoRequest`,
  `routers/todos.py:38-46`) lets "set a timer that files a todo when it
  fires" compose with the existing todo-creation path — `TimerScheduler`
  calls `TodoRepository.create` the same way `TodoScheduler._build_child_data`
  does (`scheduler.py:237-253`), just from a Timer template instead of a
  Todo template. This is optional; the default behavior is notification-only.

**Why a new table instead of overloading `TodoModel`:** a Timer is not work
to execute (no `assigned_agent`, no worktree, no test_commands) — coercing it
into `TodoModel`'s 13+ execution columns (per C-TODOMODEL's blob-cap audit,
`WAVE_C_DESIGNS_2026-07-10.md:458-463`) would bloat every timer row with
unused columns and complicate the due-lookup index with irrelevant
predicates. A separate lightweight table with its own due-index is the
smaller, more honest schema — while still sharing the *code* (croniter
helper, advance-then-fire ordering, phase-insertion pattern) with the todo
scheduler, per the task's "reuse the existing scheduler/cron machinery where
possible" directive.

**MCP tool + Ansible module:** `SET_TIMER_TOOL` in `mcp/builtins.py`
(`fire_at` OR `duration_expr`, `scope_type`, `scope_id`, `message`,
`recurring_cron` optional) — internally calls `timekit.resolve_time`/
`resolve_duration` first (T-1) if given an expression rather than an ISO
datetime, so the model can say `set_timer(duration_expr="in 90 minutes",
scope_type="task", scope_id="TODO-001", message="check on this")` in one
call. New `gludd_timer.py` Ansible module (state=present/list/cancel,
mirrors `gludd_message.py`'s send/receive/ack three-state shape) wrapping new
`POST /api/timers`, `GET /api/timers`, `DELETE /api/timers/{id}` endpoints in
a new `routers/timers.py`.

**Tests:** `tests/unit/test_timer_scheduler.py` (mirrors
`test_todo_scheduler.py`'s structure) — one-shot timer fires exactly once and
transitions to `fired` (never re-fires on a second `tick()`); recurring timer
advances `next_fire_at` + increments `run_count` + fires again next due tick;
`max_runs` retires to `cancelled`; a crash between advance and fire (mocked)
yields a missed occurrence not a duplicate (mirrors
`test_todo_scheduler.py:228`'s regression coverage); `payload_todo` creates a
todo on fire. `tests/integration/test_timer_scheduler_integration.py`
(mirrors `test_todo_scheduler_integration.py`) — real DB round-trip.
`tests/unit/test_event_loop.py` phase-order + phase-count updates per above.

---

### T-3 — Scoped notification routing (`ScopedNotifier`)

**New:** `src/general_ludd/events/scoped_notifier.py`.

**Scope tuple:** `(scope_type, scope_id)` where
`scope_type ∈ {global, project, task, agent, role, event_type}`. `scope_id`
is required for every type except `global`; for `task` it is a `todo_id`
(agent-todo) or `human_todo_id`; for `agent`/`role` it is the same string
space `AgentMessageModel.recipient` already uses (per the finding above that
"role" and "agent" are naming-convention-distinguished, not
structurally-distinguished — `ScopedNotifier` doesn't need to invent a new
identity system, it reuses the existing recipient string).

**"Who should know" — precise routing table (binding spec):**

| scope_type | agents reached | humans reached |
|---|---|---|
| `global` | every agent (broadcast recipient, `AgentMessageModel.recipient=BROADCAST_RECIPIENT`) | every open human-todo inbox (unfiltered `HumanTodoRepository.list_open()`) |
| `project` | every agent with a `TodoModel.assigned_agent` for that `project_id` (query `TodoRepository`, distinct `assigned_agent` where `project_id=scope_id`), **plus** an `AgentMessageModel` row with that `project_id` set (reaches anyone who later calls `inbox(project_id=X)`, `db/repository.py:1456-1462`) | any human-todo whose `parent_agent_todo_id` resolves to a `TodoModel` with that `project_id` (the only join path today, given `HumanTodoModel` has no `project_id` column — see the flagged schema gap below) |
| `task` | the todo's `assigned_agent` (if set) + any agent that filed an `AgentMessageModel`/watch on that `todo_id` (see watcher note below) | the human who owns the corresponding `HumanTodoModel` (i.e. `agent_id` is who *filed* it, but the notification also reaches whoever is polling `GET /api/human-todos?...` for that id — practically: file a fresh human-todo row referencing it, or extend the existing row if still open) |
| `agent` | exactly that one recipient string | n/a (agents don't have "their own" human) |
| `role` | every live agent addressed by that recipient string (a role in this codebase already fans out to every process polling that inbox — no new fan-out needed) | n/a |
| `event_type` | every agent subscribed via `EventBus.subscribe(event_type, cb)` or `HookSystem.register_callback/register_webhook(event_type, ...)` | n/a — event_type is an internal-wiring scope, not a human-facing one |

**Watcher note:** there is no existing "watch a todo" table. Until one
exists, `task` scope's agent fan-out is best-effort: the assigned agent
always gets it; anything calling itself a "watcher" today would have to be
doing so via its own `AgentMessageModel` polling loop scoped by
`project_id` (fallback to `project` scope). Flag this as a **known
limitation**, not a blocker — do not invent a watcher table as part of this
design; it's out of scope creep beyond what T-1/T-2/T-3 need.

**Schema gap to close as part of this work (required, not optional):**
`HumanTodoModel` (`db/models.py:842-892`) has no `project_id` column, so
`project`-scoped human routing can only reach a human-todo indirectly via
`parent_agent_todo_id`. Add `project_id: Mapped[str | None]` (nullable FK to
`projects.project_id`, `ondelete="SET NULL"`, indexed — mirrors
`AgentMessageModel.project_id` at `db/models.py:602-607` exactly) in the same
migration batch as T-2's `timers` table (one migration, two additive
changes — matches the `HumanTodoModel`/`AgentMessageModel` "additive table,
no heavy migration" precedent noted at `db/models.py:906-909`). Backfill: NULL
for existing rows (safe — NULL already means "no project" throughout this
schema's convention). Update `CreateHumanTodoRequest`
(`routers/human_todos.py:69-78`) to accept an optional `project_id`, and
`HumanTodoRepository.create` (`db/repository.py:1965-2002`) to persist it.

**Delivery implementation — `ScopedNotifier.notify(scope_type, scope_id,
message, *, event_type="timer_fired")`:**
1. Resolve the agent/human/webhook targets per the table above.
2. **Agents:** one `AgentMessageRepository.send(...)` call per resolved
   recipient (or a single `recipient=BROADCAST_RECIPIENT` call for
   `global`/`role`-that-is-a-shared-inbox), reusing
   `db/repository.py:1417-1427` — zero new agent-delivery code, just new
   *targeting* logic upstream of the existing `send`.
3. **Humans:** one `HumanTodoRepository.create(..., category="notification")`
   per resolved human-todo-worthy scope (or update-in-place for `task` scope
   when a live human-todo already exists for that parent, avoiding a stream
   of duplicate rows for a recurring timer — check `HumanTodoRepository`
   for an existing open row with the same `parent_agent_todo_id` +
   `category="notification"` first).
   `category="notification"` is a **new** value to add to
   `HUMAN_TODO_CATEGORIES` (`db/repository.py`, alongside
   `permission_escalation`/`external_action`/`decision`/`input_request`/
   `blocker`) — a notification is not a request that blocks on a human
   response, so its `status` should default straight to a
   non-blocking-read state; do not set `parent_agent_todo_id` unless the
   caller explicitly wants the FYI to gate a parent todo (rare).
4. **Webhooks/external sinks:** publish an `Event` via `EventBus.publish`
   (`events/bus.py:37-87`) using `event_type` as the routing key AND fire any
   registered `HookSystem` webhook for that same `event_type`
   (`events/hooks.py:130-159`) — this is the one place scope really does
   collapse to the existing flat `event_name`-keyed mechanisms, because
   webhook registration is inherently "subscribe to this event_type", which
   **is** one of the six scope types. No new webhook-registration API is
   needed; `event_type` scope *is* `HookSystem.register_webhook`.
5. Extend `Event` (`events/types.py:30-37`) with three **optional** fields —
   `project_id: str | None`, `task_id: str | None`, `agent_id: str | None`
   (all default `None`, so every existing `Event`/`*Event` subclass
   constructor at `events/types.py:40-184` keeps working unchanged) — so a
   published notification event carries its scope for any future subscriber
   that wants to self-filter, without forcing `EventBus.subscribe` itself to
   change signature. `EventBus.subscribe`'s per-type dict (`:17`,
   `:23-28`) is left alone; **do not** add a scope filter parameter to
   `subscribe()` itself — that would require every existing call site
   (currently just `loop.py:418`) to be touched for no behavioral gain, since
   `ScopedNotifier` already does the scope resolution before ever touching
   `EventBus`. (This directly answers the "extend `subscribe()` to accept a
   scope filter, or add a layer on top" question in the brief: **layer on
   top**, because `EventBus` has exactly one subscriber today and the
   agent/human targets are resolved from SQL, not from bus subscriptions.)

**Flood cap (binding):** `ScopedNotifier` enforces
`max_timers_per_scope`/`max_notifications_per_scope_per_hour` (T-5 config) by
counting existing `pending` `TimerModel` rows for the same `(scope_type,
scope_id)` before insert (`TimerRepository.count_pending_for_scope`) — refuse
with a typed error past the cap rather than silently dropping or silently
allowing (fail-closed, matching the project's "never a silent skip" house
style, e.g. `_contain_workspace`'s explicit `None`-then-error convention in
`mcp/builtins.py:137-153`).

**Tests:** `tests/unit/test_scoped_notifier.py` — global reaches
broadcast-agent-inbox + every open human-todo; project-scoped reaches only
that project's assigned agents + (post-migration) that project's human-todos,
verified NOT reaching a second project's agents/humans (two-project fixture,
assert zero cross-contamination — same shape as the existing
`test_messages_fallback_project_isolation.py` cross-project isolation test);
agent-scoped reaches exactly one recipient; role-scoped reaches the shared
role inbox; event_type-scoped fires the registered webhook (reuse
`test_hooks_ssrf.py`'s harness) and does NOT touch agent/human tables; flood
cap enforced (Nth timer past the per-scope cap refused with a typed error,
N-1th still succeeds).

---

### T-4 — Ansible role surface

**New role:** `roles/set_timer_and_notify/` — declarative wrapper composing
T-1 (resolve) → T-2 (create timer) → (on fire, driven by the daemon phase,
not the role itself) T-3 (scoped notify). Role tasks:
1. `general_ludd.agent.gludd_time` (state=resolve) — resolve a
   `time_expression` var to a concrete `fire_at`, fail the play if
   `TimeResolutionError` comes back (surface `reason` in the failure message).
2. `general_ludd.agent.gludd_timer` (state=present) — create the timer with
   the resolved `fire_at`, `scope_type`/`scope_id`/`message` role vars.
3. Register the created `timer_id` as a fact
   (`ansible_facts.gludd_timer_id`) so a calling playbook can later cancel it.

**New capability role:** `roles/timekeeper/` — a thin capability marker role
(mirrors how other capability roles in `roles/` are structured, e.g.
`budget_guard`/`agent_task`) granting an agent the permission surface to call
`gludd_time`/`gludd_timer` — this is the "a `timekeeper` capability role" the
brief asks for, kept separate from `set_timer_and_notify` so a playbook can
compose "this agent may manage time" (capability) independently of "run this
specific set-a-timer-and-notify workflow" (the task role).

**Tests:** `molecule/roles/set_timer_and_notify/` scenario (mirrors existing
role-molecule scaffolding under `molecule/roles/`) — asserts the role
resolves a relative expression, creates a timer, and (with a fast
`fire_at` in the test) that a subsequent daemon tick fires the scoped
notification end-to-end. Ansible-lint clean (`make ansible-lint-playbooks`).

---

### T-5 — Config schema + consolidated test plan

**New config keys** (daemon config snapshot, same `config.get(key, default)`
convention used throughout `src/general_ludd/connectors/*.py` and proposed
for the C-SPD1/C-EVENTLOOP Wave-C items):

```yaml
timekit:
  default_timezone: "UTC"           # base tz when a caller doesn't specify one
timers:
  max_per_scope: 50                 # pending-timer cap per (scope_type, scope_id)
  max_notifications_per_scope_per_hour: 200   # ScopedNotifier flood cap
  retention_days: 30                # fired/cancelled TimerModel rows purged after N days
  tick_interval_ticks: 1            # fire_timers phase runs every tick (parity with run_scheduler)
```

**Retention:** a periodic purge (mirrors
`AgentMessageRepository.purge_expired`, `db/repository.py:1504-1527`'s
single-set-based-DELETE style) removing `TimerModel` rows in `fired`/
`cancelled` status older than `retention_days` — wire it as a low-frequency
daemon phase or piggyback on an existing periodic-cleanup phase; do not add
per-tick overhead for a purge that only needs to run hourly-ish.

**Consolidated test plan (acceptance criteria for the whole feature):**
1. `resolve_time("5 weeks and 3 hours from today", base=<frozen>)` → exact
   pinned datetime (T-1).
2. A one-shot timer fires exactly once; a second `tick()` after firing does
   not re-fire it (`status="fired"` guards re-selection) (T-2).
3. A recurring timer advances `next_fire_at` and fires again on the next due
   tick, `run_count` incremented each time, retired at `max_runs` (T-2).
4. A `project`-scoped notification reaches all agents assigned to that
   project (via `TodoModel.assigned_agent`) and all humans with a human-todo
   whose parent resolves to that project (post-migration: also any
   human-todo with `project_id` set directly) — and provably does **not**
   reach a second project's agents or humans (T-3).
5. An `agent`-scoped notification reaches only that one agent's inbox (T-3).
6. Unparseable time expression → `TimeResolutionError`, never a raised
   exception, never a silently-wrong datetime (T-1).
7. Timer-flood cap: the (N+1)th pending timer for the same scope is refused
   with a typed error; the Nth still succeeds (T-3/T-5).

---

### T-6 — Time & calendar knowledge layer

The model must not carry date math in its head. It expresses **intent**
("sunrise in Tokyo next Friday", "3 business days from today excluding US
holidays", "every last Friday of the quarter") and this layer resolves it.
All tools live in `general_ludd/timekit/` next to T-1's `resolver.py`
(new submodules: `tz.py`, `calendars.py`, `holidays.py`, `celestial.py`,
`recur.py`, `humanize.py`), each exposed as (a) an MCP tool branch in
`mcp/builtins.py:113-118 BuiltinToolHandler.__call__` and (b) an Ansible
verb on the new `gludd_time` module (T-1) — a single module with a `state`
switch (`resolve|convert_tz|business_days|fiscal_quarter|iso_week|is_holiday|
next_business_day|sun_times|moon_phase|next_equinox|next_occurrences|
humanize|time_until`) mirroring `gludd_message.py`'s multi-state shape, so
one module covers the whole knowledge surface rather than a dozen modules.
Every tool is **fail-closed → typed error** exactly like `resolve_time`
(T-1): a bad region/tz/lat-lon/rrule returns `{"error": reason}`, never a
raised exception or a silently-wrong value.

**New pyproject deps to add** (after `dateparser`, `pyproject.toml:61`; each
with a one-line rationale comment; add matching `[[tool.mypy.overrides]]`
`ignore_missing_imports` entries alongside `croniter.*` at
`pyproject.toml:174` — none ship `py.typed`; `make relock` after):
`tzdata` (bundled IANA db — required for Windows/frozen PyInstaller builds
where the OS zoneinfo is absent; `dist`/`build-executable` targets),
`holidays`, `workalendar`, `astral`, `python-dateutil` (rrule +
relativedelta), `humanize`. **Optional extra** `skyfield` under a new
`[project.optional-dependencies] astronomy` group (heavy — pulls `numpy` + a
BSP ephemeris data file; lazy-import only, mirroring the `aws`/`gcp` optional
groups at `pyproject.toml:92-108`, so the core package never hard-depends on
it). `convertdate` (non-Gregorian) also goes in `astronomy` (optional).

**1. TIME ZONES** — `zoneinfo` (stdlib) + `tzdata` (bundled db). Module
`timekit/tz.py`.
- `convert_tz(dt, to_tz)` → same instant in `to_tz`; `local_now(tz)` →
  current wall-clock in `tz`. Both validate the IANA name via
  `ZoneInfo(...)` (raises → typed error), reusing the exact validation
  `_next_cron_dt` already does at `event_loop/scheduler.py:73-75`.
- **DST + ambiguous/nonexistent local times:** a "spring-forward" gap time
  (02:30 on a US DST-start date) and a "fall-back" fold time are the two
  hazard cases. Use `datetime.fold` semantics (PEP 495) — for a nonexistent
  local time return a typed error naming the gap (never silently roll it
  forward to a wrong instant); for an ambiguous fold time default to
  `fold=0` (earlier offset) but surface both candidates in the tool result
  so the model can disambiguate. This is the presentation-layer analog of
  the DST correctness the cron scheduler already guards
  (`event_loop/scheduler.py:77-85`, converting to local before croniter
  precisely so DST-crossing schedules fire at the right wall-clock time).
- **Default-tz precedence (binding):** per-agent tz > per-project tz >
  `timekit.default_timezone` global (T-5). Per-project tz reuses the same
  scope-id space as T-3's `project` scope (a project's configured tz lives on
  its config dict, `ProjectManager` at `projects/manager.py:211`); per-agent
  tz is an agent config field. Storage rule (binding, matches existing
  codebase convention — every `DateTime(timezone=True)` column stores UTC,
  e.g. `db/models.py:221-225`, `TimerModel.fire_at` in T-2): **UTC-canonical
  at rest, tz applied only at presentation.** `TimerModel.fire_at` and every
  DB datetime stay UTC; tz is a display concern resolved at the notification
  boundary, never persisted into the instant.
- **Fan-out across tz's:** when a scoped notification (T-3) reaches an
  audience spanning multiple tz's, `ScopedNotifier` renders the fire time
  **per recipient** in that recipient's resolved default tz (precedence
  above) — the human-todo body / agent message for a Tokyo agent shows JST,
  for a NY agent EST, computed from the one canonical UTC `fire_at`. Add a
  `local_time_for_audience(utc_dt, recipient)` helper in `tz.py` that
  `ScopedNotifier.notify` calls once per resolved target.

**2. CALENDARS** — `workalendar` (business-day math) + stdlib `date.isocalendar`.
Module `timekit/calendars.py`.
- `business_days_from(date, n, region)` → the date `n` working days ahead
  (skip weekends **and** region holidays — delegates to `workalendar`'s
  `add_working_days`, which folds in the holiday sets of T-6.3);
  `iso_week(date)` → `(iso_year, iso_week, iso_weekday)` via
  `date.isocalendar()` (stdlib, no dep); `fiscal_quarter(date, fy_start)` →
  `(fy_year, quarter)` for a **configurable** fiscal-year start month
  (`fy_start` default 1 = calendar year; e.g. `fy_start=10` for a US-federal
  Oct-start FY). "End of Q3" from T-1's idiom table routes through this so
  the fiscal-vs-calendar quarter distinction is honored (a plain-language
  "Q3" resolves against the configured `fy_start`, not a hardcoded
  Jul-Sep).
- **ISO-8601 everywhere:** all tool datetime I/O is ISO-8601 strings (parse +
  emit), and ISO week numbering (weeks start Monday, week 1 contains the
  first Thursday) is the canonical week semantics.
- **Non-Gregorian awareness:** `convertdate` (optional `astronomy` group)
  exposes Julian/Hijri/Hebrew and lunar conversions behind a
  `convert_calendar(date, system)` verb — lazy-imported, returns a typed
  "astronomy extra not installed" error if absent (fail-closed, mirrors the
  `[sandbox]` extra's lazy-import-or-fail-open note at
  `pyproject.toml:115-132`).

**3. HOLIDAYS** — `holidays` lib (per-country + subdivision) +
`workalendar` for the business-day-skip integration. Module
`timekit/holidays.py`.
- `is_holiday(date, region)` → bool + holiday name; `next_business_day(date,
  region)` → next non-weekend, non-holiday date. `region` is a
  country[/subdivision] code (e.g. `US`, `US-CA`, `DE-BY`).
- **Operator-configurable custom/company holidays:** T-5 config
  `holidays.custom: [{date, name, region?}]` merged into the `holidays` lib's
  set at load (the lib supports `.append({...})`), so a company shutdown week
  counts as non-working. Active holiday sets are config, not hardcoded:
  `holidays.regions: ["US", "US-CA"]` (T-5).
- **Holiday-aware SLAs:** "due in 3 business days" (a deadline the model or a
  human-todo sets) resolves through `business_days_from` so it respects both
  weekends and the active holiday sets — a Timer's `fire_at` (T-2) or a
  human-todo `due_at` (`db/models.py:885`) can be set from a business-day SLA
  expression rather than a raw calendar delta. Ties directly into T-2:
  `set_timer(duration_expr="3 business days from now", region="US-CA", ...)`.

**4. CELESTIAL** — `astral` (sun/moon, lightweight) + optional `skyfield`
(deep astronomy). Module `timekit/celestial.py`.
- `sun_times(lat, lon, date)` → dawn/sunrise/noon/sunset/dusk + golden-hour
  window (astral's `sun()` + `golden_hour()`); `moon_phase(date)` → phase
  angle + named phase (astral's `moon.phase()`). Both need a **location** —
  resolved from per-project/per-agent configured lat/lon (T-5
  `timekit.locations`), or geocoded from a place name (a `geocode(place)`
  verb; if we avoid a geocoding dep/network call, require explicit lat/lon
  and return a typed error prompting for coordinates — fail-closed, no silent
  wrong location). "Sunrise in Tokyo next Friday" = T-1 resolves "next
  Friday" → T-6.1 resolves Tokyo's tz + configured lat/lon → `sun_times`.
- **Deep astronomy (optional `astronomy` extra):** `skyfield` (or `ephem`)
  backs `next_equinox()` / `next_solstice()`, planetary positions, eclipse
  and satellite-pass prediction — lazy-imported, typed "extra not installed"
  error when absent. Keep `astral` in core (small, pure-Python) and gate only
  the `skyfield` ephemeris-heavy surface behind the extra.

**5. OTHER** — module `timekit/recur.py` + `timekit/humanize.py`.
- **Recurrence rules** — `python-dateutil` `rrule`:
  `next_occurrences(rrule, count)` → the next `count` datetimes of an rrule
  ("every 2nd Tuesday", "last Friday of month", "every last Friday of the
  quarter"). **Ties into T-2:** the recurring-Timer design currently uses a
  5-field cron (`TimerModel.recurring_cron`), which **cannot** express
  "last Friday of the month" (cron has no nth-weekday-of-month operator in the
  croniter 5-field grammar). Add an **alternative** `recurring_rrule:
  String` column on `TimerModel` (T-2) — a timer is recurring if EITHER
  `recurring_cron` OR `recurring_rrule` is set (mutually exclusive; validate
  exactly one). `TimerScheduler` advances an rrule timer via
  `rrule.after(now)` instead of `_next_cron_dt`, same advance-then-fire
  ordering — this is the rrule analog of the cron advancement at
  `event_loop/scheduler.py:187`. The model expresses the human phrase; the
  layer compiles it to an rrule (dateutil's `rrulestr` / a small
  phrase→rrule table for the common cases).
- **Humanized/relative** — `humanize`: `humanize_delta(dt)` → "3 hours ago" /
  "in 2 days" (naturaltime); used to render Timer countdowns and notification
  bodies in human-friendly form. `time_until(dt)` → the `timedelta` to a
  future instant **plus** its humanized string — the SLA/deadline countdown
  primitive.
- **Concepts the layer encodes so the model needn't:** durations
  (`timedelta`) vs instants (`datetime`) vs intervals (a `(start, end)`
  pair) are distinct return types, never conflated; deadline/SLA countdowns
  are `time_until` + business-day awareness (T-6.3); leap-year / epoch-range
  caveats (year 9999 ceiling, pre-1970 negative epochs) are validated at the
  resolver boundary and returned as typed errors rather than overflowing.
- **Monotonic-vs-wall-clock (binding invariant, cross-ref):** everything in
  T-1/T-2/T-6 that names a *calendar instant* — `TimerModel.fire_at`,
  `next_fire_at`, `due_at`, every resolved datetime — MUST use **wall-clock**
  time (`datetime.now(UTC)`), because those instants must survive process
  restarts and be comparable across the daemon's ticks. This is the OPPOSITE
  of stall/duration detection, which MUST use **monotonic** time
  (`time.monotonic()`, immune to NTP steps and DST) — see the existing
  monotonic users `coordination/file_overlap.py:311+`,
  `git_automation/locking.py:196`, `renderers/runner.py:190+` and the
  `observability/timing.py` DurationTracker/StallWatchdog family. Never
  compute a `fire_at` from a monotonic base or a stall deadline from a
  wall-clock base; the T-6 tools return wall-clock instants exclusively, and
  a doc-comment in `timekit/__init__.py` states this split so an implementer
  doesn't reach for `monotonic` when resolving a future calendar time.

**Tests (append to the timekit suite):** `test_timekit_tz.py` —
convert_tz round-trip; nonexistent spring-forward local → typed error;
ambiguous fall-back fold → both candidates surfaced; per-recipient fan-out
renders two tz's from one UTC instant. `test_timekit_calendars.py` —
business_days_from skips a weekend; fiscal_quarter honors `fy_start=10`;
iso_week matches stdlib `isocalendar`. `test_timekit_holidays.py` —
is_holiday(US July 4) true; next_business_day skips a holiday+weekend; a
custom company holiday counts. `test_timekit_celestial.py` — sun_times for a
known lat/lon/date matches astral within tolerance; missing location → typed
error; skyfield-absent → typed "extra not installed". `test_timekit_recur.py`
— next_occurrences("last Friday of month", 3) correct; an rrule Timer
advances via `rrule.after` (unit, mirrors `test_timer_scheduler.py`);
cron+rrule both-set rejected. `test_timekit_humanize.py` — humanize_delta /
time_until strings. A monotonic-vs-wallclock guard test asserting
`resolve_time`/timer `fire_at` are wall-clock (not derived from
`time.monotonic`).
