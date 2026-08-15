# OpenCode database maintenance runbook

**Last evidence review:** 2026-08-08
**Scope:** Gludd's `opencode-disk`, `opencode-db-*`, and `opencode-clean*`
Make targets.

This runbook is for reclaiming OpenCode session storage without racing the
OpenCode process that owns it. It is deliberately conservative: a cleanup that
returns `busy` and changes nothing is safer than one that makes the current
session disappear or prevents OpenCode from starting.

## Safety contract

1. Treat an explicit `OPENCODE_DB` value as authoritative; otherwise use the
   bounded result of `opencode db path`. The
   [current OpenCode CLI reference](https://opencode.ai/docs/cli/#db) exposes
   that command specifically to locate the active database. Do not infer a
   filename such as `opencode.db` from an old installation, issue, or forum
   post. `OPENCODE_DATA_DIR` only overrides the sibling data directory used by
   the cleanup targets. [OpenCode issue #21790](https://github.com/anomalyco/opencode/issues/21790)
   records a 2026 filename change that made intact history appear lost.
2. Inspection may run while OpenCode is active; deletion, checkpointing, and
   `VACUUM` may not. Quit the TUI and desktop app, stop `serve`/`web` processes,
   and stop containers that share the same OpenCode data directory before a
   mutating target. Never bypass the running-process guard.
3. Never remove, rename, or copy only the database, `-wal`, or `-shm` file.
   SQLite documents the
   [WAL as part of the database's persistent state](https://www.sqlite.org/wal.html#the_wal_file)
   and warns that separating it can lose committed transactions or corrupt the
   database. A target must ask SQLite to checkpoint; it must not unlink SQLite's
   sidecar files.
4. Keep the database on a local filesystem used by one host. SQLite states that
   [WAL does not work over a network filesystem](https://www.sqlite.org/wal.html#overview),
   and OpenCode users have reported corruption with concurrent NFS and
   host/container access. Maintenance cannot make those unsupported sharing
   patterns safe.
5. Do not trade a large database for an out-of-space crash. Gludd's maintenance
   targets deliberately avoid full `VACUUM` and full-copy backups: SQLite's
   [`VACUUM` documentation](https://www.sqlite.org/lang_vacuum.html#how_vacuum_works)
   says a conventional vacuum can need as much as twice the database size in
   free space. When an independent backup is required and capacity permits it,
   use a SQLite-consistent backup mechanism; never make a raw copy of only the
   main file while OpenCode is running.
6. Mutating targets refuse symlinked data, tool-output, or log roots so path
   normalization cannot redirect deletion outside the intended data directory.
   Long SQLite and file-cleanup phases emit periodic heartbeats and still stop
   at their configured time or entry bound.

## Target selection

Run the narrowest target that solves the problem.

| Target | Mutates session DB? | OpenCode may be running? | Purpose |
|---|---:|---:|---|
| `make opencode-disk` | No | Yes | Show database, WAL, tool-output, and log disk use. |
| `make opencode-db-stats` | No | Yes | Show table row counts and resolved database size. Counts are only a point-in-time observation. |
| `make opencode-db-schema` | No | Yes | Diagnose schema/version differences; it is not a cleanup step. |
| `make opencode-db-sample` | No | Yes | Diagnose timestamp representation before changing retention logic; it is not a cleanup step. |
| `make opencode-db-prune` | Yes | **No** | Remove expired session records and dependent event/projection rows in small committed batches with row, time, and lock-wait bounds. This frees reusable pages but need not shrink the file. |
| `make opencode-clean` | Maintenance only | **No** | Run a nonblocking passive checkpoint and optimize; use incremental vacuum only if the database was already configured for it, then perform bounded cache/log cleanup. It never runs a full vacuum. |
| `make opencode-clean-hard` | No session-row deletion | **No** | Remove more disposable tool-output/log data. This is not database repair and does not replace pruning. |

The OpenCode CLI also provides logical
[`session delete`](https://opencode.ai/docs/cli/#delete) and
[`export`](https://opencode.ai/docs/cli/#export) operations. Prefer those
upstream operations when preserving or removing a specific known session;
Gludd's database targets are for measured bulk maintenance, not ad-hoc SQL.

## Routine cleanup

1. Run `make opencode-disk` and `make opencode-db-stats`. Record the resolved
   path, free-space situation, and row counts before changing anything.
2. If only disposable tool output or logs are large, use the corresponding
   cache cleanup target; do not touch session rows.
3. If session data must be pruned, finish or export anything that must be kept,
   quit every OpenCode client/server sharing the database, and run
   `make opencode-db-prune`. The target reports each committed batch and stops
   at its configured row or elapsed-time bound; rerunning continues safely.
4. Read the complete result. `busy`, a failed integrity check, partial
   completion, or any SQL error means **no full-success claim and no manual
   fallback deletion**. Already committed batches remain deleted; resolve the
   cause, then rerun the same guarded target.
5. Run `make opencode-db-stats` again. A lower row count with an unchanged file
   size is normal: SQLite generally marks deleted pages reusable rather than
   returning them to the filesystem.
6. Keep OpenCode stopped and run `make opencode-clean` for the bounded
   checkpoint/optimization step. It uses `wal_checkpoint(PASSIVE)`, which
   [does not wait for readers or writers](https://www.sqlite.org/pragma.html#pragma_wal_checkpoint),
   and never follows it with a full `VACUUM` or sidecar deletion. Physical file
   size can therefore remain unchanged even after successful maintenance.
7. Start OpenCode, open a retained session, and run `make opencode-disk` once
   more. Retain any independently managed pre-maintenance backup until that
   application-level check succeeds.

## Failure and recovery notes

- **`SQLITE_BUSY`, a lock refusal, or a running-process refusal:** stop all
  OpenCode TUI, desktop, `serve`, `web`, and container processes that resolve to
  the same database. Retry later. Do not use a force flag and do not delete
  sidecar files. The targets intentionally avoid `TRUNCATE` checkpoints:
  SQLite documents that mode as waiting for readers and writers and reporting
  when another process blocked it.
- **Database does not shrink after prune or clean:** this is not evidence that
  deletion failed. Compare row counts. The guarded targets prioritize bounded
  resource use and do not run a full vacuum; incremental vacuum only helps when
  the database's existing auto-vacuum mode supports it.
- **History appears missing after an OpenCode upgrade:** compare the path that
  the targets resolved with `opencode db path` before altering either database.
  A filename migration can look like data loss while the old data remains
  intact, as #21790 demonstrates.
- **Integrity check fails or OpenCode reports `database disk image is
  malformed`:** stop immediately and preserve the database together with its
  WAL/SHM files. Do not run prune or vacuum as a repair attempt. Restore the
  last known-good consistent backup, or work from a copy with SQLite recovery
  tooling. Deleting the database is a last-resort reset that loses history.
- **Disk fills during maintenance:** stop writing, preserve any existing
  recovery copy, and recover space outside the database directory. User report
  #7607 shows that
  disk exhaustion during active storage writes can leave a session unreadable;
  an unbounded full copy or full vacuum is not a safe recovery shortcut.
- **Database is on NFS or shared between host and container:** move the OpenCode
  data directory to a local, single-host filesystem before retrying. Cleanup
  serialization cannot repair broken cross-host WAL locking.

## Current OpenCode storage behavior

The target design was checked against OpenCode's current `dev` source on
2026-08-08, not inferred from an older database sample:

- [`Database.path()`](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/database/database.ts)
  honors `OPENCODE_DB`, otherwise chooses a release-channel-specific filename.
  The same module enables WAL, foreign keys, a five-second busy timeout, and a
  passive startup checkpoint.
- The
  [session schema](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/session/sql.ts)
  cascades session deletion to `message`, then message deletion to `part`; it
  also cascades to `todo`, `session_message`, `session_input`, and
  `session_context_epoch`. `part.session_id` is not itself a foreign key, so a
  cleanup must preserve the message-to-part ordering and validate the actual
  installed schema.
- The
  [event schema](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/event/sql.ts)
  cascades `event_sequence` to `event`, but has no foreign key to `session`.
  OpenCode's application-level remove path recursively removes child sessions,
  deletes the session projection, and then removes each session's durable event
  aggregate. A raw `DELETE FROM session` alone can therefore strand
  `event_sequence` and `event` records.

For that reason, `opencode-db-prune` snapshots eligible session identifiers and
removes both projection dependents and matching durable aggregates in bounded,
committed batches. The current source is still allowed to evolve, so the target
validates the installed schema instead of assuming all tables are present.

## Long-lived user evidence

These reports establish failure modes and operational demand; they do **not**
prove every reported root-cause theory or guarantee the current OpenCode release
still has the same implementation. Statuses below were checked on 2026-08-08.

| Report | Date and status | Reported evidence | Operational inference used here |
|---|---|---|---|
| [OpenCode #4980](https://github.com/anomalyco/opencode/issues/4980) | Opened 2025-12-02; closed | A user could not find automatic cleanup for historical session data. This report predates the current SQLite layout. | Retention must be explicit and observable; absence of a current product guarantee is not proof of automatic cleanup. |
| [OpenCode #5734](https://github.com/anomalyco/opencode/issues/5734) | Opened 2025-12-18; closed | Subagent sessions and related records accumulated; deleting only selected files produced orphaned-data errors. This also predates the current SQLite layout. | Bulk cleanup must remove related records transactionally; operators must not hand-delete partial storage. |
| [OpenCode #7607](https://github.com/anomalyco/opencode/issues/7607) | Opened 2026-01-10; closed | A full disk left active session artifacts truncated and unreadable; the reporter recovered by backing up and quarantining damaged data. | Check disk headroom first and preserve reversible recovery material before maintenance. |
| [OpenCode #14194](https://github.com/anomalyco/opencode/issues/14194) and [#14970](https://github.com/anomalyco/opencode/issues/14970) | Opened 2026-02-18 (closed as not planned) and 2026-02-24 (open) | Users reported malformed SQLite databases when concurrent OpenCode processes shared data across a host/container boundary or NFS. | Require a local filesystem and quiesce every process using the resolved database before writes/checkpoints. |
| [OpenCode #16101](https://github.com/anomalyco/opencode/issues/16101) | Opened 2026-03-05; open | One user measured a 774 MB database after three weeks: 1,812 sessions, 50,818 messages, and 176,596 parts, with child sessions dominating growth. | Provide read-only size/count visibility and a deliberate retention path; one user's measurements are not a universal growth rate. |
| [r/opencodeCLI session-recovery thread](https://www.reddit.com/r/opencodeCLI/comments/1sgx0ld/i_built_a_plugin_to_fix_opencodes_most_annoying/) | Posted about April 2026; forum post live | The author reported 649 sessions, 53,000 messages, and 200,000 parts in a local SQLite database while describing crash recovery needs. | Operators value retained context, so cleanup defaults should fail closed and recovery should preserve history rather than reset the database. |

The recurring evidence supports guarded maintenance, but the safety rules above
ultimately come from OpenCode's current path-discovery interface and SQLite's
locking, WAL, backup, and vacuum documentation.
