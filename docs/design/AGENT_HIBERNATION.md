# Agent-environment hibernation (dehydrate ⇄ hydrate)

**Status:** mechanism landed (`src/general_ludd/agents/hibernation.py`); integration is opt-in and staged (see §4).

## Problem

Deep agent recursion should be cheap. When an agent parks waiting on a child
N levels down a recursion tree, its in-RAM environment — dominated by the
conversation **context** (message history + tool results) — stays resident even
though the agent does nothing until the child returns. With enough nesting, the
idle ancestors pin far more memory than the single active frontier needs.

Goal (user ask): *the state of agent environments not expected to be needed for
a while can be serialized and hydrated when the subagent is about to return to
them* — i.e. inflate/deflate agent environments cheaply so recursion can go deep
without hoarding system resources.

## What is (and isn't) the expensive part

`AgentDispatcher.dispatch_one` awaits its child inside a per-agent semaphore. An
`await` on the asyncio loop already makes the *coroutine frame* cheap — a
suspended frame is not a thread. Recon (`docs/` + source) confirmed the actually
reclaimable costs of an idle-waiting agent are:

1. **Context RAM** — the message list (`list[ContextMessage]`), the single
   largest live object per agent (grows every tool-loop iteration).
2. Worktree/venv disk (~320 MB per isolated worktree) — orthogonal; handled by
   the worktree reclaim path, not this module.

So hibernation is a **context-offload** feature: on park, serialize the context
to disk and drop it from RAM; on resume, rehydrate. The coroutine frame stays;
its heavy payload does not.

## Mechanism (`agents/hibernation.py`)

| Piece | Role |
|---|---|
| `AgentEnvironmentSnapshot` | pydantic model; `messages: list[ContextMessage]` is the heavy field, plus small metadata (`task_id`, `depth`, `parent_task_id`, workspace, model/prompt profile, `scratch`). |
| `HibernationHandle` | tiny (<512 B) in-RAM reference to a dehydrated snapshot — holding this instead of the snapshot is the RAM reclaim. |
| `HibernationStore` | `dehydrate`/`hydrate`/`discard` (+ `_async` variants via `asyncio.to_thread`). JSON only, **never pickle**. |
| `HibernationController` | policy: `should_dehydrate(snap)` gates on depth **and** context size; `parked(snap)` async CM wraps the wait. |
| `ParkedEnv` | yielded by `parked()`; exposes `.dehydrated` and, after the block, the rehydrated `.snapshot`. |
| `messages_from_dicts(raw)` | bridge from the gateway/tool-loop's untyped `list[dict]` (which also carries a non-serializable LangChain `raw_response`) to clean `ContextMessage`s. |

### Security contract (per the serialization security review)

- **Pydantic-validated JSON, never pickle** — a tampered file cannot execute code.
- **Keyed HMAC-SHA256 integrity.** A per-store random key (RAM only) signs the
  payload; `hydrate` verifies with `hmac.compare_digest` against both the
  envelope MAC and the trusted in-RAM handle MAC. A tampered on-disk file cannot
  be re-signed without the key → rejected. (Ephemeral key ⇒ snapshots are
  in-process only, intentionally not portable across a restart. A durable
  variant would key from `secrets/`.)
- **Path jail** — hostile `task_id` (`../../etc/passwd`) is sanitized to a single
  filename component; both write and read re-check `resolved.parent == base`.
- **`0o700` dir + `0o600` file** — removes the cross-user-tamper precondition
  (mirrors the `models/response_cache.py` diskcache-CVE posture).
- **Atomic write** (tmp + `replace`) — no half-written snapshot survives a crash.
- **No leaks** — `parked()`'s `finally` discards the on-disk file even if hydrate
  raises; the generator `del`s its own `snap` ref so RAM is genuinely freed.

### Default location

`GLUDD_HIBERNATION_DIR` → `$XDG_DATA_HOME/general-ludd/hibernation` →
`~/.local/share/general-ludd/hibernation` (mirrors `db.session.get_default_db_path`).
Callers should inject a **session-scoped** dir so orphaned snapshots are cleaned
on teardown.

## 4. Integration path (staged, opt-in — NOT yet wired)

The correct reclaim seam is **inside a recursive executor, around its fan-out
await** (where the parent's context is idle until the child returns), NOT at
`dispatch_one` (there the executor is active and needs its context — dehydrating
a copy there reclaims nothing).

Planned wiring, all inert unless a `hibernation.enabled` config flag is set:

1. **Config flag** — `hibernation: {enabled=False, min_depth=3, min_context_messages=8, base_dir?}`.
2. **Build in daemon lifespan** — construct `HibernationStore`(session dir) +
   `HibernationController`, publish on `app.state._hibernation`; else `None`.
3. **Depth via `contextvars`** — `dispatch_one` sets `_hib_depth`/`_hib_parent_id`
   (asyncio propagates them to children created by `ensure_future`); the executor
   reads `_hib_depth.get()` to stamp `snapshot.depth`. (`AgentTask.parent_task_id`
   is currently never set; contextvars avoid a race-prone global map.)
4. **Wrap the fan-out await** in the recursive executor / `ToolCallLoop` with
   `async with controller.parked(snap): result = await <child>`.

### Known integration risks (must resolve before enabling)

- **Tool-call metadata is lossy.** `messages_from_dicts` keeps role/content/
  timestamp and drops `tool_call_id` / assistant `tool_calls`. Do **not** restore
  working `messages` from a snapshot *mid* tool-loop — the assistant↔tool linkage
  would break. v1 dehydrates a copy for RAM relief only; a faithful restore needs
  the snapshot to preserve tool metadata (extend `ContextMessage`).
- **Semaphore is held while dehydrated** — `parked()` frees RAM, not the
  concurrency slot; deep same-role recursion can still hit `max_concurrent`.
- **Disk churn** — bound by `should_dehydrate` (depth + size); consider a
  child-duration heuristic later so deep-but-fast calls don't pay disk cost.
- **Restart orphans** — ephemeral MAC key ⇒ post-restart snapshots are
  unrehydratable; session-scoped dir + teardown cleanup required.

### Future optimizations (noted, not implemented)

- gzip the payload on serialize (cheaper disk, per the context-compaction audit).
- token-based `should_dehydrate` gate instead of raw message count.
