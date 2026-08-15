# Batch-4 Worktree Merge-Safety Map

Read-only audit produced 2026-06-16 to make `make wt-reap` of the pending agent
worktrees predictable. Grounds: `make git-status` (main working tree), `make
git-where` (canonical `git worktree list`), and `make wt-changed SRC=<wt>` per
worktree (which `cd`s into the worktree and lists its uncommitted
diff-vs-HEAD + untracked set). All 12 worktrees are based at the same HEAD
`5f7a453` and carry only **uncommitted** work (no feature branch of their own).

## How the reap decides safe-vs-overlap (the clobber-guard rule)

`make wt-reap` calls `wt-sync` per worktree. `wt-sync` whole-file-copies each
changed file into main **unless** the destination is (a) tracked AND (b)
locally-modified vs HEAD in main AND (c) differs in content — in which case it
prints `⛔ REFUSED (CLOBBER GUARD)` and tells you to use `make wt-apply` (3-way).

Two important mechanics from the Makefile:

1. **`wt-sync` SKIPS every `__init__.py`** (`*/__init__.py|__init__.py` in its
   skip-case). So `coordination/__init__.py`, `self_improve/__init__.py`,
   `connectors/__init__.py`, `memory/__init__.py`, etc. are **never** synced by
   the reap — they must be hand-merged / hand-created if a package needs new
   exports. This neutralizes the listed `__init__.py` overlap risk for the reap
   itself, but means new sub-packages may import-fail until their `__init__.py`
   is placed by hand. (Flagged per-worktree in Notes below.)
2. The guard only fires against files **already dirty in main**. A file that is
   clean-vs-HEAD in main (even if two worktrees touch it) syncs clean on the
   FIRST worktree; the SECOND worktree then sees main dirty and is REFUSED →
   needs `wt-apply`. That is exactly the `db/models.py` case below.

## Per-worktree map

| Worktree (agent-) | Task (inferred from files) | Changed tracked/untracked files (excl __init__, venv, logs) | Verdict | Notes |
|---|---|---|---|---|
| a0f2cca1f2e058861 | retrieval/RAG subsystem | `src/general_ludd/retrieval/{chunker,index,model,scorer,service}.py`, `tests/unit/test_retrieval.py` (+ `retrieval/__init__.py` skipped) | **SAFE wt-sync** | All-new package. `retrieval/__init__.py` is SKIPPED by wt-sync — place it by hand or package import fails. No main/other-wt overlap. |
| a24b0cb9a03d0b24d | memory store (G1) | `src/general_ludd/db/models.py`, `src/general_ludd/memory/{model,service,store}.py`, `tests/unit/test_memory_store.py` (+ `memory/__init__.py` skipped) | **OVERLAP → wt-apply** | `db/models.py` ALSO edited by a819 → real hazard (see below). Appends `MemoryRecordModel` at end of file. memory/__init__.py skipped — hand-place. |
| a3bcdaf779c9c2f54 | ansible result parsing | `src/general_ludd/ansible/{core_runner,result_parser,runner,runstate}.py`, `tests/unit/test_ansible_output.py` | **CHECK** (likely SAFE) | None of these are in main's M-list and no other worktree touches them. These are likely NEW files (ansible/ subdir not in main M-list); treat as SAFE wt-sync. Verify they are additions, not edits to pre-existing runner.py. |
| a3d19f1fef2b16a51 | sandbox policy/runner | `src/general_ludd/sandbox/{policy,runner}.py`, `tests/unit/test_sandbox.py` (+ `sandbox/__init__.py` skipped) | **SAFE wt-sync** | All-new package. `sandbox/__init__.py` SKIPPED — hand-place. No overlap. |
| a42a44311316d2184 | eval harness | `src/general_ludd/eval/{baseline,harness,model,scorers}.py`, `tests/unit/test_eval_harness.py` (+ `eval/__init__.py` skipped) | **SAFE wt-sync** | All-new package. `eval/__init__.py` SKIPPED — hand-place. No overlap. |
| a61d263b97ab0dc3a | orchestration guards | `src/general_ludd/agents/{dispatcher,orchestration_guards}.py`, `tests/unit/test_orchestration_guards.py` | **SAFE wt-sync** (in-place EDIT) | VERIFIED: `agents/dispatcher.py` PRE-EXISTS in HEAD (`make grep` confirmed) → this is an in-place edit + a new `orchestration_guards.py`. main is clean at `agents/` → wt-sync copies cleanly (worktree version wins). No clobber, no cross-wt overlap. Spot-check dispatcher.py edit doesn't drop a HEAD behavior. |
| a6658bf05a69d4d36 | git-lock / gludd_git module (**LOCKED**) | `collections/.../module_utils/{capability_policy,gludd_git_lock}.py`, `collections/.../modules/gludd_git.py`, `playbooks/{git_automate_change,git_manage_worktree,git_repo_init}.yml`, `tests/security/test_gludd_git_lock.py` | **HOLD — worktree is LOCKED** | `git worktree list` marks this `locked` = likely a STILL-RUNNING agent. Do NOT reap; pass its id to `KEEP=`. capability_policy.py also appears in main as committed (8afe2bd) — if this worktree re-edits it, it may need wt-apply once it finishes. |
| a7af6fa1b17155947 | backlog-audit system (#65) | `Makefile`, `scripts/backlog_audit.py`, `src/general_ludd/quality/bug_class_registry.py`, `src/general_ludd/validation/backlog_auditor.py`, `tests/unit/test_backlog_audit_system.py` (+ `validation/__init__.py` if any, skipped) | **OVERLAP → wt-apply / manual** | `Makefile` AND `scripts/backlog_audit.py` are BOTH in main's M-list → CLOBBER GUARD fires on both. Makefile especially must be MANUAL (3-way may conflict — see hazards). bug_class_registry.py / backlog_auditor.py are clean-in-main → those hunks sync/apply clean. |
| a819a1994d313cede | per-project accounting (#28) | `src/general_ludd/accounting/{git_loc,ledger}.py`, `src/general_ludd/db/models.py`, `src/general_ludd/db/repository.py`, `src/general_ludd/routers/{accounting,projects}.py`, `tests/unit/{test_project_ledger_endpoint,test_project_ledger}.py` | **OVERLAP → wt-apply** | TWO guard hits: `db/models.py` (shared with a24b) and `db/repository.py` (in main's M-list). `routers/projects.py` is clean-in-main + unique → SAFE part. accounting/, routers/accounting.py new → SAFE. |
| a90aaa2c4daeca7b9 | self-improve outcome loop | `src/general_ludd/self_improve/outcome_loop.py`, `tests/unit/test_outcome_loop.py` (+ `self_improve/__init__.py` skipped) | **SAFE wt-sync** | `self_improve/` package EXISTS in HEAD (committed prior). outcome_loop.py is a NEW module in it, clean-in-main, no other-wt overlap → SAFE. `self_improve/__init__.py` SKIPPED — if it needs a new export of outcome_loop, hand-edit. |
| abc58f5c2ecd41ce0 | worktree race fix | `src/general_ludd/worktree/core.py`, `tests/unit/test_worktree_race.py` | **SAFE wt-sync** (in-place EDIT) | VERIFIED: `worktree/core.py` PRE-EXISTS in HEAD (`make grep` confirmed) → in-place edit (race fix). main is clean at this path → wt-sync copies cleanly. No clobber, no cross-wt overlap. Spot-check the edit preserves HEAD's WorktreeEventDispatcher/evaluate behavior. |
| ae13d66c078d6dc4e | molecule-coverage tooling | `molecule/playbooks/role_self_improve_propose/default/converge.yml`, `molecule/playbooks/test_gludd_reload/default/{converge,prepare,verify}.yml`, `src/general_ludd/quality/molecule_coverage.py`, `tests/integration/test_molecule_coverage.py`, `tests/unit/{test_molecule_coverage,test_quality_tools}.py`, `tools/check_molecule_coverage.py` | **OVERLAP → wt-apply / manual** | FIVE files collide with main's M-list: the 4 molecule `*.yml` and `tests/integration/test_molecule_coverage.py`. These need wt-apply or manual. `quality/molecule_coverage.py`, `tools/check_molecule_coverage.py`, `tests/unit/*` are clean-in-main → SAFE part. |

## Real merge hazards — files touched by main AND/OR more than one worktree

These are the only places `wt-reap`'s blind whole-file copy is unsafe. Everything
not listed here is a clean additive sync.

### 1. `src/general_ludd/db/models.py` — touched by TWO worktrees (a24b + a819)
**THE headline hazard.** Verified by `make grep 'class .*Model'` on all three copies:

- main/HEAD `db/models.py`: `BenchmarkResultModel` at line 491; `RoleRunModel`
  at 471. (db/models.py is NOT in main's M-list — main is clean here, == HEAD.)
- a24b (memory): identical through `BenchmarkResultModel` (491), then APPENDS a
  new `class MemoryRecordModel` at line 529. Pure end-of-file append.
- a819 (accounting): `BenchmarkResultModel` shifted to line 496 (vs 491) — i.e.
  it INSERTED ~5 lines into `RoleRunModel` (471–491), adding columns. Mid-file edit.

Because the two edits are in **non-overlapping regions** (a24b = tail append,
a819 = RoleRunModel body), a 3-way apply of BOTH onto the pristine HEAD blob
merges cleanly. The danger is ONLY the blind whole-file copy: whichever syncs
first wins the file, the second gets clobber-REFUSED, and if you then `--force`
or hand-copy you LOSE the first one's class.

**Plan:** Do NOT let wt-sync touch `db/models.py` for either. Apply both via
3-way against the clean HEAD base:
```text
make wt-apply SRC=.../agent-a819a1994d313cede FILES='src/general_ludd/db/models.py'
make wt-apply SRC=.../agent-a24b0cb9a03d0b24d FILES='src/general_ludd/db/models.py'
```
(order doesn't matter; hunks are disjoint). Verify afterward that BOTH
`MemoryRecordModel` and the new RoleRunModel columns are present.

### 2. `Makefile` — main (M) + a7af (#65 backlog-audit target)
Main's Makefile carries the live wt-reap / floor-status / git-commit-file /
disk-guard / wt-apply machinery (this whole reap depends on it). a7af appended a
`backlog-audit` target. `wt-apply --3way` MAY succeed if a7af's add is a clean
tail-append, but given main has heavily rewritten the Makefile since HEAD, treat
this as **MANUAL**: read a7af's Makefile diff, copy ONLY the new `backlog-audit`
target block into main's Makefile by hand. Never whole-file-copy the worktree
Makefile over main's — it would erase the reap tooling.

### 3. `src/general_ludd/db/repository.py` — main (M) + a819
In main's M-list (batch edits) and edited by a819 (accounting ledger queries).
Clobber-guard fires. Use `make wt-apply SRC=.../agent-a819... FILES='src/general_ludd/db/repository.py'`;
if 3-way conflicts, hand-merge the accounting query methods.

### 4. `scripts/backlog_audit.py` — main (M) + a7af
In main's M-list and edited by a7af. Clobber-guard fires. `wt-apply` the a7af
diff; hand-resolve if it conflicts.

### 5. Molecule YAML + integration test — main (M) + ae13
`molecule/playbooks/role_self_improve_propose/default/converge.yml`,
`molecule/playbooks/test_gludd_reload/default/{converge,prepare,verify}.yml`,
and `tests/integration/test_molecule_coverage.py` are all in main's M-list and
re-edited by ae13. Clobber-guard fires on all five. `wt-apply` each; molecule
YAML 3-ways are conflict-prone, so be ready to hand-merge.

### 6. `__init__.py` files — NOT synced at all (silent gap, not a clobber)
`wt-sync` skips every `__init__.py`. New packages from the SAFE worktrees
(`retrieval/`, `sandbox/`, `eval/`, `memory/`) and possibly `self_improve/`,
`agents/` ship their `__init__.py` ONLY in the worktree; the reap will NOT copy
it. After syncing those packages, **hand-create / hand-edit each new
`__init__.py`** or the package import (and its tests) will fail. This is the
`coordination/__init__.py`, `self_improve/__init__.py`, `connectors/__init__.py`
class of risk called out in the task — for the reap it manifests as a *missing
file*, not a clobber. (No pending worktree here edits `connectors/__init__.py`
or `coordination/__init__.py`; those are main-tree concerns from other work.)

## Recommended reap ORDER

**Step 0 — exclude the locked/running worktree.** `agent-a6658bf05a69d4d36` is
`locked` (running agent). Keep it out of every reap:
`make wt-reap KEEP='a6658bf05a69d4d36'` (or omit it from any explicit SRCS list).

**Step 1 — SAFE new-file worktrees first (plain wt-sync, parallel-safe).** No
main overlap, no cross-worktree overlap. Bulk them:
```text
make wt-sync-all SRCS='\
  /Users/shawnwilson/gludd/.claude/worktrees/agent-a0f2cca1f2e058861 \
  /Users/shawnwilson/gludd/.claude/worktrees/agent-a3bcdaf779c9c2f54 \
  /Users/shawnwilson/gludd/.claude/worktrees/agent-a3d19f1fef2b16a51 \
  /Users/shawnwilson/gludd/.claude/worktrees/agent-a42a44311316d2184 \
  /Users/shawnwilson/gludd/.claude/worktrees/agent-a61d263b97ab0dc3a \
  /Users/shawnwilson/gludd/.claude/worktrees/agent-a90aaa2c4daeca7b9 \
  /Users/shawnwilson/gludd/.claude/worktrees/agent-abc58f5c2ecd41ce0'
```
(retrieval, ansible, sandbox, eval, orchestration-guards, self-improve-loop,
worktree-race). Immediately after, **hand-place the new `__init__.py`** files for
retrieval/ sandbox/ eval/ (and check self_improve/ exports).

**Step 2 — the db/models.py pair via 3-way (must precede their whole-file sync).**
Apply both worktrees' `db/models.py` (and a819's `db/repository.py`) by
`wt-apply` BEFORE syncing the rest of those two worktrees, so the guard never
clobbers models.py:
```text
make wt-apply SRC=.../agent-a819a1994d313cede FILES='src/general_ludd/db/models.py src/general_ludd/db/repository.py'
make wt-apply SRC=.../agent-a24b0cb9a03d0b24d FILES='src/general_ludd/db/models.py'
```
Then wt-sync the REMAINDER of those two (accounting + memory new files) — those
non-models files are clean additions:
```text
make wt-sync SRC=.../agent-a819a1994d313cede   # syncs accounting/, routers/*, tests; models.py/repository.py now match -> not refused
make wt-sync SRC=.../agent-a24b0cb9a03d0b24d   # syncs memory/* tests; models.py now matches
```
Hand-place `memory/__init__.py`.

**Step 3 — Makefile + backlog-audit (a7af) by hand.** Manually copy a7af's new
`backlog-audit` target into main's Makefile; `wt-apply` the other a7af files
(`scripts/backlog_audit.py` via 3-way, plus the clean
`quality/bug_class_registry.py` + `validation/backlog_auditor.py` which can
wt-sync). Do NOT reap a7af with a blind wt-sync — it would clobber the Makefile.

**Step 4 — molecule-coverage (ae13) via wt-apply.** `wt-apply` the 5 colliding
files (4 molecule YAML + test_molecule_coverage integration test); wt-sync the
clean remainder (`quality/molecule_coverage.py`, `tools/check_molecule_coverage.py`,
`tests/unit/*`). Be ready to hand-merge the molecule YAML 3-ways.

**Step 5 — reclaim + verify.** Only after the above, remove the integrated
worktrees (`make wt-remove-many SRCS='...'` for the ones already drained, still
KEEPing a6658bf...), then run `make test-count` when the tree is quiet to catch
any collection error from a missing `__init__.py`.

## Caveats on this map (honesty)

- a61d (`agents/dispatcher.py`) and abc5 (`worktree/core.py`) were VERIFIED via
  `make grep` to pre-exist in HEAD → they are in-place EDITS, not pure additions.
  a3bc (ansible `runner.py` etc.) was classified by absence from main's M-list /
  other worktrees but its per-file diff was NOT read — it may likewise be an
  in-place edit. In all three cases main is clean at those paths, so wt-sync
  copies cleanly REGARDLESS; the only residual question is whether an in-place
  edit silently regresses a HEAD behavior the orchestrator expected to keep.
  Low risk; spot-check the dispatcher/core/runner diffs if paranoid.
- `db/models.py` disjointness was verified structurally (class-line offsets via
  `make grep`), not by a literal 3-way dry-run. The offsets prove the edit regions
  don't overlap, which is what `git apply --3way` needs to succeed.
- The locked worktree a6658bf's final changed-set may grow before it finishes;
  re-run `make wt-changed` on it after it unlocks before integrating.
