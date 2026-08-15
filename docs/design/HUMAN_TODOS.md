# Human-Todo System (Bot→Human Requests)

This document describes the `HumanTodo` system — the structured channel by
which an agent asks a human for something it cannot get on its own. It covers
the data model, lifecycle, HTTP API, CLI, Ansible module, categories, and the
security model. The reference implementations are
[`HumanTodoModel`](../../src/general_ludd/db/models.py),
[`HumanTodoRepository`](../../src/general_ludd/db/repository.py),
[`routers/human_todos.py`](../../src/general_ludd/routers/human_todos.py),
[`cli_human_todos.py`](../../src/general_ludd/cli_human_todos.py), and the
[`gludd_human_todo`](../../collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_human_todo.py)
Ansible module.

## 1. Purpose & scope

A `HumanTodo` is **a request filed BY an agent AGAINST a human**. The agent
hits a wall it cannot break through programmatically — a missing permission,
an external action it cannot perform, a decision it cannot make, missing
input — and instead of writing a log line and silently failing, it files a
structured record that the human can see, triage, and resolve.

### What a HumanTodo IS

- Filed by an agent (via `POST /api/human-todos` or the `gludd_human_todo`
  Ansible module) when work cannot continue without a human action.
- Resolved by a human (via `PATCH` or the `gludd human-todo` CLI) with a
  free-text `human_resolution` that is fed back to the agent.
- Optionally linked to a parent agent-todo via `parent_agent_todo_id`; when
  linked, the parent is moved to `blocked_on_human` so the event loop stops
  dispatching it until the human resolves the request.

### What a HumanTodo is NOT

| Concept | Distinction |
|---|---|
| `TodoModel` (agent todo) | A `TodoModel` is work a **user assigns to an agent**. A `HumanTodo` is work an **agent asks of a human**. The two link via `parent_agent_todo_id` but live in separate tables with separate state machines. |
| Event log | The event log records system occurrences (dispatches, results, errors). A `HumanTodo` is a **request** — an outstanding item requiring action, not a record of something that happened. |
| Audit log | The audit log records security decisions (permission grants, denials, sandbox applications). A `HumanTodo` with `category=permission_escalation` may *trigger* an audit entry, but the audit log is the security trail; the `HumanTodo` is the workflow item. |

The rule of thumb: a log line is observation; a `HumanTodo` is a **request**.

## 2. Lifecycle

```text
        ┌──────┐
 create│ open │────────────────────────┐
        └──┬───┘                       │
           │ mark_in_progress          │ dismiss / supersede
           ▼                           ▼
    ┌─────────────┐               ┌──────────┐
    │ in_progress │               │dismissed │ (terminal)
    └─────┬───────┘               │superseded│ (terminal)
          │                       └──────────┘
          │ mark_done
          ▼
      ┌──────┐
      │ done │ (terminal)
      └──────┘
```

State transitions are validated by `_HUMAN_TODO_TRANSITIONS` in
`repository.py`:

| From          | Allowed targets                                  |
|---------------|--------------------------------------------------|
| `open`        | `in_progress`, `done`, `dismissed`, `superseded` |
| `in_progress` | `done`, `dismissed`, `superseded`, `open`        |
| `done`        | (terminal — no outgoing transitions)             |
| `dismissed`   | (terminal)                                       |
| `superseded`  | (terminal)                                       |

### Parent agent-todo integration

The link between a `HumanTodo` and its (optional) parent agent-todo is the
**blocking integration**. It is opt-in: a `HumanTodo` filed without
`parent_agent_todo_id` is just a logged need (no parent is blocked).

When `parent_agent_todo_id` is set:

1. **On file** (`POST`): the parent agent-todo transitions
   `queued → blocked_on_human` (or `active → blocked_on_human`). The event
   loop's claimer skips `blocked_on_human` todos, so the agent stops
   dispatching the blocked work. See
   [`TodoStatus.BLOCKED_ON_HUMAN`](../../src/general_ludd/schemas/todo.py).

2. **On resolve** (`PATCH` to `done` or `dismissed`): the parent agent-todo
   transitions back:

   | HumanTodo resolved as | Parent moves to | Meaning |
   |---|---|---|
   | `done` | `queued` | Resume — the human provided what was asked. The `human_resolution` text is delivered as `human_input` on the next dispatch. |
   | `dismissed` | `cancelled` | The human declined; the agent should try a different approach. |

The parent transition is **non-fatal**: if it cannot be applied (e.g. the
parent is already terminal), the router logs a warning and the `HumanTodo`
itself is still filed/resolved. The agent's request is never lost.

## 3. Data model

Table: `human_todos` (defined in
[`HumanTodoModel`](../../src/general_ludd/db/models.py)).

| Column                 | Type                      | Notes                                                        |
|------------------------|---------------------------|--------------------------------------------------------------|
| `id`                   | `String(32)` PK           | Format `HTODO-<10 hex>`. Generated by `_gen_human_todo_id`.  |
| `parent_agent_todo_id` | `String(32)`, nullable    | Links to `TodoModel.todo_id`. Indexed.                       |
| `agent_id`             | `String(128)`, not null   | The filing agent. Indexed.                                   |
| `session_id`           | `String(128)`, nullable   | Optional session correlation. Indexed.                       |
| `title`                | `String(512)`, not null   | Short summary.                                               |
| `body`                 | `Text`, not null          | Full markdown context.                                       |
| `category`             | `String(32)`, not null    | See §7. Indexed.                                             |
| `priority`             | `String(16)`, default `medium` | `low` / `medium` / `high` / `urgent`. Indexed.          |
| `status`               | `String(16)`, default `open` | See §2. Indexed.                                          |
| `human_resolution`     | `Text`, nullable          | Set by the human on `done`/`dismissed`.                      |
| `human_resolver`       | `String(128)`, nullable   | Who resolved it.                                             |
| `created_at`           | `DateTime(tz)`, not null  | Indexed.                                                     |
| `updated_at`           | `DateTime(tz)`, not null  | Updated on every transition.                                 |
| `resolved_at`          | `DateTime(tz)`, nullable  | Set when entering a terminal state.                          |
| `due_at`               | `DateTime(tz)`, nullable  | Optional SLA hint.                                           |
| `tags`                 | `Text`, default `"[]"`    | JSON array of strings (JSON-in-Text convention).             |

Composite indexes:

- `ix_human_todos_status_category` — `(status, category)` for the
  triage-by-category dashboard query.
- `ix_human_todos_status_priority` — `(status, priority)` for the
  "what's the most urgent open item" query.

## 4. HTTP API

Registered by
[`routers/human_todos.py`](../../src/general_ludd/routers/human_todos.py) under
`/api/human-todos`. GET endpoints are **public**; POST/PATCH/DELETE are
**PSK-gated** (see §8).

### `POST /api/human-todos` — file a request

Request body (`CreateHumanTodoRequest`):

```json
{
  "agent_id": "implement_change_role",
  "title": "Need write access to /etc/gludd/prod.conf",
  "body": "Capability policy denies the write. Tried: X, Y, Z.",
  "category": "permission_escalation",
  "priority": "high",
  "parent_agent_todo_id": "TODO-abc12345",
  "session_id": "sess-...",
  "due_at": "2026-07-01T00:00:00Z",
  "tags": ["prod", "config"]
}
```

Response: `201 Created` with the full record (see `_human_todo_to_dict`).

**Parent block-on-file integration:** when `parent_agent_todo_id` is set, the
handler transitions the parent agent-todo to `BLOCKED_ON_HUMAN` before
committing. If the parent is missing or already terminal, the transition is
logged-and-skipped (the `HumanTodo` is still filed).

Validation errors → `422`.

### `GET /api/human-todos` — list

Query params: `status`, `category`, `priority`, `agent_id`, `limit`
(1–500, default 100), `offset`.

Response: `200` with an array of records (newest first).

### `GET /api/human-todos/feed` — incremental feed

Query params: `since` (ISO datetime; defaults to 24h ago).

Returns every `HumanTodo` whose `updated_at >= since`. Used by the CLI
`watch` subcommand for live tailing.

### `GET /api/human-todos/{id}` — fetch one

Response: `200` with the record, or `404`.

### `PATCH /api/human-todos/{id}` — resolve / advance

Request body (`PatchHumanTodoRequest`):

```json
{
  "status": "done",
  "human_resolution": "Key rotated and loaded into OpenBao at secret/gludd/openai.",
  "human_resolver": "shawn"
}
```

Allowed `status` values via PATCH: `done`, `dismissed`, `in_progress`,
`superseded`. `done` and `dismissed` require both `human_resolver` and
`human_resolution` (the latter is the dismiss reason). A `HumanTodo` already
in a terminal state → `422`.

**Unblock-on-resolve integration:** when the patched `HumanTodo` has a
parent agent-todo and just entered `done` or `dismissed`, the handler
transitions the parent back (`done → queued`, `dismissed → cancelled`)
before committing.

### `DELETE /api/human-todos/{id}` — soft delete

Soft-deletes by transitioning to `dismissed` (resolver `admin`, reason
`soft-deleted by admin`) if the record is still non-terminal. Does NOT
hard-delete — the row stays for audit. Response:

```json
{ "id": "HTODO-...", "status": "deleted", "final_status": "dismissed" }
```

### `POST /api/human-todos/{id}/tags` — add a tag

Request body (`AddTagRequest`):

```json
{ "tag": "comment:please check OpenBao first" }
```

The CLI `comment` subcommand uses the `comment:` prefix convention so a
separate comments table is unnecessary.

## 5. CLI

Subcommand tree: `gludd human-todo {list|show|done|dismiss|in-progress|comment|watch|stats}`
(see [`cli_human_todos.py`](../../src/general_ludd/cli_human_todos.py)).

Write operations send `GLUDD_PSK` as `Authorization: Bearer <psk>` so the
daemon's PSK middleware authenticates them. The daemon URL defaults to
`http://localhost:8000` and can be overridden with `--daemon-url` on every
subcommand. `--json` switches any command from a human-readable table to raw
JSON.

### Examples

```bash
# list (filter by open + urgent)
gludd human-todo list --status open --priority urgent

# show full detail
gludd human-todo show HTODO-1A2B3C4D5E

# resolve (the human provided what was asked)
gludd human-todo done HTODO-1A2B3C4D5E \
    --resolution "OPENAI_API_KEY rotated and loaded into OpenBao." \
    --resolver shawn

# dismiss (agent should try a different approach)
gludd human-todo dismiss HTODO-1A2B3C4D5E \
    --reason "We don't grant prod write access from agent sandboxes." \
    --resolver shawn

# mark in-progress (human is working on it)
gludd human-todo in-progress HTODO-1A2B3C4D5E

# comment (stored as a comment: tag)
gludd human-todo comment HTODO-1A2B3C4D5E "checking OpenBao now"

# live-tail the feed
gludd human-todo watch --poll 5

# counts by status / category / priority
gludd human-todo stats
```

## 6. Ansible module

The [`gludd_human_todo`](../../collections/ansible_collections/general_ludd/agent/plugins/modules/gludd_human_todo.py)
module is the **tool-call boundary** for filing/resolving a `HumanTodo` from a
playbook. Agents that run inside Ansible Runner use this module instead of
calling the HTTP API directly.

### When to use it

- An agent needs to **block on a human** before continuing (e.g. permission
  escalation that exceeds the agent's `PermissionSpec`).
- An agent needs an **external action** it cannot perform (create an AWS
  account, paste an API token, sign a release).
- An agent needs a **decision** from a human (which of N designs to pursue).
- A play needs to **resolve** a previously-filed `HumanTodo` (rare — usually
  the human uses the CLI; included for symmetry).

### Required arguments

- `state=present` requires `title`, `body`. Optional: `category`, `priority`,
  `parent_agent_todo_id`, `tags`, `agent_id`.
- `state=done` requires `id`, `human_resolution`. Optional: `human_resolver`.
- `state=dismissed` requires `id`, `reason`. Optional: `human_resolver`.

Connection args (`daemon_url`, `psk`, `timeout`) default to
`http://localhost:8000`, empty, and `30` respectively.

### Example task

```yaml
- name: Agent blocks on a permission escalation
  general_ludd.agent.gludd_human_todo:
    state: present
    title: "Need write access to /etc/gludd/prod.conf"
    body: |
      I need to update the prod config but the current capability policy
      denies filesystem writes outside the workspace. Tried: gludd_db
      resource_preference, env-var override. Both rejected.
    category: permission_escalation
    priority: high
    parent_agent_todo_id: "TODO-abc12345"
    agent_id: "implement_change_role"
  register: human_req
```

The module supports check mode (skips the write and returns a synthesized
`changed=True` result).

## 7. Categories

Defined in `HUMAN_TODO_CATEGORIES` (`repository.py`). The Ansible module and
the HTTP router both validate against this set.

| Category                | Use when                                                                                    |
|-------------------------|---------------------------------------------------------------------------------------------|
| `permission_escalation` | The agent needs a capability outside its `PermissionSpec` (filesystem path, host, TTL, etc.). The most common trigger for a `HumanTodo`. |
| `external_action`       | Something only a human can do in the real world: create an account, sign up for a service, file a ticket, plug in a hardware token. |
| `decision`              | The agent cannot choose between multiple valid approaches without human judgement ("which of these 3 designs?"). |
| `input_request`         | Missing input the agent cannot derive: a secret not in OpenBao, a config value, a clarification of intent. |
| `blocker`               | Generic catch-all when none of the above fit. Prefer the specific category when applicable. |

## 8. Security & auth

### Endpoint auth

| Endpoint                                  | Auth     |
|-------------------------------------------|----------|
| `GET /api/human-todos`                    | public   |
| `GET /api/human-todos/feed`               | public   |
| `GET /api/human-todos/{id}`               | public   |
| `POST /api/human-todos`                   | PSK      |
| `PATCH /api/human-todos/{id}`             | PSK      |
| `DELETE /api/human-todos/{id}`            | PSK      |
| `POST /api/human-todos/{id}/tags`         | PSK      |

Rationale (from the router docstring): a human needs to **see** the queue
without the admin PSK, but **mutating** the queue (filing, resolving,
deleting) is privileged. The CLI sends `GLUDD_PSK` as a Bearer token on write
operations; the Ansible module passes `psk` through to the daemon's PSK
middleware.

### Escalation requests & the permission intersection policy

A `HumanTodo` with `category=permission_escalation` is the **agent-facing
surface** for the permission model described in AGENTS.md's *"Human
Permission Subjects + Intersection Policy"* section. The two interact as
follows:

1. **Intersection rule.** When an agent dispatches a subagent, the effective
   permission is the **intersection** of the human's `PermissionSpec`, the
   agent's `PermissionSpec`, and the requested spec — intersection only
   narrows. No entity ever exercises a permission outside its own spec.

2. **Auto-approval within the intersection.** If the agent's escalation
   request is **inside** `(human ∩ agent)` — i.e. the agent is asking for
   something it would have had but for an overly-narrow intersection — the
   request is auto-approved. No `HumanTodo` is filed.

3. **Outside-intersection requests → `HumanTodo`.** If the request exceeds
   the intersection, it becomes a `HumanTodo` with
   `category=permission_escalation` and the parent agent-todo is moved to
   `blocked_on_human`. The human resolves it via
   `gludd human-todo done HTODO-...` or the CLI equivalent.

4. **Escalation request validator.** Before filing, the daemon's escalation
   endpoint requires the agent to document **≥3 distinct alternatives it
   already tried** (`alternatives_tried` with `{approach, outcome}` entries).
   Fewer than 3 → `422`. This prevents the agent from punting to the human
   without first exhausting its own options.

5. **Approval scopes the grant to the intersection.** When the human
   approves, the daemon mints a short-lived credential (STS) scoped to
   `(current + requested) ∩ human_spec`. **Humans cannot grant more than
   they have** — the intersection rule holds even at approval time.

6. **Human-Todo vs. permission deny.** A raw permission *deny* (the agent
   attempted something outside its spec and was blocked by the kernel /
   sandbox) is recorded in the **audit log**, not as a `HumanTodo`. A
   `HumanTodo` is only filed when the agent **proactively requests** an
   escalation after exhausting alternatives.
