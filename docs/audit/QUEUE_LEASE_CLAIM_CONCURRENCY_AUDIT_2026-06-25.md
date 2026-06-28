# Queue / Bucket-Lease / Claim / PID-Cap Concurrency Audit — 2026-06-25

Scope: `claim_runnable`, bucket-lease acquire/reclaim, and the PID-cap
"set-drop" release path in the event loop. Read-only audit; no code changed.

Files reviewed:
- `src/general_ludd/db/repository.py` (`TodoRepository.claim_runnable`, `transition`)
- `src/general_ludd/event_loop/lease.py` (`acquire_lease`, `reclaim_expired_leases`)
- `src/general_ludd/event_loop/loop.py` (`_phase_claim_runnable_todos`, `_phase_dispatch_execute_jobs`, `_phase_refill_task_buckets`)
- `src/general_ludd/db/models.py` (`BucketLeaseModel`)
- `tests/security/test_eventloop_redteam.py`, `tests/security/test_db_redteam.py`

---

## F1 — HIGH: a stale bucket lease can yank a *legitimately re-claimed* ACTIVE todo back to QUEUED (double-dispatch / work interruption)

**Mechanism.** The lease bucket key is derived from `queue:todo_id` only, but the
holder rotates every tick:

- `loop.py:927` — `holder = f"tick-{self._total_ticks}"`
- `loop.py:934` — `bucket_key=f"{bucket_key}:{todo_id}"`
- `models.py:459` — unique key is `("bucket_key", "holder_id")` (`uq_bucket_lease`)

Because `holder_id` changes per tick, re-claiming the same todo creates a **new**
lease row each tick rather than renewing one (confirmed by the existing test
`test_two_ticks_acquire_duplicate_leases_for_same_bucket`, redteam:156–184).

`reclaim_expired_leases` (`lease.py:71–87`) then requeues **any** ACTIVE todo
whose `todo_id` matches an expired lease's bucket key:

```python
todo_id = bucket_key.partition(":")[2] ...
if todo_id:
    await session.execute(
        update(TodoModel)
        .where(TodoModel.todo_id == todo_id,
               TodoModel.status == TodoStatus.ACTIVE.value)
        .values(status=TodoStatus.QUEUED.value, updated_at=now))
```

It does **not** check whether a *different, still-live* lease exists for the same
bucket. So when an older tick's lease for `queue:T1` expires while a newer tick is
legitimately running T1 under a fresh lease, the expiry forces `T1` ACTIVE→QUEUED
mid-flight → T1 is re-claimed and dispatched a second time.

**Trigger path (made reachable by F3).** The PID-cap release
(`loop.py:980–985`) transitions over-cap todos ACTIVE→QUEUED but leaves their
just-acquired lease (`loop.py:932`) in the table. Next tick re-claims the todo →
second lease row. ~300s later the first (orphan) lease expires → reclaim yanks the
now-active todo. The 300s TTL window makes this intermittent and easy to miss.

**Test gap.** `test_two_ticks…` proves duplicate live leases exist, and
`test_expired_lease_does_not_requeue_active_todo` (redteam:188–226) proves the
crashed-worker recovery path requeues on expiry — but **no test** covers the
interaction: reclaim on an expired lease while a live lease for the same bucket
is present. That is exactly the unsafe case.

**Suggested fix (pick one):**
- Make the holder stable per todo (e.g. `holder_id = todo_id` or a worktree id)
  so `acquire_lease` *renews* instead of accumulating rows; or
- In `reclaim_expired_leases`, before requeuing, skip the requeue when **another
  non-expired** lease row exists for the same `bucket_key` (`WHERE bucket_key=?
  AND expires_at >= now`); or
- Carry a lease generation / version into the requeue guard so only the holder
  that currently owns the todo can release it.

---

## F2 — MEDIUM: PID-cap victim selection is an arbitrary unordered slice (priority inversion)

`_phase_dispatch_execute_jobs` drops the over-cap tail:

```python
# loop.py:973
excess = list(claimed[cap:])
claimed = list(claimed[:cap])
```

`claimed` comes from `TodoRepository.claim_runnable`, whose candidate query has
**no `ORDER BY`** (`repository.py:372–375`): `select(TodoModel).where(status ==
QUEUED).limit(limit)`. So the rows arrive in natural/rowid order and the
"victims" beyond the cap are an arbitrary slice. A high-priority todo can be
released back to QUEUED while a lower-priority one is dispatched.

`TodoModel.priority` exists but is ignored here. Note `claim_unreviewed`
(`repository.py:640`) *does* `ORDER BY created_at ASC` — the inconsistency is
itself a smell.

**Suggested fix:** order the claim candidates deterministically
(`ORDER BY priority DESC, created_at ASC`) so both *which* todos are claimed and
*which* are dropped over the cap are stable and priority-respecting.

---

## F3 — MEDIUM: PID-cap release leaks the lease row (and is F1's trigger)

`loop.py:980–985` transitions over-cap todos ACTIVE→QUEUED but never deletes the
lease acquired at `loop.py:932`. Two consequences:
1. `bucket_leases` accumulates orphan rows (one per cap-released todo per tick).
2. Those orphans are the expiry that fires F1.

**Suggested fix:** delete the bucket lease for each released todo inside the same
loop that transitions it back to QUEUED (same session, before flush).

---

## F4 — LOW: `bucket_leases.expires_at` has no index

`reclaim_expired_leases` runs `WHERE expires_at < now` every tick
(`_phase_refill_task_buckets`, `loop.py:909`). `BucketLeaseModel` indexes
`bucket_key` and `project_id` but **not** `expires_at` (`models.py:443–460`).
Combined with the row accumulation from F1/F3 this is a full-table scan per tick.

**Suggested fix:** add `index=True` on `expires_at` (and a migration).

---

## F5 — LOW (note, no action): SQLite `with_for_update(skip_locked)` is suppressed by design

`claim_runnable` wraps `with_for_update(skip_locked=True)` in
`contextlib.suppress(Exception)` (`repository.py:376–377`) because SQLite drops
row locks. Correctness rests on the guarded conditional UPDATE
(`WHERE id=? AND status='queued' AND version=?`), which is sound and covered by
`test_concurrent_claim_runnable_double_claims_same_todo` (redteam:116–150). No
change needed; on Postgres the hint additionally helps.

---

## Naming defect (cosmetic)

`test_expired_lease_does_not_requeue_active_todo` (redteam:188–226) asserts the
**opposite** of its name — its body asserts the todo **IS** requeued
(`assert t1.status == TodoStatus.QUEUED.value`). Rename to
`…_does_requeue_active_todo` to avoid future confusion.

---

## Priority

1. **F3 + F1 together** — F3 is a small, safe fix (delete the lease on release)
   that also closes F1's main trigger; F1's reclaim-side guard should land with it.
2. **F2** — deterministic claim ordering.
3. **F4** — index + migration.

All findings are read-only observations; none have been applied.
