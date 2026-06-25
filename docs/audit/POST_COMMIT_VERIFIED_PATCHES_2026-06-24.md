> STATUS (2026-06-25): SUPERSEDED — most patches herein were APPLIED this session (P1 4306bcf, A1 e74249a, A2 18d3abe). See POST_COMMIT_BACKLOG_2026-06-24.md Reconciliation section for current status. Kept as historical reference.

# Post-Commit Verified Patches — 2026-06-24

Paste-ready, caller-compat-verified diffs for the post-alpha.4 backlog
(`POST_COMMIT_BACKLOG_2026-06-24.md`). Each was produced by a read-only agent that
located the code by content (line numbers approximate, re-pin at apply) and confirmed
imports/names are in scope and the caller contract is preserved. Apply AFTER the
alpha.4 + alpha.5 commits land, then run `make gate-lowmem-background` to verify.

---

## PERF/ASYNC (P1, A1, A2) — verified, 4 diffs

### Patch 1 — `db/repository.py` `status_summary`: SQL GROUP BY (P1)

Replaces full-table load + Python counting with 3× SQL `GROUP BY` + `MIN(created_at)`.
Dict shape preserved exactly (`total`, `by_status`, `by_queue`, `by_work_type`,
`oldest_age_seconds`, `backlog_size`). `total = sum(by_status.values())` is correct
because `status` is non-nullable in `TodoModel`. `func`, `select`, `UTC`, `datetime`,
`TodoStatus`, `Any` all in scope.

```diff
--- a/src/general_ludd/db/repository.py
+++ b/src/general_ludd/db/repository.py
@@ status_summary
         _pid = self._resolve_pid(project_id)
-        stmt = select(TodoModel)
-        if _pid is not None:
-            stmt = stmt.where(TodoModel.project_id == _pid)
-        result = await self._session.execute(stmt)
-        rows = list(result.scalars().all())
-        by_status: dict[str, int] = {}
-        by_queue: dict[str, int] = {}
-        by_work_type: dict[str, int] = {}
-        oldest_created: datetime | None = None
-        for r in rows:
-            by_status[r.status] = by_status.get(r.status, 0) + 1
-            by_queue[r.queue] = by_queue.get(r.queue, 0) + 1
-            by_work_type[r.work_type] = by_work_type.get(r.work_type, 0) + 1
-            created = r.created_at
-            if created is not None:
-                if created.tzinfo is None:
-                    created = created.replace(tzinfo=UTC)
-                if oldest_created is None or created < oldest_created:
-                    oldest_created = created
+        from sqlalchemy import func
+
+        async def _group_counts(column):
+            stmt = select(column, func.count()).group_by(column)
+            if _pid is not None:
+                stmt = stmt.where(TodoModel.project_id == _pid)
+            result = await self._session.execute(stmt)
+            return {key: count for key, count in result.all()}
+
+        by_status = await _group_counts(TodoModel.status)
+        by_queue = await _group_counts(TodoModel.queue)
+        by_work_type = await _group_counts(TodoModel.work_type)
+
+        oldest_stmt = select(func.min(TodoModel.created_at))
+        if _pid is not None:
+            oldest_stmt = oldest_stmt.where(TodoModel.project_id == _pid)
+        oldest_created = (await self._session.execute(oldest_stmt)).scalar()
+        if oldest_created is not None and oldest_created.tzinfo is None:
+            oldest_created = oldest_created.replace(tzinfo=UTC)
+
         oldest_age_seconds: float | None = None
         if oldest_created is not None:
             oldest_age_seconds = (datetime.now(UTC) - oldest_created).total_seconds()
         backlog = by_status.get(TodoStatus.BACKLOG.value, 0) + by_status.get(
             TodoStatus.QUEUED.value, 0
         )
         return {
-            "total": len(rows),
+            "total": sum(by_status.values()),
             "by_status": by_status,
             ...
         }
```

### Patch 2 — `routers/integrity.py` `admin_selftest`: offload subprocess (A1)

`asyncio` is NOT imported at module level and `subprocess` is imported locally inside
the function — add a local `import asyncio`. `to_thread` forwards kwargs; `TimeoutExpired`
still caught by the surrounding try/except.

```diff
--- a/src/general_ludd/routers/integrity.py
+++ b/src/general_ludd/routers/integrity.py
@@ async def admin_selftest
+        import asyncio
         import subprocess
         ...
-                    result = subprocess.run(
+                    result = await asyncio.to_thread(
+                        subprocess.run,
                         ["uv", "run", "molecule", "test", "-s", scenario],
                         capture_output=True,
                         text=True,
                         timeout=300,
                         cwd=os.getcwd(),
                     )
```

### Patch 3a — `event_loop/loop.py` ~872: bounded concurrent-batch gather (A2)

Wrap the concurrent dispatch batch in `asyncio.wait_for` (300s/job, 30-min cap);
`ensure_future` so pending coroutines are cancellable Tasks; on timeout cancel + drain +
`continue`. `asyncio`, `logger` in scope.

```diff
-                tasks = [
-                    self._dispatch_execute_job_isolated(t)
-                    for t in batch_todos
-                ]
-                results = await asyncio.gather(*tasks, return_exceptions=True)
-                for res in results:
-                    if isinstance(res, Exception):
-                        logger.error("Concurrent job dispatch raised: %s", res)
-                    else:
-                        dispatch_count += 1
+                tasks = [
+                    asyncio.ensure_future(self._dispatch_execute_job_isolated(t))
+                    for t in batch_todos
+                ]
+                batch_timeout = min(300.0 * len(batch_todos), 1800.0)
+                try:
+                    results = await asyncio.wait_for(
+                        asyncio.gather(*tasks, return_exceptions=True),
+                        timeout=batch_timeout,
+                    )
+                except (TimeoutError, asyncio.TimeoutError):
+                    logger.error(
+                        "Concurrent dispatch batch timed out after %.0fs; "
+                        "cancelling %d pending job(s)",
+                        batch_timeout,
+                        sum(1 for t in tasks if not t.done()),
+                    )
+                    for t in tasks:
+                        if not t.done():
+                            t.cancel()
+                    await asyncio.gather(*tasks, return_exceptions=True)
+                    continue
+                for res in results:
+                    if isinstance(res, Exception):
+                        logger.error("Concurrent job dispatch raised: %s", res)
+                    else:
+                        dispatch_count += 1
```

### Patch 3b — `agents/dispatcher.py` `dispatch_many` ~121: bounded gather (A2)

Add `timeout` kwarg (default 30m, so existing 1-arg callers compile unchanged),
`return_exceptions=True`, synthesize `status="failed"` results on timeout/exception.
Returns one `AgentTaskResult` per input task. Add module constant near `logger`:

```diff
+# Default wall-clock budget for a whole dispatch_many batch.
+DEFAULT_DISPATCH_TIMEOUT = 1800.0  # 30 minutes
```

```diff
-    async def dispatch_many(self, tasks: list[AgentTask]) -> list[AgentTaskResult]:
-        coros = [self.dispatch_one(t) for t in tasks]
-        results = await asyncio.gather(*coros)
-        return list(results)
+    async def dispatch_many(
+        self,
+        tasks: list[AgentTask],
+        timeout: float = DEFAULT_DISPATCH_TIMEOUT,
+    ) -> list[AgentTaskResult]:
+        if not tasks:
+            return []
+        futures = [asyncio.ensure_future(self.dispatch_one(t)) for t in tasks]
+        try:
+            results = await asyncio.wait_for(
+                asyncio.gather(*futures, return_exceptions=True),
+                timeout=timeout,
+            )
+        except (TimeoutError, asyncio.TimeoutError):
+            logger.error(
+                "dispatch_many timed out after %.0fs; cancelling %d pending task(s)",
+                timeout,
+                sum(1 for f in futures if not f.done()),
+            )
+            for f in futures:
+                if not f.done():
+                    f.cancel()
+            await asyncio.gather(*futures, return_exceptions=True)
+            return [
+                self._result_from_future(task, fut)
+                for task, fut in zip(tasks, futures, strict=True)
+            ]
+        out: list[AgentTaskResult] = []
+        for task, res in zip(tasks, results, strict=True):
+            if isinstance(res, AgentTaskResult):
+                out.append(res)
+            else:
+                logger.error("Task %s raised in dispatch_many: %s", task.task_id, res)
+                out.append(AgentTaskResult(
+                    task_id=task.task_id, agent_name=task.agent_name,
+                    status="failed", output=str(res),
+                ))
+        return out
+
+    @staticmethod
+    def _result_from_future(task, fut):
+        if fut.done() and not fut.cancelled():
+            exc = fut.exception()
+            if exc is None:
+                return fut.result()
+        return AgentTaskResult(
+            task_id=task.task_id, agent_name=task.agent_name,
+            status="failed", output="dispatch timed out",
+        )
```

**Post-apply caveat:** confirm no caller of `dispatch_many` relies on it *raising* on a
child exception (new code converts raises → `status="failed"`). Consistent with
`dispatch_one`, which already returns failures rather than raising.

---

## ERROR-HANDLING (E1/E3) — verified, 3 diffs

**Critical caller fact:** `EventLoop.run_forever` (loop.py:458-468) calls `await self.tick()`
with no per-tick try/except and its outer handler **re-raises**. Sole prod caller is
daemon.py:1032 (`asyncio.create_task(event_loop.run_forever(...))`). So making `tick()`
re-raise on commit failure would **kill the daemon loop permanently**. All fixes below keep
the loop alive (rollback + loud log, never re-raise). `contextlib` (loop.py:6) and `logger`
(both modules) are already in scope. All three verified caller-safe against the prod chain +
the 7 unit-test callers in `tests/unit/test_event_loop.py`.

### Bug 1 — `loop.py:420-423` commit-swallow (Med-High: silent data loss)

```diff
                     try:
                         await session.commit()
-                    except Exception as exc:
-                        logger.warning("Failed to commit tick session: %s", exc)
+                    except Exception as exc:
+                        # Data-loss event: the whole tick's writes are dropped. Log
+                        # loudly + roll back so the failed txn doesn't leak through
+                        # context-exit. Do NOT re-raise: run_forever re-raises and
+                        # would kill the daemon loop. Orphaned ACTIVE todos are
+                        # reclaimed by reclaim_expired_leases / _reap_stuck_todos.
+                        logger.error("Failed to commit tick session (writes lost): %s", exc)
+                        with contextlib.suppress(Exception):
+                            await session.rollback()
```

### Bug 2 — `loop.py:739-750` lease-suppress (Low-Med)

Replace blind `contextlib.suppress(Exception)` with logged try/except — a CLAIMED todo
with no lease row can only be recovered by the slow `_reap_stuck_todos` fallback, so the
failure must be visible.

```diff
             for todo in claimed:
                 bucket_key = _safe_str(todo, "queue", "core") or "core"
-                with contextlib.suppress(Exception):
+                todo_id = _safe_str(todo, "todo_id", "")
+                try:
                     await acquire_lease(
                         self._active_session,
-                        bucket_key=f"{bucket_key}:{_safe_str(todo, 'todo_id', '')}",
+                        bucket_key=f"{bucket_key}:{todo_id}",
                         holder_id=holder,
                         project_id=project_id,
                     )
+                except Exception as exc:
+                    logger.warning(
+                        "Lease acquisition failed for todo %s (bucket=%s): %s",
+                        todo_id, bucket_key, exc,
+                    )
```

### Bug 3 — `models/job_invocation.py:183-184` silent benchmark swallow (Low-Med)

NOTE: actual path is `models/job_invocation.py` (there is no `jobs/` dir). The truly-silent
swallow is at :183-184 (not :103, which already logs at warning + intentionally returns None).
Keep fire-and-forget; add `debug`-level diagnostics so a persistently-dead recorder is visible.

```diff
-                except RuntimeError:
-                    pass
-    except Exception:
-        pass
+                except RuntimeError as exc:
+                    logger.debug("Benchmark recorder event-loop scheduling failed: %s", exc)
+    except Exception as exc:
+        logger.debug("Benchmark recording failed (non-fatal): %s", exc)
```

### Remaining except-pass sites — audit premise partly stale (verified)

A separate agent re-audited the other E1/E2 sites; several audit claims were stale:
- **`worker/app.py` ~71, ~124: NOT BUGS** — already log via `logger.warning`. The file's
  other except clauses (~280, ~314) re-raise `HTTPException` / re-raise after cleanup. No fix.
- **`loop.py` ~394 (reaper): NOT A BUG** — already logs `logger.warning`. No fix.
- **`loop.py` ~114 and ~121 (`_resolve_prompt_text_static`): real BUGS** — two genuinely
  silent swallows. Fix = log-only additions (`logger.warning`/`debug`) inside the existing
  `except` bodies; no control-flow change, `logger` in scope.
- **`loop.py` ~283 (MQ inbox): BUG (low)** — discards DB errors into an empty-inbox default;
  add a `logger.warning` so a broken inbox query is visible. No control-flow change.
(Exact diffs are log-only insertions; re-pin line numbers at apply.)

## DEPENDENCIES (Dep1-6) — pending agent (langchain/langgraph conflict resolution)

## DEPENDENCIES (Dep1-6) — pending agent (langchain/langgraph conflict resolution)
