# Batch-4 Merge Safety Plan v2

Generated: 2026-06-16 (refreshed after 529-overload kill)
Method: `make git-where` for worktree list; `make wt-changed SRC=<dir>` for each live worktree;
        `make git-status` for main working-tree state; direct Read of conflicting files.

---

## 1. Live worktree inventory and changed-file table

All 33 live worktrees are at commit `5f7a453` (current master HEAD).
The `+` prefix in `git worktree list` confirmed each disk path is intact.

| # | Worktree suffix    | New package / feature                     | Changed files (wt-changed output)                                                                                                                                             | db/models.py? | Makefile? | __init__.py files (skipped by wt-sync) |
|---|-------------------|-------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------|-----------|----------------------------------------|
| 1 | a075db9607378180d | audit_log                                 | src/general_ludd/audit_log/__init__.py, audit_log/log.py, tests/unit/test_audit_log.py                                                                                        | NO            | NO        | audit_log/__init__.py                  |
| 2 | a0789ea341bb2cf84 | preview                                   | src/general_ludd/preview/__init__.py, preview/engine.py, preview/models.py, tests/unit/test_preview.py                                                                        | NO            | NO        | preview/__init__.py                    |
| 3 | a0f2cca1f2e058861 | retrieval                                 | src/general_ludd/retrieval/__init__.py, retrieval/chunker.py, retrieval/index.py, retrieval/model.py, retrieval/scorer.py, retrieval/service.py, tests/unit/test_retrieval.py | NO            | NO        | retrieval/__init__.py                  |
| 4 | a1aeefc974084b154 | prompt_versioning                         | src/general_ludd/prompt_versioning/__init__.py, prompt_versioning/models.py, prompt_versioning/store.py, tests/unit/test_prompt_versioning.py                                 | NO            | NO        | prompt_versioning/__init__.py          |
| 5 | a24b0cb9a03d0b24d | memory + MemoryRecordModel (DB)           | src/general_ludd/db/models.py, memory/__init__.py, memory/model.py, memory/service.py, memory/store.py, tests/unit/test_memory_store.py                                       | **YES**       | NO        | memory/__init__.py                     |
| 6 | a257c4c0fd60a0b9b | tool_registry                             | src/general_ludd/tool_registry/__init__.py, tool_registry/models.py, tool_registry/registry.py, tests/unit/test_tool_registry.py                                              | NO            | NO        | tool_registry/__init__.py              |
| 7 | a27188d287234a5ab | replay                                    | src/general_ludd/replay/__init__.py, replay/api.py, replay/models.py, replay/recorder.py, replay/store.py, tests/unit/test_replay.py                                          | NO            | NO        | replay/__init__.py                     |
| 8 | a3bcdaf779c9c2f54 | ansible runner extensions                 | src/general_ludd/ansible/core_runner.py, ansible/result_parser.py, ansible/runner.py, ansible/runstate.py, tests/unit/test_ansible_output.py                                  | NO            | NO        | NONE (existing ansible package)        |
| 9 | a3d19f1fef2b16a51 | sandbox                                   | src/general_ludd/sandbox/__init__.py, sandbox/policy.py, sandbox/runner.py, tests/unit/test_sandbox.py                                                                        | NO            | NO        | sandbox/__init__.py                    |
|10 | a42a44311316d2184 | eval harness                              | src/general_ludd/eval/__init__.py, eval/baseline.py, eval/harness.py, eval/model.py, eval/scorers.py, tests/unit/test_eval_harness.py                                         | NO            | NO        | eval/__init__.py                       |
|11 | a5c5b105c4a51621b | context window                            | src/general_ludd/context/__init__.py, context/compact.py, context/items.py, context/window.py, tests/unit/test_context_window.py                                              | NO            | NO        | context/__init__.py                    |
|12 | a6097cdf37d2dbd1f | config_schema                             | src/general_ludd/config_schema/__init__.py, config_schema/migration.py, config_schema/schema.py, tests/unit/test_config_schema.py                                             | NO            | NO        | config_schema/__init__.py              |
|13 | a61d263b97ab0dc3a | orchestration_guards                      | src/general_ludd/agents/dispatcher.py, agents/orchestration_guards.py, tests/unit/test_orchestration_guards.py                                                                 | NO            | NO        | NONE (existing agents package)         |
|14 | a65da5d8a220af983 | hitl                                      | src/general_ludd/hitl/__init__.py, hitl/classifier.py, hitl/gate.py, hitl/models.py, hitl/store.py, tests/unit/test_hitl_gate.py                                              | NO            | NO        | hitl/__init__.py                       |
|15 | a6658bf05a69d4d36 | git ansible playbooks + capability_policy | collections/ansible_collections/.../capability_policy.py, gludd_git_lock.py, gludd_git.py; playbooks/git_automate_change.yml, git_manage_worktree.yml, git_repo_init.yml; tests/security/test_gludd_git_lock.py | NO | NO | NONE (new files in existing dirs)      |
|16 | a7af6fa1b17155947 | backlog-audit system                      | **Makefile**, scripts/backlog_audit.py, src/general_ludd/quality/bug_class_registry.py, src/general_ludd/validation/backlog_auditor.py, tests/unit/test_backlog_audit_system.py | NO          | **YES**   | NONE (existing packages)               |
|17 | a819a1994d313cede | accounting + RoleRunModel cols (DB)       | src/general_ludd/accounting/git_loc.py, accounting/ledger.py, **db/models.py**, db/repository.py, routers/accounting.py, routers/projects.py, tests/unit/test_project_ledger_endpoint.py, test_project_ledger.py | **YES** | NO | NONE (existing packages) |
|18 | a9069b96e915aefe4 | rate_limit                                | src/general_ludd/rate_limit/__init__.py, rate_limit/_clock.py, rate_limit/_quota.py, rate_limit/_registry.py, rate_limit/_sliding_window.py, rate_limit/_token_bucket.py, tests/unit/test_rate_limit.py | NO | NO | rate_limit/__init__.py |
|19 | a90aaa2c4daeca7b9 | self_improve outcome_loop                 | src/general_ludd/self_improve/__init__.py, self_improve/outcome_loop.py, tests/unit/test_outcome_loop.py                                                                       | NO            | NO        | self_improve/__init__.py               |
|20 | aa164deb29a0c5388 | output_schema                             | src/general_ludd/output_schema/__init__.py, output_schema/schema.py, tests/unit/test_output_schema.py                                                                         | NO            | NO        | output_schema/__init__.py              |
|21 | aa97d93fee26d8c33 | cost_report                               | src/general_ludd/cost_report/__init__.py, cost_report/builder.py, cost_report/models.py, tests/unit/test_cost_report.py                                                       | NO            | NO        | cost_report/__init__.py                |
|22 | abc58f5c2ecd41ce0 | worktree race fix                         | src/general_ludd/worktree/core.py, tests/unit/test_worktree_race.py                                                                                                           | NO            | NO        | NONE (existing worktree package)       |
|23 | ac70c3cf92b4f9710 | pareto routing                            | src/general_ludd/scoring/pareto.py, tests/unit/test_pareto_routing.py                                                                                                        | NO            | NO        | NONE (existing scoring package)        |
|24 | ac8a21b1baf0caf4d | patch_apply                               | src/general_ludd/patch_apply/__init__.py, patch_apply/_search_replace.py, patch_apply/_types.py, patch_apply/_unified_diff.py, patch_apply/apply.py, tests/unit/test_patch_apply.py | NO | NO | patch_apply/__init__.py |
|25 | acc72dca83d683b27 | repro                                     | src/general_ludd/repro/__init__.py, repro/_env.py, repro/manager.py, repro/spec.py, tests/unit/test_repro.py                                                                  | NO            | NO        | repro/__init__.py                      |
|26 | adf7b74d0cec9be30 | redaction                                 | src/general_ludd/redaction/__init__.py, redaction/core.py, tests/unit/test_redaction.py                                                                                       | NO            | NO        | redaction/__init__.py                  |
|27 | ae13d66c078d6dc4e | molecule_coverage quality tool            | molecule/playbooks/role_self_improve_propose/default/converge.yml, test_gludd_reload/{converge,prepare,verify}.yml, src/general_ludd/quality/molecule_coverage.py, tests/integration/test_molecule_coverage.py, tests/unit/test_molecule_coverage.py, tests/unit/test_quality_tools.py, tools/check_molecule_coverage.py | NO | NO | NONE (existing packages) |
|28 | aea69fdc640ab858c | resilience policy                         | src/general_ludd/resilience/__init__.py, resilience/policy.py, tests/unit/test_resilience.py                                                                                  | NO            | NO        | resilience/__init__.py                 |
|29 | aebcd6c3962503ba3 | consensus                                 | src/general_ludd/consensus/__init__.py, consensus/decide.py, consensus/models.py, consensus/strategies.py, tests/unit/test_consensus.py                                       | NO            | NO        | consensus/__init__.py                  |
|30 | af3f724a33bdb10aa | event_bus                                 | src/general_ludd/event_bus/__init__.py, event_bus/bus.py, event_bus/types.py, tests/unit/test_event_bus_package.py                                                            | NO            | NO        | event_bus/__init__.py                  |
|31 | af52558f397351d3f | planner                                   | src/general_ludd/planner/__init__.py, planner/core.py, tests/unit/test_goal_planner.py                                                                                        | NO            | NO        | planner/__init__.py                    |
|32 | af9a1ad7ba6755bb8 | provenance                                | src/general_ludd/provenance/__init__.py, provenance/models.py, provenance/tracker.py, tests/unit/test_provenance.py                                                           | NO            | NO        | provenance/__init__.py                 |
|33 | afd7934f7e17c4213 | run_timeline                              | src/general_ludd/run_timeline/__init__.py, run_timeline/builder.py, run_timeline/models.py, run_timeline/renderer.py, tests/unit/test_run_timeline.py                         | NO            | NO        | run_timeline/__init__.py               |

---

## 2. db/models.py multi-edit merge sequence

**Two worktrees touch `src/general_ludd/db/models.py`:**

### Worktree 5 — a24b0cb9a03d0b24d (memory / MemoryRecordModel)
- **Change type:** Pure EOF-append. Adds helper `_gen_memory_id()` and `class MemoryRecordModel(Base)` after the final `BenchmarkResultModel` class (line 523 in master).
- **New table:** `memory_records` — columns: id, scope, scope_key, kind, text, tags, source, embedding, created_at, last_used_at.
- **STATUS: ALREADY APPLIED** via `make wt-apply` during this audit run. The `M` (staged) marker for `db/models.py` in `make git-status` confirms it is in the working tree. No further action needed for this worktree's db edit.

### Worktree 17 — a819a1994d313cede (accounting / RoleRunModel columns)
- **Change type:** Mid-file column addition to the existing `RoleRunModel` class (lines 471-493 in master). Adds two nullable columns:
  - `success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)`
  - `duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)`
- **New table:** NONE. This is a column addition to `role_runs` (existing table).
- **Conflict risk:** The two edits are in disjoint regions (MemoryRecordModel appends at EOF; RoleRunModel cols modify lines ~480-492). After wt-5 is applied, wt-17's diff will 3-way merge cleanly because the base is unchanged in the RoleRunModel region.
- **ACTION REQUIRED:** Run `make wt-apply SRC=.../agent-a819a1994d313cede FILES=src/general_ludd/db/models.py` AFTER the MemoryRecordModel change is committed. This applies cleanly (disjoint hunks).

### Merge sequence for db/models.py:
1. DONE: `make wt-apply SRC=.../agent-a24b0cb9a03d0b24d FILES=src/general_ludd/db/models.py` (MemoryRecordModel — already applied)
2. COMMIT the current main working tree (includes MemoryRecordModel).
3. `make wt-apply SRC=.../agent-a819a1994d313cede FILES=src/general_ludd/db/models.py` (RoleRunModel cols — applies cleanly post-step-2)
4. After both applied, run `make wt-sync SRC=.../agent-a819a1994d313cede` for the remaining non-models.py files from that worktree (accounting/git_loc.py, accounting/ledger.py, db/repository.py, routers/accounting.py, routers/projects.py, and test files).

---

## 3. `__init__.py` hand-place checklist (CRITICAL — wt-sync SKIPS these)

`wt-sync` explicitly skips `*/__init__.py` files (Makefile line 718-719: `*/__init__.py|__init__.py) continue`). Every new package must have its `__init__.py` hand-placed after `wt-sync`. Failure to do this caused 3 collection errors in batch-3.

For each worktree below, after `make wt-sync SRC=<dir>`, manually copy the `__init__.py` using `make wt-import`:

```markdown
make wt-import SRC=.../agent-a075db9607378180d/src/general_ludd/audit_log/__init__.py \
               DST=src/general_ludd/audit_log/__init__.py

make wt-import SRC=.../agent-a0789ea341bb2cf84/src/general_ludd/preview/__init__.py \
               DST=src/general_ludd/preview/__init__.py

make wt-import SRC=.../agent-a0f2cca1f2e058861/src/general_ludd/retrieval/__init__.py \
               DST=src/general_ludd/retrieval/__init__.py

make wt-import SRC=.../agent-a1aeefc974084b154/src/general_ludd/prompt_versioning/__init__.py \
               DST=src/general_ludd/prompt_versioning/__init__.py

make wt-import SRC=.../agent-a24b0cb9a03d0b24d/src/general_ludd/memory/__init__.py \
               DST=src/general_ludd/memory/__init__.py

make wt-import SRC=.../agent-a257c4c0fd60a0b9b/src/general_ludd/tool_registry/__init__.py \
               DST=src/general_ludd/tool_registry/__init__.py

make wt-import SRC=.../agent-a27188d287234a5ab/src/general_ludd/replay/__init__.py \
               DST=src/general_ludd/replay/__init__.py

make wt-import SRC=.../agent-a3d19f1fef2b16a51/src/general_ludd/sandbox/__init__.py \
               DST=src/general_ludd/sandbox/__init__.py

make wt-import SRC=.../agent-a42a44311316d2184/src/general_ludd/eval/__init__.py \
               DST=src/general_ludd/eval/__init__.py

make wt-import SRC=.../agent-a5c5b105c4a51621b/src/general_ludd/context/__init__.py \
               DST=src/general_ludd/context/__init__.py

make wt-import SRC=.../agent-a6097cdf37d2dbd1f/src/general_ludd/config_schema/__init__.py \
               DST=src/general_ludd/config_schema/__init__.py

make wt-import SRC=.../agent-a65da5d8a220af983/src/general_ludd/hitl/__init__.py \
               DST=src/general_ludd/hitl/__init__.py

make wt-import SRC=.../agent-a9069b96e915aefe4/src/general_ludd/rate_limit/__init__.py \
               DST=src/general_ludd/rate_limit/__init__.py

# SKIP: self_improve/__init__.py already exists in main (package predates batch-4)
# wt-sync will transfer outcome_loop.py only; do NOT run wt-import for this __init__.py

make wt-import SRC=.../agent-aa164deb29a0c5388/src/general_ludd/output_schema/__init__.py \
               DST=src/general_ludd/output_schema/__init__.py

make wt-import SRC=.../agent-aa97d93fee26d8c33/src/general_ludd/cost_report/__init__.py \
               DST=src/general_ludd/cost_report/__init__.py

make wt-import SRC=.../agent-ac8a21b1baf0caf4d/src/general_ludd/patch_apply/__init__.py \
               DST=src/general_ludd/patch_apply/__init__.py

make wt-import SRC=.../agent-acc72dca83d683b27/src/general_ludd/repro/__init__.py \
               DST=src/general_ludd/repro/__init__.py

make wt-import SRC=.../agent-adf7b74d0cec9be30/src/general_ludd/redaction/__init__.py \
               DST=src/general_ludd/redaction/__init__.py

make wt-import SRC=.../agent-aea69fdc640ab858c/src/general_ludd/resilience/__init__.py \
               DST=src/general_ludd/resilience/__init__.py

make wt-import SRC=.../agent-aebcd6c3962503ba3/src/general_ludd/consensus/__init__.py \
               DST=src/general_ludd/consensus/__init__.py

make wt-import SRC=.../agent-af3f724a33bdb10aa/src/general_ludd/event_bus/__init__.py \
               DST=src/general_ludd/event_bus/__init__.py

make wt-import SRC=.../agent-af52558f397351d3f/src/general_ludd/planner/__init__.py \
               DST=src/general_ludd/planner/__init__.py

make wt-import SRC=.../agent-af9a1ad7ba6755bb8/src/general_ludd/provenance/__init__.py \
               DST=src/general_ludd/provenance/__init__.py

make wt-import SRC=.../agent-afd7934f7e17c4213/src/general_ludd/run_timeline/__init__.py \
               DST=src/general_ludd/run_timeline/__init__.py
```

**No new `__init__.py` needed for:**
- a3bcdaf779c9c2f54 (ansible — existing package)
- a61d263b97ab0dc3a (agents — existing package)
- a6658bf05a69d4d36 (collections/modules — no Python package __init__)
- a7af6fa1b17155947 (quality/validation — existing packages)
- a819a1994d313cede (accounting/db — existing packages, accounting dir may need check below)
- a819a1994d313cede accounting dir: VERIFY if `src/general_ludd/accounting/` is a new package or existing. If new: also run `make wt-import SRC=.../agent-a819a1994d313cede/src/general_ludd/accounting/__init__.py DST=src/general_ludd/accounting/__init__.py`
- a819a1994d313cede DB subpackage: already has db/__init__.py in main
- abc58f5c2ecd41ce0 (worktree — existing package)
- ac70c3cf92b4f9710 (scoring — existing package)
- ae13d66c078d6dc4e (quality/tools — existing packages)

**accounting package (a819a1994d313cede) — VERIFIED EXISTING:**
`make grep Q="general_ludd.accounting"` confirms `src/general_ludd/accounting/__init__.py` is already compiled in main (`__pycache__/__init__.cpython-311.pyc` present). No `__init__.py` hand-place needed for accounting. wt-sync will transfer `git_loc.py` and `ledger.py` additions cleanly.

---

## 4. Makefile target-append list

**Only one worktree (a7af6fa1b17155947) touches the Makefile.**

Change type: EOF-append. Adds:
- PHONY addition: `backlog-audit`
- New target `backlog-audit:` (lines 1383-1387 in the worktree Makefile) — calls `scripts/backlog_audit.py` with optional `BACKLOG=` and `NO_COLLECT=1` flags.
- `backlog_audit.py` script also modified (related changes).

The current main Makefile ends after `release-validate:` at line 1471. There is no conflict — the worktree change is a clean append.

**Action:** After all pure-new-file wt-syncs are done and committed, apply the Makefile via:
```text
make wt-apply SRC=.../agent-a7af6fa1b17155947 FILES=Makefile
```
Then also sync the remaining files from that worktree:
```text
make wt-sync SRC=.../agent-a7af6fa1b17155947
```
(wt-sync will transfer the non-Makefile, non-__init__ files; the Makefile was pre-applied by wt-apply.)

---

## 5. Molecule playbook changes (ae13d66c078d6dc4e)

This worktree modifies existing molecule playbook files that are also modified in the main working tree (noted in `make git-diff` output: molecule/playbooks/role_self_improve_propose and test_gludd_reload are in the main tree's diff). This is a **CLOBBER HAZARD** — `wt-sync` will refuse these files (clobber guard active), which is correct.

**Action:** Use `wt-apply --3way` for the molecule files:
```text
make wt-apply SRC=.../agent-ae13d66c078d6dc4e \
  FILES='molecule/playbooks/role_self_improve_propose/default/converge.yml \
         molecule/playbooks/test_gludd_reload/default/converge.yml \
         molecule/playbooks/test_gludd_reload/default/prepare.yml \
         molecule/playbooks/test_gludd_reload/default/verify.yml'
```
If there are 3-way conflicts, resolve manually keeping both sets of changes. The wt's new-file additions (quality/molecule_coverage.py, tools/check_molecule_coverage.py, test files) are pure-new and can be synced normally.

---

## 6. Safe reap ORDER

Integrate in this sequence to minimize conflict risk:

### Pass 1 — Pure new-package worktrees (no shared-file edits; wt-sync is safe)
These worktrees only add new files. Run `make wt-sync` then hand-place `__init__.py` files, then `make test-count` after each batch.

```markdown
# Batch A — pure greenfield packages
make wt-sync SRC=.../agent-a075db9607378180d   # audit_log
make wt-import SRC=.../agent-a075db9607378180d/src/general_ludd/audit_log/__init__.py DST=src/general_ludd/audit_log/__init__.py

make wt-sync SRC=.../agent-a0789ea341bb2cf84   # preview
make wt-import SRC=.../agent-a0789ea341bb2cf84/src/general_ludd/preview/__init__.py DST=src/general_ludd/preview/__init__.py

make wt-sync SRC=.../agent-a0f2cca1f2e058861   # retrieval
make wt-import SRC=.../agent-a0f2cca1f2e058861/src/general_ludd/retrieval/__init__.py DST=src/general_ludd/retrieval/__init__.py

make wt-sync SRC=.../agent-a1aeefc974084b154   # prompt_versioning
make wt-import SRC=.../agent-a1aeefc974084b154/src/general_ludd/prompt_versioning/__init__.py DST=src/general_ludd/prompt_versioning/__init__.py

make wt-sync SRC=.../agent-a257c4c0fd60a0b9b   # tool_registry
make wt-import SRC=.../agent-a257c4c0fd60a0b9b/src/general_ludd/tool_registry/__init__.py DST=src/general_ludd/tool_registry/__init__.py

make wt-sync SRC=.../agent-a27188d287234a5ab   # replay
make wt-import SRC=.../agent-a27188d287234a5ab/src/general_ludd/replay/__init__.py DST=src/general_ludd/replay/__init__.py

make wt-sync SRC=.../agent-a3d19f1fef2b16a51   # sandbox
make wt-import SRC=.../agent-a3d19f1fef2b16a51/src/general_ludd/sandbox/__init__.py DST=src/general_ludd/sandbox/__init__.py

make wt-sync SRC=.../agent-a42a44311316d2184   # eval
make wt-import SRC=.../agent-a42a44311316d2184/src/general_ludd/eval/__init__.py DST=src/general_ludd/eval/__init__.py

make wt-sync SRC=.../agent-a5c5b105c4a51621b   # context
make wt-import SRC=.../agent-a5c5b105c4a51621b/src/general_ludd/context/__init__.py DST=src/general_ludd/context/__init__.py

make wt-sync SRC=.../agent-a6097cdf37d2dbd1f   # config_schema
make wt-import SRC=.../agent-a6097cdf37d2dbd1f/src/general_ludd/config_schema/__init__.py DST=src/general_ludd/config_schema/__init__.py

make wt-sync SRC=.../agent-a65da5d8a220af983   # hitl
make wt-import SRC=.../agent-a65da5d8a220af983/src/general_ludd/hitl/__init__.py DST=src/general_ludd/hitl/__init__.py

make wt-sync SRC=.../agent-a9069b96e915aefe4   # rate_limit
make wt-import SRC=.../agent-a9069b96e915aefe4/src/general_ludd/rate_limit/__init__.py DST=src/general_ludd/rate_limit/__init__.py

make wt-sync SRC=.../agent-a90aaa2c4daeca7b9   # self_improve (outcome_loop)
make wt-import SRC=.../agent-a90aaa2c4daeca7b9/src/general_ludd/self_improve/__init__.py DST=src/general_ludd/self_improve/__init__.py

make wt-sync SRC=.../agent-aa164deb29a0c5388   # output_schema
make wt-import SRC=.../agent-aa164deb29a0c5388/src/general_ludd/output_schema/__init__.py DST=src/general_ludd/output_schema/__init__.py

make wt-sync SRC=.../agent-aa97d93fee26d8c33   # cost_report
make wt-import SRC=.../agent-aa97d93fee26d8c33/src/general_ludd/cost_report/__init__.py DST=src/general_ludd/cost_report/__init__.py

make wt-sync SRC=.../agent-ac8a21b1baf0caf4d   # patch_apply
make wt-import SRC=.../agent-ac8a21b1baf0caf4d/src/general_ludd/patch_apply/__init__.py DST=src/general_ludd/patch_apply/__init__.py

make wt-sync SRC=.../agent-acc72dca83d683b27   # repro
make wt-import SRC=.../agent-acc72dca83d683b27/src/general_ludd/repro/__init__.py DST=src/general_ludd/repro/__init__.py

make wt-sync SRC=.../agent-adf7b74d0cec9be30   # redaction
make wt-import SRC=.../agent-adf7b74d0cec9be30/src/general_ludd/redaction/__init__.py DST=src/general_ludd/redaction/__init__.py

make wt-sync SRC=.../agent-aea69fdc640ab858c   # resilience
make wt-import SRC=.../agent-aea69fdc640ab858c/src/general_ludd/resilience/__init__.py DST=src/general_ludd/resilience/__init__.py

make wt-sync SRC=.../agent-aebcd6c3962503ba3   # consensus
make wt-import SRC=.../agent-aebcd6c3962503ba3/src/general_ludd/consensus/__init__.py DST=src/general_ludd/consensus/__init__.py

make wt-sync SRC=.../agent-af3f724a33bdb10aa   # event_bus
make wt-import SRC=.../agent-af3f724a33bdb10aa/src/general_ludd/event_bus/__init__.py DST=src/general_ludd/event_bus/__init__.py

make wt-sync SRC=.../agent-af52558f397351d3f   # planner
make wt-import SRC=.../agent-af52558f397351d3f/src/general_ludd/planner/__init__.py DST=src/general_ludd/planner/__init__.py

make wt-sync SRC=.../agent-af9a1ad7ba6755bb8   # provenance
make wt-import SRC=.../agent-af9a1ad7ba6755bb8/src/general_ludd/provenance/__init__.py DST=src/general_ludd/provenance/__init__.py

make wt-sync SRC=.../agent-afd7934f7e17c4213   # run_timeline
make wt-import SRC=.../agent-afd7934f7e17c4213/src/general_ludd/run_timeline/__init__.py DST=src/general_ludd/run_timeline/__init__.py
```

**Worktrees that modify existing files (no new __init__ needed):**
```text
make wt-sync SRC=.../agent-a3bcdaf779c9c2f54   # ansible runner extensions
make wt-sync SRC=.../agent-a61d263b97ab0dc3a   # orchestration_guards (agents package)
make wt-sync SRC=.../agent-abc58f5c2ecd41ce0   # worktree core fix
make wt-sync SRC=.../agent-ac70c3cf92b4f9710   # pareto routing
```

**Ansible/git playbooks (new files in existing collection dirs):**
```text
make wt-sync SRC=.../agent-a6658bf05a69d4d36   # git ansible playbooks
```

### Pass 2 — db/models.py via wt-apply (ordered, after Pass 1 committed)

Step 1 (memory) is already applied. After the full batch-3 + Pass-1 commit:
```markdown
# Step 2: accounting RoleRunModel column additions
make wt-apply SRC=.../agent-a819a1994d313cede FILES=src/general_ludd/db/models.py
# Step 3: sync remaining accounting files (non-models.py)
make wt-sync SRC=.../agent-a819a1994d313cede
# accounting/__init__.py is NOT needed — accounting package already exists in main (verified)
```

### Pass 3 — Makefile EOF-append (after Pass 2 committed)
```markdown
make wt-apply SRC=.../agent-a7af6fa1b17155947 FILES=Makefile
make wt-sync SRC=.../agent-a7af6fa1b17155947
# backlog_audit.py already modified in main tree (see git-status M scripts/backlog_audit.py)
# wt-sync will refuse it with clobber guard if main already has different content
# Use: make wt-apply SRC=.../agent-a7af6fa1b17155947 FILES=scripts/backlog_audit.py
```

### Pass 4 — Molecule files (3-way merge needed)
```markdown
# ae13d66c078d6dc4e: molecule/quality tool
# These molecule files are ALSO modified in the main tree; wt-sync will refuse them.
# Use wt-apply for each conflicting molecule file individually:
make wt-apply SRC=.../agent-ae13d66c078d6dc4e \
  FILES=molecule/playbooks/role_self_improve_propose/default/converge.yml
make wt-apply SRC=.../agent-ae13d66c078d6dc4e \
  FILES=molecule/playbooks/test_gludd_reload/default/converge.yml
make wt-apply SRC=.../agent-ae13d66c078d6dc4e \
  FILES=molecule/playbooks/test_gludd_reload/default/prepare.yml
make wt-apply SRC=.../agent-ae13d66c078d6dc4e \
  FILES=molecule/playbooks/test_gludd_reload/default/verify.yml
# Pure-new files from this worktree (no conflict):
make wt-sync SRC=.../agent-ae13d66c078d6dc4e
# Also: tests/integration/test_molecule_coverage.py is modified in main — use wt-apply
make wt-apply SRC=.../agent-ae13d66c078d6dc4e \
  FILES=tests/integration/test_molecule_coverage.py
```

---

## 7. Gate-readiness notes

- `make test-count` MUST be run after every `__init__.py` hand-placement and after every wt-sync. A missing `__init__.py` yields a collection ERROR (not a test failure — the gate's collect-check phase catches it, but a silent `make test` could report "0 failures" on a broken suite).
- `db/models.py` step-2 (RoleRunModel cols) is safe only after the MemoryRecordModel addition is committed — the 3-way base must include MemoryRecordModel so the patch applies to the right context lines.
- The `scripts/backlog_audit.py` file appears in BOTH the main working tree (` M scripts/backlog_audit.py`) AND in worktree a7af6fa1 — wt-sync will refuse it. Use `make wt-apply` for that file specifically.
- Worktree ae13d66c's `tests/integration/test_molecule_coverage.py` conflicts with main's modified version — must use wt-apply, not wt-sync.
- All 33 worktrees are at commit 5f7a453 (master HEAD), so no worktree has diverged from master; all diffs are purely uncommitted working-tree changes.

---

## 8. Accounting package __init__.py verification

Before syncing a819a1994d313cede, verify whether `accounting/` already exists in main:

```text
make grep Q="from general_ludd.accounting" PATH_=src
```

If no results: `accounting/` is a NEW package — hand-place `__init__.py` after wt-sync.
If results exist: package already present — no hand-place needed.
