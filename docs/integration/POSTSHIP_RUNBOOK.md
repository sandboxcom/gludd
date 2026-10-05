# POSTSHIP_RUNBOOK — after batch 2 commits

Executable runbook. Per cycle: apply edits → `make ship FILES='...' MSG_FILE=<msg>` → on **SHIP-RESULT=PASS** proceed. Bash is make-only; edits via Read/Edit/Write tools.

`make ship` (Makefile, verified): phase A gate (test BEFORE commit) → phase B `git-add FILES` → phase C `git-commit-file` (re-checks `.gate-status` is all-PASS and <30 min old). MSG_FILE should be an absolute path. Prints `SHIP-RESULT=PASS <short-sha>` on success, else `SHIP-RESULT=FAIL {gate|commit|usage(...)}`. Only proceed on PASS.

---

## CYCLE A — Port (one `make ship`)

Port gated-commit/gated-merge support end to end.

1. `git_automation/types.py` — add `GatedCommitResult`:
   `success: bool`, `commit_sha: str | None = None`, `gate_returncode: int = 0`, `message: str = ""`.
2. `git_automation/__init__.py` — export `GatedCommitResult`.
3. `git_automation/repo.py` — add `gated_commit` + `gated_merge`; argv form `shell=False`; mypy-strict (every path returns).
4. `collections/.../plugins/modules/gludd_git.py` — add `gated_commit` / `gated_merge` ops; `gate_command` as list argv.
5. `roles/validate_and_push/tasks/main.yml` — add gated path.
6. NEW `tests/unit/test_git_automation_gated_commit.py`.
7. NEW `tests/unit/test_ship_failsafe.py`.

**FILES (Cycle A):**
```text
git_automation/types.py git_automation/__init__.py git_automation/repo.py collections/.../plugins/modules/gludd_git.py roles/validate_and_push/tasks/main.yml tests/unit/test_git_automation_gated_commit.py tests/unit/test_ship_failsafe.py
```

**Ship:**
```text
make ship FILES='<Cycle A FILES>' MSG_FILE=.commit-msg-batch3.txt
```
On **SHIP-RESULT=PASS** → proceed to Cycle B.

---

## CYCLE B — Batch-3 fixes (one `make ship`)

Real module paths verified against repo `BATCH3_APPLY_PLAN.md` (basenames in prompt; full paths below).

1. `agents/capabilities.py` — docstring (prepare_messages).
2. `runtime/release.py` — assert LICENSE present in manifest (_check_pip_bundle).
3. `routers/accounting.py` — `total_tokens` (real token counting).
4. `issue_sources/markdown_todo.py` — escape `--&gt;`.
5. `connectors/elastic_apm.py` — `follow_redirects=False` (verified MISSING today; httpx defaults to follow → fix is real).
6. `auth.py` — rename `is_path_within` → `is_join_within` (+ keep alias); update `__init__.py` export. No caller changes needed (alias covers them).

**FILES (Cycle B):**
```text
agents/capabilities.py runtime/release.py routers/accounting.py issue_sources/markdown_todo.py connectors/elastic_apm.py auth.py __init__.py
```

**Ship:**
```text
make ship FILES='<Cycle B FILES>' MSG_FILE=.commit-msg-batch3.txt
```
On **SHIP-RESULT=PASS** → done.

---

## DEFERRED — batch-4 (see `BATCH4_DEFERRED.md`)

Not in this runbook; tracked for a later cycle:
- `variable_store` encoding.
- redirect guards: tempo / zipkin / newrelic.
- `secrets` `resolve_required`.
- cassandra / clickhouse executor guard.

Note: SSRF init-guard is **NOT** needed — query-time path is already correct.
