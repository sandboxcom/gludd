# Audit Findings Re-Triage — 2026-07-13

Re-triage of `docs/audit/BACKLOG_FINDINGS_2026-07-01.md` and
`docs/audit/NEW_FINDINGS_TRIAGE_2026-06-18.md` against current master
(HEAD: `5a152695`). Each item verified by reading current source and
cross-referencing with `AGENTIC_IMPLEMENTATION_SPEC.md` §3.0.

---

## Source: BACKLOG_FINDINGS_2026-07-01.md

### 2026-07-11 state → 2026-07-13 re-triage changes

5 items changed status since the 2026-07-11 re-triage (commits `3aec400b`..`5a152695`):

| # | Finding | 07-11 → 07-13 | Evidence |
|---|---------|---------------|----------|
| 1 | **Ansible process_isolation silent no-op** (podman-PRESENT unconfined) | OPEN → **FIXED** | `core_runner.py:294-313` now delegates to `_execute_with_runner()` which spawns container confinement via ansible-runner. Comment at :294 confirms "Wave 13 fail-closed guard replaced by actual confinement." |
| 2 | **Deny-list leading-slash drift** (C9/C14) | OPEN → **FIXED** | `security/path_canonicalizer.py` now consolidates all deny markers into a single canonical source: `PROTECTED_PATH_SUBSTRINGS` (slash-anchored), `PROTECTED_PATH_SEGMENTS` (bare segments), and `_HARD_DENY_SEGMENTS` (for apply path). Relative `.claude/` / `.opencode/` paths are matched by segment-based check. All three enforcement sites (capability_lattice, applier, apply) consume from this canonical set. |
| 3 | **Git merge_branch bypass** (C17) | OPEN → **FIXED** | `git_automation/repo.py:743-789`: `merge_branch` now uses `git_repo_lock` (line 761), `_reject_leading_dash` guards on both source+target, `check=True` on `_run_git` (fail-closed), and `--` option termination. C17 tests (8) landed in `fdb40722`. |
| 4 | **Self-improve auto_queue bypass** (C13) | OPEN → **FIXED** | `self_improve/gate.py`: `auto_queue` removed (docstring at :11-15 confirms). Gate now always returns `APPROVAL_REQUIRED`. `SelfImproveApprovalManager` wired as the only release path. |
| 5 | **Worker broadcast PSK leak** (C8) | OPEN → **MITIGATED** | `reload/worker_broadcast.py:16-29`: `_is_safe_worker_address` delegates to canonical `is_safe_fetch_url` (https-only + literal-host deny) before PSK Bearer header is sent. SSRF guard now on the hot path. Remaining: no cryptographic signature on inter-worker auth. |

### Final tally (BACKLOG_FINDINGS)

| Status | 2026-07-11 | 2026-07-13 | Delta |
|--------|-----------|-----------|-------|
| FIXED | 8 | **12** | +4 |
| OPEN | 19 | **14** | -5 |
| REFUTED | 3 | 3 | 0 |
| MITIGATED | 1 | **2** | +1 |
| PARTIALLY FIXED | 0 | **1** | +1 |

### Remaining OPEN items (14)

- Ansible: per-project secret isolation (`for_project` still 0 callers in `secrets/`)
- Security: C5 (integrity store unsigned), C14 (capability lattice widening / STS re-delegation), C15 (ToolCallLoop capability bypass)
- Workers: C20 (worker fail-open auth default)
- Reload: C8 (TOCTOU, rate-limit, symlink bypass)
- Self-update: C9 (parent-dir TOCTOU, cwd resolve)
- Execution: C10 (async create_task in sync, blocking on loop, background_tasks drain)
- Event Loop: C11 (DB session pinned across exec, ThreadPool saturation)
- Events: C12 (hooks list iteration, EventBus locking)
- Accounting: C18 (blocking subprocess, no tenant scoping)
- Runtime: bundle manifests unsigned
- Remediation: C25 (no idempotency guard)
- Gateway: C28 (call_model_with_fallback — PARTIALLY FIXED: health-gated, budget threaded, cause chain preserved; remaining: semaphore acquisition timing)

---

## Source: NEW_FINDINGS_TRIAGE_2026-06-18.md

### 2026-07-11 state → 2026-07-13 re-triage changes

2 items changed status since the 2026-07-11 re-triage:

| # | Finding | 07-11 → 07-13 | Evidence |
|---|---------|---------------|----------|
| 1 | **#12: `call_model_with_fallback` ignores `is_healthy`** | OPEN → **PARTIALLY FIXED** | `gateway.py:1742-1819` now: (a) gates primary on `is_healthy` before calling (line 1755-1758), (b) threads budget through call chain (line 1760-1761), (c) `CircuitBreakerOpenError` preserves cause chain via `raise from last_exc` (line 1815). Remaining: semaphore `acquire()` has no timeout, exception detail is reduced. Tracked as C28 residual. |
| 2 | **#14: `TodoModel.version` not wired as `version_id_col`** | PARTIAL → **FIXED** | `db/models.py:289`: `__mapper_args__ = {"version_id_col": version}` — now wired. CAS repository guard at `repository.py:277-315` is the primary concurrency guard; `version_id_col` is now defense-in-depth. |

### Final tally (NEW_FINDINGS_TRIAGE)

| Status | 2026-07-11 | 2026-07-13 | Delta |
|--------|-----------|-----------|-------|
| FIXED (High) | 11 | **12** | +1 |
| FIXED (Medium) | 2 | 2 | 0 |
| OPEN (High) | 2 | **1** | -1 |
| PARTIALLY FIXED | 1 | **2** | +1 |
| OPEN (Medium) | 0 | 0 | 0 |

### Remaining OPEN/PARTIALLY FIXED items (3)

- **#9: Daemon auth fail-open default** — PARTIALLY FIXED. `_is_public` at `daemon.py:2652` uses `startswith("/docs/")` (over-matches `/docs-secret`). Default (no PSK + no `GLUDD_REQUIRE_AUTH`) is still fail-open. Worker mirrors same pattern (`worker/app.py:278`). Tracked as C20.
- **#12: `call_model_with_fallback`** — PARTIALLY FIXED (see above). Tracked as C28.
- **#14: `TodoModel.version` wiring** — FIXED (see above).

---

## Cross-referenced with AGENTIC_IMPLEMENTATION_SPEC.md §3.0

All FIXED items above are independently verified in current source (not relying
on claim alone). The `AGENTIC_IMPLEMENTATION_SPEC.md` §3.0 already-fixed list
was cross-checked and matches.

## Verification method

Each item verified by:
1. Reading the cited file:line in current master
2. Checking for the specific code pattern described as the fix
3. Cross-referencing commit messages in `make git-log` (range `3aec400b..5a152695`)
4. Marking only when code evidence was observed (not inferred from commit messages alone)
