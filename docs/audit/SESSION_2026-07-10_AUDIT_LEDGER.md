# Session 2026-07-10 Audit Ledger

Consolidated remediation ledger for this session's confirmed security/robustness
findings. Every citation below was re-read against the tree on 2026-07-10 and
corrected where it had drifted (drift notes inline). **No fixes have been
applied yet** — this is a prioritized backlog only. Do not treat any item here
as landed until a corresponding commit + evidence line exists in `TASKS.md`.

Total: **21** findings — 1 CRITICAL, 3 HIGH, 8 MEDIUM, 9 LOW (LOW section
also folds in the two extra sub-findings surfaced during verification: the
priority-clamp gap actually lives in `routers/self_improve.py`, not a
top-level `self_improve.py`; and the file-claim registry is *partially*,
not purely, advisory).

---

## CRITICAL

### A-MEMORY-CROSS-PROJECT-BLEED
**Severity:** CRITICAL
**Files:**
- `src/general_ludd/db/models.py:731-757` (`MemoryRecordModel` — no `project_id` column; `UniqueConstraint("agent_id","key","namespace")` only, confirmed verbatim)
- `src/general_ludd/routers/memory.py:63-93` (`POST /api/memory`, `GET /api/memory/{agent_id}`, `DELETE /api/memory/{agent_id}/{key}` — `agent_id`/`namespace` taken from untrusted request body/path/query with no auth dependency injected in this router)
- `src/general_ludd/event_loop/loop.py:4350-4380` (`_auto_record_episode` — `agent_id` falls back to shared `work_type`; real `project_id` is only stashed inside the `context={...}` JSON blob at line 4380, never used to scope the write)
- `src/general_ludd/db/repository.py:2822-2841` (`list_by_namespace`; the `namespace == "*"` wildcard bypass is at line 2837-2838, reachable from the HTTP `namespace` query param)
- `src/general_ludd/daemon.py:1612-1613` (single shared `MemoryRepository(session_factory=session_factory)` instance stored on `app.state._memory_repo` — no per-project sharding)

**Failure scenario:** Any HTTP caller (or any agent whose `agent_id`/`work_type` collides with another project's) can read or overwrite another project's persistent agent memory — including via the documented `namespace=*` wildcard — because nothing in the storage layer, the API, or the episodic-write path scopes records by `project_id`.

**Fix sketch:** Add a `project_id` column to `MemoryRecordModel`, include it in the unique constraint and every repository query (drop the `"*"` bypass or make it require an explicit project-scoped capability), and thread the caller's authenticated project context through `routers/memory.py` and `_auto_record_episode` instead of trusting request-body fields.

---

## HIGH

### A-SELFUPDATE-GUARDRAIL-OVERWRITE
**Severity:** HIGH
**Files:**
- `src/general_ludd/self_update/applier.py:41-64` (`PROTECTED_PATH_MARKERS` — confirmed: no `AGENTS.md`, `CLAUDE.md`, or `TASKS.md` marker present)
- `src/general_ludd/self_update/applier.py:324-351` (YAML-kind branch: `yaml.safe_load(change_content)` then writes via `self._writer.write(...)`)

**Failure scenario:** A self-update proposal with `kind="config"`/`"yaml"`/`"role"` targeting `AGENTS.md` or `CLAUDE.md` passes the protected-path deny-list (neither file's marker is listed), and `yaml.safe_load` trivially accepts arbitrary prose as a valid YAML plain scalar — so the guardrail policy file gets silently overwritten by an automated update.

**Fix sketch:** Add `agents.md`, `claude.md`, and `tasks.md` (lowercased, matched as basenames) to `PROTECTED_PATH_MARKERS`; consider also rejecting YAML-kind changes whose parsed result is a bare scalar/string rather than a mapping, since that's the shape that lets prose slip through.

### A-CHRONIC-CAP-DEAD-FOR-REDISPATCH
**Severity:** HIGH
**Files:**
- `src/general_ludd/db/models.py:246` (`run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)` — lives in the cron-recurrence column block, models.py:227-248)
- `src/general_ludd/event_loop/lease.py:97-104` (`reclaim_expired_leases` resets `status` `ACTIVE`→`QUEUED` via a bare `update(TodoModel).values(status=..., updated_at=...)` — no `run_count` touch)
- `src/general_ludd/remediation/blocker_detector.py:246-298` (`_scan_chronic_requeues`, threshold at `self._config.max_requeues_before_chronic`, default 3 — reads `run_count` at line 276 as if it were a requeue counter)
- `src/general_ludd/event_loop/scheduler.py:196-207` (`TodoScheduler.tick()` — the **only** writer of `run_count` anywhere in the codebase, and it only fires for SCHEDULED cron-template rows: `"run_count": run_count + 1`)

**Failure scenario:** `run_count` is actually a cron-fire counter, not a retry/requeue counter — no dispatch, lease-reclaim, stuck-todo-reap, or floor/PID-cap release path increments it. `BlockerDetector._scan_chronic_requeues` reads it as a "times requeued" signal, so the chronic-detection cap can **never trip** for an ordinary (non-cron) todo that gets redispatched indefinitely (e.g. via repeated lease expiry) — it silently burns budget/GPU forever instead of surfacing as `resource_contention`.

**Fix sketch:** Increment `run_count` (or add a dedicated `requeue_count`) on every QUEUED-reassignment path — `lease.py:reclaim_expired_leases`, `_reap_stuck_todos`, and the PID/floor-cap release paths in `loop.py` — so `BlockerDetector`'s threshold check has real signal to act on.

### A-FILECLAIM-UNAUTHENTICATED-WORKERID
**Severity:** HIGH
**Files:**
- `src/general_ludd/routers/coordination.py:29-36` (`ClaimRequest`/`ReleaseRequest` — `worker_id` is caller-supplied free-form, `min_length=1, max_length=256`, no ownership check)
- `src/general_ludd/routers/coordination.py:98` (`api_release` calls `registry.release(req.worker_id)` for whatever `worker_id` the caller names)
- `src/general_ludd/execution/engine.py` (confirmed zero references to `file_claims`/`FileClaimRegistry`/`overlaps`/`should_wait` anywhere in the file — the write path in `_write_file()`/`_apply_unified_diff()` never consults the registry)
- `src/general_ludd/event_loop/loop.py:3355-3406` (`_try_commit_completed_work` — the *only* enforcement point; it claims/checks overlaps at **commit** time, i.e. after the write already landed, per its own docstring at 3360-3361)

**Failure scenario:** Any PSK holder can release or steal another worker's file claims by naming its `worker_id` (no ownership binding), and even absent that, two workers can concurrently overwrite the same file on disk because `execution/engine.py` never gates a write on the registry — the only check happens later, at git-commit time, which only defers the commit, not the write.

**Fix sketch:** Bind `worker_id` to an authenticated session/token rather than trusting the request body, and add a claim check in `execution/engine.py` before `_write_file()`/`_apply_unified_diff()` (not just at commit time in `loop.py`).

---

## MEDIUM

### A-UNREDACTED-MODEL-ERROR-PERSISTED
**Severity:** MEDIUM
**Files:**
- `src/general_ludd/event_loop/loop.py:2187` (`_model_call_error = str(_exc)`)
- `src/general_ludd/event_loop/loop.py:2227-2257` (`record_call(..., error_message=_model_call_error)`)
- `src/general_ludd/db/models.py:985` (`ModelCallLogModel.error_message: Mapped[str | None] = mapped_column(Text, nullable=True)` — unbounded)
- `src/general_ludd/secrets/manager.py:165` (`SecretsManager` class exists with a working redactor: `_sanitize_error`/`_redact_message`/`_redact` at lines 214-233) — confirmed unused in the loop.py capture path.

**Failure scenario:** A provider error (which can embed API keys, request bodies, or PII) is captured via bare `str(exc)` and persisted verbatim into an unbounded `Text` column, even though a redaction utility already exists elsewhere in the codebase and is simply not called here.

**Fix sketch:** Route `_model_call_error` through `SecretsManager`'s existing `_sanitize_error`/redact helper before it reaches `record_call(error_message=...)`.

### A-PROTECTED-PATH-DENYLIST-DRIFT
**Severity:** MEDIUM
**Files:**
- `src/general_ludd/self_update/applier.py:41-64` (`PROTECTED_PATH_MARKERS` — includes CI/build markers: `.github`, `/workflows/`, `pyproject.toml`, `makefile`, `alembic`, `tox.ini`, `.pre-commit`, `dockerfile`)
- `src/general_ludd/security/capability_lattice.py:41-75` (`PROTECTED_FILE_STEMS` 41-53, `PROTECTED_PATH_SUBSTRINGS` 58-64, `PROTECTED_PATH_SEGMENTS` 66-75 — no CI/build markers at all, only guardrail/policy/permission stems)
- `src/general_ludd/self_update/apply.py:51-65` (`_HARD_DENY_SUBSTRINGS` 51-56 + `_HARD_DENY_SEGMENTS` 58-65 — a third, narrower list: `.opencode`, `.claude`, `settings.json`, `settings.local.json` only)

**Failure scenario:** Three independent hardcoded protected-path lists disagree on coverage (e.g. only `applier.py` blocks `pyproject.toml`/`Makefile`/`Dockerfile`; only `capability_lattice.py` blocks `capability_lattice` itself by stem). Whichever guard sits on a given code path determines whether a target is actually protected — a future refactor that swaps which guard fires on a path can silently narrow protection.

**Fix sketch:** Consolidate into one shared deny-list module imported by all three call sites, or add a cross-check test asserting the three sets are equal (or a documented superset/subset relationship).

### A-CROSSTENANT-CLAIMRUNNABLE-FALLBACK
**Severity:** MEDIUM
**Files:**
- `src/general_ludd/event_loop/loop.py:1376-1380` (`_phase_claim_runnable_todos` — `if project_id is not None: claimed = await self._todo_repo.claim_runnable(project_id=project_id) else: claimed = await self._todo_repo.claim_runnable()`)
- `src/general_ludd/db/repository.py:424-451` (`claim_runnable` — `if _pid is not None: stmt = stmt.where(TodoModel.project_id == _pid)`, i.e. no filter at all when no project is resolved)

**Failure scenario:** When the EventLoop tick has no active project selected (`self._tick_project_id is None`), `claim_runnable()` is called with no `project_id`, and the repository applies no `project_id` filter — todos from every tenant become claimable in the same tick.

**Fix sketch:** Require an explicit project scope for `claim_runnable` in multi-tenant deployments, or make the no-project case an explicit opt-in cross-tenant mode rather than the silent default.

### A-ORNITH-EXPORT-ARBITRARY-WRITE
**Severity:** MEDIUM (feature is off by default — `ORNITH_ENABLED`)
**Files:**
- `src/general_ludd/routers/ornith.py:182-209` (`GET /admin/ornith/export` — `out_path: str | None` taken directly from the query string and passed through)
- `src/general_ludd/ornith/training_repo.py:199-226` (`export_dataset` — `out = Path(out_path); out.parent.mkdir(parents=True, exist_ok=True); with out.open("w", ...)`, no path confinement)
- `src/general_ludd/ornith/mcp_server.py:150-155` (`handle_tool_call` — `subprocess.run([self._binary_path, "--json", ...], capture_output=True, text=True, timeout=self._timeout_seconds)`, no rlimits/sandboxing)
- `src/general_ludd/abtest/_child.py:40,43-51` (contrast: `apply_limits(mem_limit_mb, cpu_seconds)` via `general_ludd.system.rlimit` — the hardened pattern this endpoint should follow)

**Failure scenario:** A caller of `/admin/ornith/export` can set `out_path` to any writable filesystem path (e.g. overwrite a config file or plant a file outside the intended export directory); separately, the ornith coding-agent subprocess runs with only a wall-clock timeout and no memory/CPU rlimits, unlike the hardened `abtest/_child.py` pattern already in the codebase.

**Fix sketch:** Confine `out_path` to a fixed export directory (reject absolute paths / `..` traversal), and wrap the ornith subprocess launch with the same `general_ludd.system.rlimit.apply_limits` used by `abtest/_child.py`.

### A-PROCESS-SIGNAL-NO-CAPABILITY-GATE
**Severity:** MEDIUM
**Files:**
- `src/general_ludd/routers/processes.py:1-12,162-200` (docstring explicitly states "this module adds NO auth of its own"; `POST /admin/processes/{pid}/signal` has no capability-gate check)
- `src/general_ludd/dispatch/dynamic_dispatcher.py:251` (contrast: `role_may_dispatch(str(self._role), kind)` gates dispatch, wired from `routers/dispatch.py:57-63`)
- `src/general_ludd/routers/self_improve.py:68-69,278` (contrast: `_ConfigTierCapabilityChecker().allows(...)` gates self-improve actions)

**Failure scenario:** Any PSK holder can send arbitrary OS signals (including SIGKILL to a process group) to any gludd-managed process via `/admin/processes/{pid}/signal` — the endpoint relies solely on the daemon's blanket PSK middleware, with no per-capability check, unlike its `dispatch`/`self_improve` sibling routers.

**Fix sketch:** Add the same capability-checker pattern used by `dispatch.py`/`self_improve.py` (a role-scoped `allows("process_signal")` check) before `signal_process` calls `reg.signal(...)`.

### A-BREAKGLASS-BACKUP-HYGIENE
**Severity:** MEDIUM
**Files (path corrected — role is nested under the collection, not `ansible/roles/`):**
- `collections/ansible_collections/general_ludd/agent/roles/openbao_break_glass_backup/tasks/main.yml:97-100` ("Shred the unencrypted snapshot" step is `ansible.builtin.file: state=absent` — a plain unlink, not a multi-pass overwrite)
- `collections/ansible_collections/general_ludd/agent/roles/openbao_break_glass_backup/defaults/main.yml:30` (`snapshot_temp_path: "/tmp/gludd-openbao-snapshot-{{ ansible_date_time.iso8601 }}.bin"` — plaintext snapshot written under world-writable `/tmp` with no explicit `mode:` set anywhere it's created, so it inherits the process umask rather than a locked-down mode)
- `collections/ansible_collections/general_ludd/agent/roles/openbao_break_glass_backup/tasks/main.yml:34-49` (GPG key generated via `gpg --batch --generate-key` with `%no-protection` at line 38 — passphraseless secret key)

**Failure scenario:** The break-glass backup role leaves a plaintext OpenBao raft snapshot in world-writable `/tmp` with no restrictive file mode, "shreds" it with a step that only unlinks the directory entry (recoverable via forensic tools), and encrypts the final artifact with a passphraseless GPG key — any of the three alone weakens the guarantee that this backup can't leak the unseal material.

**Fix sketch:** Set an explicit `mode: '0600'` on the temp snapshot task, replace the `file: state=absent` step with a real multi-pass overwrite (e.g. `shred -u` via `ansible.builtin.command`), and require an explicit passphrase (or a hardware-backed key) for the GPG key generation step.

### A-TODO-PRIORITY-UNBOUNDED
**Severity:** MEDIUM
**Files:**
- `src/general_ludd/schemas/todo.py:180-185` (`_priority_non_negative` — only checks `v < 0`, no upper bound)
- `src/general_ludd/routers/self_improve.py:76-81` (path corrected from the originally-cited top-level `self_improve.py`, which does not exist — it's a package, `src/general_ludd/self_improve/`, unrelated to this finding; `_coerce_priority` passes an `int` through unclamped: `if isinstance(raw, int): return raw`)

**Failure scenario:** A caller (or a self-improve proposal) can set an arbitrarily large `priority` value, letting a single todo permanently starve the FIFO-within-priority dispatch ordering (`ORDER BY priority DESC, created_at, id` in `db/repository.py:446-448`) for every other project's work.

**Fix sketch:** Clamp `priority` to a documented range (e.g. 0-100) in both the Pydantic validator and `_coerce_priority`.

### A-METRICS-TRACEID-DEAD-CODE
**Severity:** MEDIUM (currently dead code, but a latent trap for the next caller)
**Files:**
- `src/general_ludd/observability/metrics_exporter.py:173-185` (`_current_trace_id: dict[int, str] = {}`; `set_trace_id` keys by `threading.get_ident()`)

**Failure scenario:** If any future code path wires this into the async event-loop tracing path, `threading.get_ident()` is the wrong key for asyncio (a single OS thread runs many concurrent tasks), so trace IDs would cross-contaminate between concurrent coroutines. Currently unused, so no live impact — but it's a landmine for the next person who wires it up believing it's asyncio-safe.

**Fix sketch:** Either delete the dead code, or rekey by the current `asyncio` task (e.g. `contextvars.ContextVar`) before anything depends on it.

---

## LOW

### A-ANSIBLE-ORPHAN-FORK
**Severity:** LOW
**Reference:** Worker-app audit Finding 3 (hard worker kill can orphan a forked ansible child process; not independently re-verified this pass — carried forward from the prior audit as-is).
**Fix sketch:** Ensure the worker's process-group kill path (the same registry used by `routers/processes.py`) also reaps ansible's forked children, not just the direct child PID.

### A-STALE-CLAIM-TTL-RACE
**Severity:** LOW
**Files:** `src/general_ludd/coordination/file_claims.py:44-52` (`DEFAULT_TTL_SECONDS = 900.0`)
**Failure scenario:** A worker whose claim expires (15 min TTL) after a slow write but before commit could have its claim reaped and reused by another worker mid-write, compounding `A-FILECLAIM-UNAUTHENTICATED-WORKERID` above.
**Fix sketch:** Tie the claim heartbeat to actual write-liveness rather than a fixed wall-clock TTL, or shorten the TTL and require active heartbeating during long writes.

### A-VERSIONED-DENYLIST-NO-SHARED-TEST
**Severity:** LOW
**Files:** same three files as `A-PROTECTED-PATH-DENYLIST-DRIFT`.
**Fix sketch:** (Folded from the MEDIUM item above as a process gap.) Add a unit test asserting all three deny-lists agree, so future edits to one don't silently diverge from the others again.

---

## Notes on citation corrections made during this sweep

- `db/repository.py` list_by_namespace citation tightened to `2822-2841` (full method) with the wildcard bypass pinned to `2837-2838`.
- `self_improve.py:76-81` corrected to `routers/self_improve.py:76-81` — there is no top-level `src/general_ludd/self_improve.py`; that path resolves to the `self_improve/` package, which is unrelated to the priority-coercion finding.
- `ansible/roles/openbao_break_glass_backup/...` corrected to `collections/ansible_collections/general_ludd/agent/roles/openbao_break_glass_backup/...` — the role does not exist at the originally-assumed top-level `ansible/roles/` path.
- The file-claim-registry finding was refined: it is **not** purely advisory system-wide. `event_loop/loop.py:3355-3406` (`_try_commit_completed_work`) does enforce it, but only at git-commit time — after the conflicting write has already landed on disk. The exposed gap is narrower than originally stated: write-time (not just commit-time) enforcement is missing, and `worker_id` is unauthenticated.
- The chronic-requeue-cap finding was broadened during verification: `run_count` is not merely "not incremented on the lease-reclaim path" — it is a cron-recurrence counter (`event_loop/scheduler.py:196-207` is its only writer, gated to SCHEDULED todos) that no ordinary dispatch/retry/redispatch path touches at all, so `BlockerDetector._scan_chronic_requeues` is effectively dead for the non-cron todos it was written to protect.

---

## Authorization & Async-Robustness cluster (2026-07-10 sweep, appended)

Every citation below was re-read against the current tree before being recorded.
This session had no direct grep/glob search tool available, so verification was
done by opening each cited file/line range with Read — every file:line pair
below was actually opened and its content confirmed to match the claim, except
where a drift/correction note says otherwise. Two items already in this ledger
cover part of the same surface and are cross-referenced rather than
re-added: `A-PROCESS-SIGNAL-NO-CAPABILITY-GATE` (MEDIUM, above) is the
`routers/processes.py` instance of the same "PSK-only, no capability gate"
pattern broadened by `A-EXEC-ROUTES-PSK-ONLY` below; `A-FILECLAIM-UNAUTHENTICATED-WORKERID`
(HIGH, above) already covers `routers/coordination.py` claim/release — not
duplicated here.

Total appended this section: **11** findings — 1 CRITICAL, 5 HIGH, 4 MEDIUM, 1 LOW.

### AUTHZ cluster

All items in this cluster sit behind the single shared PSK middleware at
`src/general_ludd/daemon.py:2436-2490` (`_PUBLIC_PATHS` / `_is_public` /
`auth_and_stats_middleware`, confirmed verbatim) with **no second
authorization layer** — a valid PSK is binary in/out for the entire `/admin`
surface; nothing downstream checks *which* agent/human holds it or what it is
allowed to do, except where a route independently added its own
capability-checker (the `dispatch.py` / `self_improve.py` pattern below).

#### A-ESCALATION-SELF-APPROVE
**Severity:** CRITICAL
**Files:**
- `src/general_ludd/routers/security.py:577-637` (`POST /admin/perm/escalations/{esc_id}/approve` — confirmed: the only gate is `row["status"] != "pending"` at line 582; `human_reviewer` is read straight from the caller's request body at line 620 `row["human_reviewer"] = req.get("human_reviewer")` and passed to `_resolve_human_todo_for_escalation` as `resolver=...` — never checked against any real approver identity or role)
- `src/general_ludd/routers/security.py:639-660+` (`POST /admin/perm/escalations/{esc_id}/deny` — same free-text `human_reviewer` pattern at line 656)
- `src/general_ludd/routers/integrity.py:120-199` (`POST /admin/integrity/approve` — `signer` is `req.get("signer", "admin")` at line 174, persisted into the signed audit log verbatim, never validated against a real signer identity)
- `src/general_ludd/routers/human_todos.py:199-237` (`PATCH /api/human-todos/{id}` — `req.human_resolver` required to be non-empty (lines 221/230) but is any caller-supplied string, passed straight to `repo.mark_done`/`repo.dismiss`)

**Failure scenario:** Any PSK holder can call `POST /admin/perm/escalations/{id}/approve` with a self-chosen `human_reviewer` string and no other identity check, which both marks the escalation approved AND (via `issuer.issue(...)` at security.py:606-612) mints a real STS token carrying the augmented capability set. The same self-declared-approver shape recurs at `integrity.py:120` (`signer`) for signing an integrity-approval and at `human_todos.py:199` (`human_resolver`) for resolving a human-in-the-loop todo. In all three cases the "human" in "human-in-the-loop" is an unauthenticated free-text field the same caller who requested the escalation/change can fill in themselves — the approval gate is entirely defeated.
**Fix sketch:** Require the approving/signing/resolving identity to come from an authenticated principal (a second, distinct credential from the requesting agent's PSK — e.g. a human-operator token, SSO session, or out-of-band approval channel), not a request-body string. At minimum, reject an approval where `human_reviewer`/`signer`/`human_resolver` equals the identity that filed the original request.

#### A-PERMSPEC-SELF-EDIT
**Severity:** HIGH
**Files:** `src/general_ludd/routers/security.py:406-421` (`PUT /admin/perm/spec/{agent_type}` — confirmed: `PermissionSpecParser.parse`/`.validate` are schema-only checks (lines 412-417), then `path.write_text(spec_yaml)` at line 420 overwrites the on-disk spec unconditionally)
**Failure scenario:** Any PSK holder can overwrite any `agent_type`'s permission-spec YAML (including its own) with an arbitrary schema-valid spec — e.g. granting itself every capability — since nothing here checks that the caller is authorized to modify *that* `agent_type`'s permissions.
**Fix sketch:** Require a capability check (e.g. only a higher-tier role, or the human-approval flow above once actually fixed) before accepting a spec write for any `agent_type` other than a narrowly-scoped self-demotion.

#### A-STS-REVOKE-ANY
**Severity:** HIGH
**Files:** `src/general_ludd/routers/security.py:344-355` (`POST /admin/sts/revoke` — confirmed: only checks `token_id` is non-empty and that `issuer.revoke(token_id)` finds a live token; no check that the caller issued or owns that token)
**Failure scenario:** Any PSK holder can revoke any other agent's active STS token by `token_id` (enumerable/guessable or discoverable via `GET /admin/sts/audit`), disrupting an unrelated agent's in-flight privileged work.
**Fix sketch:** Bind revoke to the issuing/subject identity, or require the same capability tier that issued the token.

#### A-DEPLOY-DESTROY-ANY
**Severity:** HIGH
**Files:**
- `src/general_ludd/routers/compute.py:315-331` (`DELETE /admin/compute/destroy/{instance_id}` — confirmed: only checks `mgr.get_deployment(instance_id) is None` (404 if unknown); no owner/caller field on the deployment record is consulted)
- `src/general_ludd/routers/compute.py:139-313` (`POST /admin/compute/deploy` — confirmed real `await mgr.deploy(config)` at line 242 which runs actual Terraform; `force` bypass of the critical-misconfig precheck confirmed at line 180: `if critical and not req.get("force")`)
**Failure scenario:** Any PSK holder who learns/enumerates an `instance_id` can tear down another caller's real cloud deployment (`mgr.destroy` performs a genuine teardown) — `app.state._compute_deployments` has no per-caller ownership field, only the instance record itself. Separately, any caller can pass `force=true` on deploy to bypass a critical misconfiguration finding that would otherwise refuse the spend.
**Fix sketch:** Add an owner/project field to the deployment record populated from an authenticated caller identity, and require it match on destroy; gate `force=true` behind a higher capability tier rather than accepting it from the same unauthenticated-beyond-PSK caller.

#### A-ACCOUNT-ANY
**Severity:** HIGH
**Files:**
- `src/general_ludd/routers/account.py:83-96` (`POST /api/account/backup`) and `:98-120` (`DELETE /api/account`) — both take `user_id` in the request body (`BackupRequest`/`DeleteRequest`, lines 55-61) with no check that the caller is authorized to act on that `user_id`; delete additionally requires `confirm=true` (a formality, not an identity check)
- `src/general_ludd/routers/account.py:141-188` (`POST /api/account/create`) — **correction to the original claim**: this endpoint's `CreateAccountRequest` (lines 64-74) has **no `user_id` field at all** (only `provider`/`budget`/`ephemeral`); the real issue here is different from backup/delete — any PSK holder can provision a real, budget-spending ephemeral cloud account (`mgr.create_account(...)` at line 174, returning live `access_key_id`) with no attribution to a specific authorized caller/project, only a `budget` ceiling.
**Failure scenario:** Any PSK holder can export or permanently delete an arbitrary user's data by naming their `user_id` (backup/delete), and separately, any PSK holder can spin up real cloud spend via the ephemeral-account endpoint with no per-caller quota or ownership tracking beyond the single ephemeral policy's global cap.
**Fix sketch:** Bind `user_id` on backup/delete to the authenticated caller (or require an explicit elevated capability for cross-user access); add a caller/project attribution field to ephemeral account creation so spend can be traced and capped per caller, not just globally.

#### A-EXEC-ROUTES-PSK-ONLY
**Severity:** MEDIUM
**Files (each confirmed PSK-only with no capability-gate call in the handler):**
- `src/general_ludd/routers/slurm.py:45-61` (`POST /admin/slurm/submit` — `command` taken directly from the request body and passed to `adapter.submit`)
- `src/general_ludd/routers/ansible.py:21-29` (`POST /admin/ansible/install` — `install_galaxy` shells out to `ansible-galaxy install` for a caller-named role)
- `src/general_ludd/routers/stream.py:79-138,185-199` (`POST /admin/stream/dispatch` — clones a role then, when `wait_for_completion`, runs `["ansible-playbook", "run-clone.yml"]` synchronously per `_run_clone_sync`, confirmed at lines 190-199)
- `src/general_ludd/routers/models.py:311-330` (`POST /admin/local-inference/start` — spawns a local inference process via `LocalInferenceManager`)
- `src/general_ludd/routers/filestore.py:46-78` (`POST /admin/filestore/write` and `DELETE /admin/filestore/remove` — `sanitize_path` confirmed traversal-only, no capability check)
- `src/general_ludd/routers/skills.py:91-101,103-127,129-144` (install / fetch-by-URL / fetch-from-GitHub — fetched skill content is later executed as agent-facing skill instructions)
- `src/general_ludd/routers/reload.py:214-224` (`POST /admin/hooks` — registers an arbitrary outbound webhook URL)
- `src/general_ludd/routers/spend.py:76-90+` (`POST /api/spend/configure` — reconfigures the spend cap; confirmed it deliberately preserves prior spend history so reconfiguring can't reset the window, but nothing gates *who* may reconfigure the cap at all)

Contrast (the fix pattern already exists elsewhere in the codebase):
- `src/general_ludd/routers/dispatch.py:34-63` (`register(...)` takes a `role` parameter that gates every dispatch through `DynamicDispatcher`'s capability lattice — "DENY-BY-DEFAULT for the privileged kinds", confirmed in the docstring at lines 50-54)
- `src/general_ludd/routers/self_improve.py:58-69` (`_ConfigTierCapabilityChecker.allows(...)`, confirmed: a fail-closed `frozenset` capability check)

**Failure scenario:** This tier of routes performs real, state-changing or arbitrary-exec actions (sbatch submission, ansible-galaxy role install, ansible-playbook execution, process spawn, filesystem write/delete, remote-content fetch treated as executable skill instructions, webhook registration, spend-cap reconfiguration) gated by nothing but "holds a valid PSK" — no role/capability distinction the way `dispatch.py`/`self_improve.py` already established for their own routes.
**Fix sketch:** Extend the `dispatch.py`/`self_improve.py` capability-checker pattern to these routes (e.g. `slurm_submit`, `ansible_install`, `stream_dispatch`, `local_inference_spawn`, `filestore_write`, `skill_install`, `hook_register`, `spend_configure` capabilities), so a PSK alone is necessary but not sufficient.

### ASYNC-ROBUSTNESS cluster

#### A-WORKER-PERFWRITE-DEADLOOP
**Severity:** HIGH (latent — the worker path has no `gludd-worker` entrypoint wired in prod today, so this is dormant until that path is activated)
**Files:**
- `src/general_ludd/worker/app.py:388-399` (`_model_perf_repo.record_call_sync(...)` call site, confirmed verbatim including the `service`/`model_name`/`todo_id`/`job_id` kwargs)
- `src/general_ludd/db/repository.py:2352-2397` (`record_call_sync` — confirmed: builds `coro = self.record_call(...)` then `_asyncio.run(coro)` at line 2397, from inside a synchronous method invoked from the worker's already-running FastAPI event loop)
- `src/general_ludd/db/repository.py:2398-2405` (the `except Exception as exc: ... logger.warning(...)` that swallows the resulting `RuntimeError` — confirmed the docstring at 2370-2376 explicitly says "Intended for use from `asyncio.to_thread` worker paths" but the actual call site at `worker/app.py:388` is a direct synchronous call, not behind `asyncio.to_thread`)
- `tests/unit/test_worker.py:272,319` (both `test_records_...` tests set `app.state.model_perf_repo = AsyncMock()`, confirmed verbatim — this makes `record_call_sync` an async mock, so the test never exercises the real synchronous `asyncio.run(coro)` path and the bug is invisible to the suite)
- `src/general_ludd/event_loop/loop.py:2243` (contrast: `await self._model_perf_repo.record_call(...)` — the daemon in-process path correctly awaits the coroutine directly and is unaffected)

**Failure scenario:** Every worker-path model-performance write calls a "synchronous" wrapper that internally does `asyncio.run(coro)` while already inside a running event loop (the worker's FastAPI app) — this always raises `RuntimeError: asyncio.run() cannot be called from a running event loop`, caught and merely logged at WARNING by `record_call_sync`'s own except block. Every worker-path model-performance record is silently lost, 100% of the time, and the test suite can't catch it because it mocks the method as async.
**Fix sketch:** Either `await self._model_perf_repo.record_call(...)` directly (the loop.py:2243 pattern) if `worker/app.py`'s handler is itself async, or wrap the sync call in `asyncio.to_thread(...)` as the docstring already assumes it's called from; alternatively, have `record_call_sync` detect a running loop (`asyncio.get_running_loop()` succeeding) and dispatch via `asyncio.ensure_future`/a thread instead of calling `asyncio.run`.

#### A-BENCHMARK-SILENT-SUPPRESS
**Severity:** LOW
**Files:**
- `src/general_ludd/event_loop/benchmark.py:12-41` (`record_job_benchmark` — confirmed: `with contextlib.suppress(Exception): await recorder._repo.record_result(...)` at lines 24-25, with a `logger.info` only on the success path at line 41 — no `except`/failure-path logging at all)
- `src/general_ludd/event_loop/loop.py:2548-2558,2605-2610` (both fire-and-forget call sites: `asyncio.create_task(record_job_benchmark(...))`/`asyncio.create_task(self._benchmark_recorder.record_from_trace(...))`, both wrapped in `self._track_background_task(...)`; the only surrounding `try/except` (loop.py:2545-2566) catches a *scheduling*-time exception from calling `asyncio.create_task` itself, not a failure inside the task's own execution)
- `src/general_ludd/execution/engine.py:567-579,740-752` (same `record_job_benchmark` fire-and-forget pattern, confirmed at both call sites)
- `src/general_ludd/observability/recorder.py:102-112` (contrast, confirmed: the sibling `record_from_trace`-adjacent write path uses a real `try: await self._repo.record_result(data) ... except Exception as exc: logger.warning(...)` — i.e. it DOES log on failure, unlike `benchmark.py`'s bare `contextlib.suppress`)

**Failure scenario:** A benchmark-recording DB write failure inside `record_job_benchmark` is swallowed with zero trace (`contextlib.suppress(Exception)` plus a success-only log line), and the enclosing fire-and-forget `asyncio.create_task` is never inspected for `task.exception()` after completion — the only exception handling anywhere on this path is for the synchronous act of scheduling the task, not its eventual result. A chronic benchmark-write failure is completely invisible to operators, unlike the working sibling pattern at `observability/recorder.py:102-112`.
**Fix sketch:** Replace `contextlib.suppress(Exception)` with a real `try/except Exception as exc: logger.warning(...)` mirroring `recorder.py:102-112`, and/or have the tracked background-task registry inspect `task.exception()` on completion and log any non-`None` result.

### Follow-up note (design doc, not a fresh finding)

**A-OSQUERY-DAEMON-NOT-BOOTSTRAPPED** (MEDIUM, implementation TODO — already identified in design, recorded here for the ledger's completeness)
**Files:**
- `src/general_ludd/filestore/bootstrap.py:353-356` (`_TARBALL_BINARIES: ClassVar[dict[str, str]] = {"osquery": "osqueryi", "codebase-memory-mcp": "codebase-memory-mcp"}` — confirmed verbatim: only `osqueryi` is ever extracted from the downloaded osquery tarball; `osqueryd` — needed for scheduled query packs — is never requested even though `_extract_executable_member` (lines 300-330) is arch/path-agnostic and could pull it out the same way)
- `src/general_ludd/filestore/bootstrap.py:270-271` (`if osq_arch != "x86_64": return None` inside `_osquery_download_url` — confirmed verbatim; drops linux/arm64 from the buildable URL set)
- `docs/design/OSQUERY_MONITORING_ROLES.md:95-123` (§1.4 "Binary bundling status" — confirmed this exact gap is already documented as "the load-bearing gap": `osqueryi` only, `osqueryd` never bootstrapped, blocking §4's query-pack roles; it also explicitly labels the linux/arm64 branch a "known bug already flagged in `docs/design/BINARY_BUNDLING.md:207-208`, still present" — i.e. **osquery does publish a linux/arm64 tarball for 5.10.2** per that doc's own research, so line 270-271 is a real URL-builder bug, not (as the in-code comment at bootstrap.py:269 implies) a reflection of a real upstream gap. This is a design-doc-vs-code-comment disagreement worth flagging directly: `bootstrap.py:269`'s comment ("osquery 5.10.2 publishes only an x86_64 linux tarball") is asserted to be wrong by `OSQUERY_MONITORING_ROLES.md`/`BINARY_BUNDLING.md`.)

**Status:** Design-complete, not yet implemented (`OSQUERY_MONITORING_ROLES.md` §3.1 `osquery_bootstrap` role is the planned fix). Recording here as a cross-reference so this ledger and the design doc don't drift out of sync — no new remediation owed beyond what that design already specifies.

### Additional findings (verified this pass, reported mid-sweep)

#### A-COMPLETION-GATE-UNWIRED
**Severity:** MEDIUM-HIGH
**Files:**
- `src/general_ludd/review/decision_applier.py:35-37` (`if decision.decision == "complete": ... decision = await asyncio.to_thread(verify_completion, decision, None, repo_root)`, confirmed verbatim) and `:55-65` (`await todo_repo.transition(decision.matched_todo_id, target_status, ...)` — the actual status-flip to `TodoStatus.COMPLETE` via `_DECISION_STATUS_MAP`, confirmed verbatim)
- `src/general_ludd/review/completion_verifier.py:120-184` (`verify_completion` — confirmed exact function boundaries; downgrades to `needs_more_work` when any `evidence_ref` is unmet, never raises)
- `src/general_ludd/schemas/task_decision.py:15` (`evidence_refs: list[str] = Field(default_factory=list)` — confirmed verbatim, and it is entirely reviewer-LLM-chosen with no independent selection)
- `src/general_ludd/quality/feature_verifier.py:189-284` (`_check_ref` dispatch, confirmed exact span) — `test:<node>` (`_check_test`, lines 204-207) genuinely runs `uv run pytest <node> -q --no-header --tb=no` via a real subprocess (`_default_runner`, confirmed at lines 63-83) but the node id can be **any pre-existing unrelated green test**, not one that covers the change; `file:<path>::<symbol>` (`_check_file_symbol`, confirmed exact span **263-283**) is a naive `if symbol in text` substring search — gameable by any comment/string containing the symbol name; `artifact:<path>` (delegated in `completion_verifier.py:103-108`) is mere file-existence.
- `src/general_ludd/quality/preflight.py:360-385` (`run_preflight` — confirmed exact span: runs ruff/mypy/coverage/molecule/etc. checks and returns an aggregate PASS/FAIL)
- `src/general_ludd/quality/gate.py:78-88` (`QualityGateChecker.enforce` — confirmed exact span: `blocks_completion`/`blocks_commit`/`blocks_merge`/`blocks_push`/`blocks_reload` computed from `self.config.enforcement.*` fields and `all_passed`)
- `src/general_ludd/quality/project_gate.py:35-208` (`run_project_gate` — confirmed exact span: aggregates per-check `ProjectCommandRunner` results into a fail-closed pass/fail verdict for an *external* target project)
- `src/general_ludd/daemon.py:1982-1993` (**citation corrected from the original `1962-1973`** — the actual `run_preflight` fire-and-forget call site: `app.state._preflight_task = asyncio.create_task(_init_preflight())` where `_init_preflight` does `daemon_state["quality_gate"] = result` purely for `/api/status` display; confirmed this is the ONLY production call site of `run_preflight` found during this sweep)
- `src/general_ludd/schemas/quality_gate.py:62` (`block_todo_complete: bool = True` inside `EnforcementGate`, confirmed verbatim)

**Note on verification scope:** no grep/glob tool was available this session, so "ZERO production call sites" for `QualityGateChecker.enforce`/`run_project_gate` could not be exhaustively confirmed by exclusion search — it is based on: (a) `decision_applier.py`'s completion path (the only production COMPLETE-transition path found) calls only `completion_verifier.verify_completion`, never `enforce`/`run_project_gate`; (b) the only `run_preflight` production wiring found is the informational startup task above, which also never calls `enforce`/`run_project_gate`. Treat "zero call sites" as strongly corroborated but not search-exhaustive.

**Failure scenario:** The reviewer LLM's own `decision="complete"` plus a handful of caller-chosen `evidence_refs` is the entire gate standing between a change and `TodoStatus.COMPLETE` — real static/type/coverage quality gates (`preflight.py`, `gate.py`, `project_gate.py`) exist, are well-built, and are fail-closed *internally*, but nothing on the completion path invokes them; `schemas/quality_gate.py:62`'s `block_todo_complete=True` is dead configuration that nothing reads before flipping a todo to COMPLETE. A reviewer can satisfy `verify_completion` by citing an unrelated pre-existing passing test and a symbol that merely appears in a comment, with zero ruff/mypy/full-pytest run against the actual diff.
**Fix sketch:** Call `run_project_gate`/`QualityGateChecker.enforce` (or gludd's own `run_preflight` for self-hosting) from `decision_applier.apply_decision`'s `"complete"` branch, honoring `blocks_completion`/`block_todo_complete` as a hard gate before `todo_repo.transition(..., TodoStatus.COMPLETE, ...)` is reached — not merely running it at daemon startup for dashboard display.

(Note: G5 `ReturnReviewer` fence/JSON parse is CONFIRMED FIXED and fail-closed per prior session findings — not re-flagged here.)

#### A-MERGE-FAILOPEN
**Severity:** MEDIUM (latent — both methods below have zero production callers found this pass)
**Files:**
- `src/general_ludd/git_automation/repo.py:583-634` (`merge_branch` — confirmed: on `result.returncode != 0` at line 602, returns `MergeResult(success=False, ...)` at line 606 with **no** `merge --abort`/`reset --hard` call anywhere in the method — a failed/conflicted merge leaves conflict markers and `MERGE_HEAD` in the working tree)
- `src/general_ludd/git_automation/repo.py:691-758` (`gated_merge` — confirmed contrast: on merge failure at lines 721-729, it DOES run `self._run_git("merge", "--abort", check=False)` then `self._run_git("reset", "--hard", pre_sha, check=False)` before returning failure)
- `src/general_ludd/git_automation/repo.py:731` (`gated_merge`'s squash-commit step — confirmed: `self._run_git("commit", "-m", ..., check=False)`, unchecked) vs. `src/general_ludd/git_automation/repo.py:615-626` (`merge_branch`'s squash-commit step — confirmed: `check=True` wrapped in `try/except subprocess.CalledProcessError`, fail-closed)

**Failure scenario:** If `merge_branch` is ever wired to a caller that follows a failed merge with a generic `commit()` (stage-all + commit), that caller would commit `<<<<<<<`/`=======`/`>>>>>>>` conflict markers as if they were resolved code — `merge_branch` leaves the tree in exactly that dangerous half-merged state on failure, unlike its sibling `gated_merge` which cleans up. Both methods are currently unreferenced by any production call site found this pass, so this is a landmine for whichever future caller wires `merge_branch` up, not a live exploit today.
**Fix sketch:** Make `merge_branch` mirror `gated_merge`'s cleanup: on non-zero merge return, run `merge --abort` (harmless no-op if not mid-merge) then `reset --hard` to the pre-merge SHA before returning `success=False`. Also make `gated_merge`'s squash-commit step (`repo.py:731`) `check=True`/exception-checked like `merge_branch`'s equivalent step, so a failed squash-commit can't silently report `success=True`.

**LOW addendum — ref-validation coverage gap:**
- `src/general_ludd/git_automation/repo.py:40-54` (`_reject_leading_dash` — confirmed: rejects ONLY a leading `-`; no `..`, `.lock`, or control-character check)
- `src/general_ludd/worktree/core.py:32-61` (`validate_branch_name` — confirmed stronger: also rejects `..` sequences, leading `/`, `.lock` suffix, and a broad forbidden-metacharacter regex — the pattern `_reject_leading_dash` should be upgraded to match)
- `tag_release`/`create_checkpoint_tag`/`create_local_bare_mirror` in `git_automation/repo.py` were reported (by the requesting message) to have no ref validation at all, backstopped only by git's own `check-ref-format`; this specific sub-claim was **not independently re-confirmed** this pass (no search tool to enumerate every ref-taking method) and both `merge_branch`/`gated_merge` above are themselves dead-code paths today, so treat this addendum as carried-forward rather than freshly verified.

**Fix sketch:** Route all ref-taking methods in `git_automation/repo.py` through `worktree/core.py`'s `validate_branch_name` (or an equivalent shared validator) instead of the narrower `_reject_leading_dash`, and add the missing validation to the three untested methods once confirmed.

---

## Sandbox-isolation addendum (2026-07-10 sweep, appended)

Every citation below was re-opened with Read against the current tree and confirmed
verbatim (line numbers matched exactly; no drift found this pass).

### A-TOOLEXEC-UNSANDBOXED
**Severity:** HIGH
**Files:**
- `src/general_ludd/sandbox_exec/executor.py:13-19` (`SandboxExecutor.execute` — confirmed verbatim: a bare `subprocess.run(shlex.split(command), cwd=workdir, capture_output=True, text=True, timeout=self.timeout)`; only a wall-clock timeout — no rlimits, chroot/tmp confinement, seccomp, network isolation, or priv-drop) — `max_output_bytes` (line 10, constructor param) is stored on `self` but never read anywhere in `execute()`, so it is dead configuration.
- `src/general_ludd/security/sandboxes/__init__.py:17-21` (module docstring: "Every backend FAILS OPEN: if loading the sandbox raises ... returns a `SandboxHandle` whose `applied` flag is `False`") confirmed as the documented contract; the concrete fail-open mechanics live in the `SandboxHandle.applied` field (line 72, default `True`, flipped by each backend) and the `SandboxBackend` Protocol docstrings (lines 152-154, 164-170) which mandate "if loading the sandbox raises, log loudly and return a handle whose `applied` is `False` rather than propagating."
- `src/general_ludd/event_loop/loop.py:1696-1701` (`_sandbox_apply_for_todo`'s `except Exception as exc: logger.error("Sandbox apply raised for todo %s — dispatching UNSANDBOXED: %s", ...); return None` — confirmed verbatim, catches ALL apply/verify exceptions)
- `src/general_ludd/event_loop/loop.py:1717-1785` (`_resolve_permission_spec` — confirmed exact span; `if spec_cfg is None: return None` at lines 1752-1753 is the default-deny-to-unsandboxed path when no per-queue `permission_spec` is configured)
- `src/general_ludd/security/sandboxes/detect.py:180-209` (confirmed verbatim: macOS branch refuses Seatbelt on `>= 15.4` via `_is_deprecated_host()` at line 187, logging "sandbox-exec present but DEPRECATED on macOS %s (>= 15.4); dispatching UNSANDBOXED" and returning `None`; the sibling branch at lines 201-209 also returns `None` with a warning when `sandbox-exec` isn't present at all)
- `src/general_ludd/event_loop/loop.py:1623-1627` (confirmed verbatim: the only production call site of `SandboxExecutor.execute()` passes `f"dispatch:{todo_id}:{work_type}"` as `command` — a synthetic label string, not a real shell command — so this call isolates nothing regardless of the executor's own weaknesses above)
- `src/general_ludd/execution/langgraph_agent.py:192-235` (`_build_langchain_tools`/`_execute` — confirmed verbatim: wraps `mcp_client.call_tool(_srv_id, _name, kwargs)` in an `asyncio.wait_for` timeout and a bare `except Exception` string-return; no sandbox/capability check anywhere in the span — this is the live agent tool-call path and it bypasses both `SandboxExecutor` and the OS-sandbox machinery above entirely)
- `src/general_ludd/project_runner/runner.py:239` (confirmed verbatim, contrast: `subprocess.Popen(argv, ..., preexec_fn=preexec, ...)` with the inline comment "argv is validated + allow-listed, no shell" plus an `RLIMIT_AS` cap applied via `preexec_fn` when `self._memory_limit_mb` is set — this is the one path in the codebase that actually isolates a child process)

**Failure scenario:** Model-driven tool execution runs with full daemon privileges out of the box. The dedicated `SandboxExecutor` enforces only a timeout (no rlimits/chroot/seccomp/network isolation/priv-drop), and its one production call site doesn't even pass it a real command. The real OS-sandbox layer (`security/sandboxes/*`) is fail-open by design — any apply/verify exception, a missing per-queue `permission_spec`, or a deprecated/absent macOS Seatbelt binary all silently fall through to dispatching the agent UNSANDBOXED. Most importantly, the actually-live agent tool-call path (`langgraph_agent.py` → `mcp_client.call_tool`) never touches either sandboxing mechanism at all. Net effect: a model's tool calls execute as plain, unconfined subprocesses with the daemon's own privileges, with `project_runner/runner.py` being the sole counter-example of what real isolation looks like in this codebase.
**Fix sketch:** Engage the OS sandbox by default and fail closed (refuse to dispatch, rather than warn-and-proceed) for the MCP/tool-exec path when no backend is available or apply/verify fails; alternatively, route model tool calls through `project_runner`'s `apply_limits` + argv-allowlist pattern (`runner.py:239`) instead of `mcp_client.call_tool`'s direct, unconfined invocation.

**POSITIVE (verified-clean, not a finding):** No shell-string injection surface found — subprocess call sites checked across `execution/engine.py:915` (LLM-authored diff applied via `patch -p1 -d <jail> -i <diff_path>`, list-form argv, diff content only ever reaches `patch` as file content via `-i`, never interpolated into a shell string), `project_runner/runner.py:239`, and the LOW list below: all list-form argv, no `shell=True`/`os.system`/`os.popen`/bare `eval`/`exec` found in any file opened this pass. Bearer-token auth is constant-time: `src/general_ludd/security/auth.py:102-111` (`verify_psk` — `hmac.compare_digest(presented, expected)`, confirmed verbatim, guards against empty presented/expected). Prior session's C22-SSTI fix holds: `src/general_ludd/prompts/registry.py:39` and `:120` both confirmed verbatim using `SandboxedEnvironment(loader=self._loader, autoescape=select_autoescape())` (Jinja2 sandboxed environment, not the bare `Environment`).

### A-SUBPROCESS-NO-TIMEOUT
**Severity:** LOW
**Files (confirmed — none of these calls pass a `timeout=`, and none is wrapped in `asyncio.wait_for`):**
- `src/general_ludd/dogfood/runner.py:127` (`subprocess.run(["ansible-playbook", "--syntax-check", ...], capture_output=True, text=True, cwd=self.config.repo_root)` — no `timeout`)
- `src/general_ludd/validation/runner.py:161` (`subprocess.run(argv, cwd=self.worktree_path, capture_output=True, text=True, shell=False)` — no `timeout`; comment notes argv is validated/allowlisted but that guards injection, not hang risk)
- `src/general_ludd/account/ephemeral.py:250` (`subprocess.run(cmd, capture_output=True, text=True, check=False)` — no `timeout`)
- `src/general_ludd/collections/importer.py:391` (`subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)` — no `timeout`)
- `src/general_ludd/secrets/manager.py:623` (`asyncio.create_subprocess_exec(*args, stdout=..., stderr=...)` followed by a bare `await proc.communicate()` at line 628 — no `asyncio.wait_for` wrapping, so a hung OpenBao dev-server container start blocks indefinitely)
- `src/general_ludd/infra/deployment.py:184` (`asyncio.create_subprocess_exec(binary, *args, ...)` followed by `await proc.communicate()` — same pattern, a hung Terraform invocation blocks indefinitely)
- `src/general_ludd/cli_remediation.py:194` (`subprocess.run([editor, str(cfg_path)], check=True)` — no `timeout`; lower real-world risk since this launches an interactive `$EDITOR` session where a human is expected to be present, but it still matches the audited pattern)

**Failure scenario:** A hung child process (ansible-playbook, a CLI tool, an OpenBao/Terraform binary, or an editor) on any of these paths blocks the calling coroutine/thread indefinitely — no local backstop exists independent of the child process itself ever exiting.
**Fix sketch:** Add an explicit `timeout=` to each `subprocess.run` call (with a sensible per-call default) and wrap the two `asyncio.create_subprocess_exec` + `communicate()` pairs (`secrets/manager.py:623`, `infra/deployment.py:184`) in `asyncio.wait_for(...)`, killing the process group on timeout.

---

## Late-sweep additions (2026-07-10, appended)

Every citation below was re-opened with Read (no grep/glob tool available this
pass either) and confirmed against the current tree, with corrections noted
inline where a claim overstated or under-cited what the code actually does.

Total appended this section: **6** findings — 2 HIGH, 2 MEDIUM, 2 LOW.

### A-VARNS-TOOLRESULT-LEAK
**Severity:** HIGH
**Files:**
- `src/general_ludd/event_loop/loop.py:2304-2308` (Phase-1 dispatch results: `await eff_variable_repo.set_var(namespace="tool_results", key=f"tool_result:{r.name}", value=str(r.output))` — confirmed verbatim, no `project_id` kwarg)
- `src/general_ludd/event_loop/loop.py:2523-2527` (Phase-2 tool-loop output: `await eff_variable_repo.set_var(namespace="tool_results", key=f"tool_loop_result:{job_id}", value=str(tool_result))` — confirmed verbatim, no `project_id` kwarg)
- `src/general_ludd/db/repository.py:832-852` (`VariableNamespaceRepository.load_vars_for_project` — confirmed: the `.where((VariableNamespaceModel.project_id == project_id) | (VariableNamespaceModel.project_id.is_(None)))` union is at lines 836-839)
- `src/general_ludd/event_loop/loop.py:1844` (both write sites and both `project_id_val` read sites live in the same enclosing method, `async def _dispatch_execute_job`; no nested `def`/`async def` boundary separates them — confirmed by reading the full 1900-2530 span)
- `src/general_ludd/event_loop/loop.py:1907` (`project_id_val` is defined here and remains a valid in-scope local at both `set_var` call sites; it is actively used elsewhere in the same function at line 2069 `"project_id": project_id_val`, line 2168 `project_id=project_id_val` into `invoke_model_for_generation`, and line 2497 `project_id=project_id_val` into `JobSpec(...)` — it is simply never passed into either `set_var` call)

**Failure scenario:** Every Phase-1 tool-dispatch result and every Phase-2 tool-loop output is persisted into the `tool_results` namespace with no `project_id`, so it lands as a `project_id IS NULL` row. `load_vars_for_project` (called at `loop.py:2037` as `shared_vars = await eff_variable_repo.load_vars_for_project(project_id_val)`, and again at `loop.py:869` with an explicit `None`) unions real-project rows with ALL global/NULL rows — so one project's tool-call output (which may contain file contents, command output, secrets echoed by a tool, etc.) is loaded into `shared_vars` for every other project's subsequent dispatch. `project_id_val` is already a valid, populated local at both write sites (per the scope trace above), so this is not a missing-data problem, only a missing-argument one.

**Fix sketch:** Pass `project_id=project_id_val` on both `set_var` calls (`loop.py:2304-2308` and `loop.py:2523-2527`), matching the pattern already used a few lines away for `load_vars_for_project`/`apply_debt_findings` in the same method.

### A-COLLECTION-HANDLER-UNWRAP
**Severity:** MEDIUM (plausible)
**Files:**
- `src/general_ludd/daemon_wiring.py:204-259` (`make_collection_handler` — confirmed: `task_args = dict(args)` at line 222 is embedded directly as `{"name": f"gludd_dispatch:{name}", name: task_args}` at line 232, then `yaml.safe_dump(playbook, f, ...)` at line 247; only the module `name` is FQCN-validated at lines 215-219, `task_args`' values are never validated or wrapped)
- `src/general_ludd/ansible/runner.py:166-174` (`AnsibleRunnerAdapter.run_playbook(playbook_name, private_data_dir=None, extravars=None, env=None, timeout=None, **runner_kwargs)` — confirmed it DOES accept an `extravars` parameter, but `daemon_wiring.py:254` calls it as `runner_adapter.run_playbook(transient_name, timeout=timeout)` — no `extravars` passed at all)
- `src/general_ludd/ansible/unsafe.py:44-72` (`wrap_unsafe`/`wrap_extravars` — confirmed real, working helpers that mark values Ansible-unsafe so embedded Jinja is never re-templated)
- `src/general_ludd/ansible/core_runner.py:287-289` (confirmed: `CoreAnsibleRunner.run_playbook` DOES call `wrap_extravars(extravars)` before executing — i.e. the wrapping protection already exists and fires automatically for any caller that routes values through the `extravars` parameter; `AnsibleRunnerAdapter.run_playbook` itself does not call it directly but passes `extravars` down into `core_runner.py`, confirmed at `runner.py:203-208`)

**Failure scenario:** `make_collection_handler`'s transient playbook embeds caller/model-supplied `task_args` values directly as inline YAML task parameters rather than as Jinja-templated references resolved from `extravars` — so it bypasses the `wrap_extravars`/`wrap_unsafe` protection that already exists and is already wired for any caller using the `extravars` parameter on the exact same `run_playbook` call. A `task_args` value like `{"cmd": "{{ lookup('pipe','id') }}"}` is written as a literal task parameter in the playbook YAML, which Ansible then templates and evaluates — a templating-injection path that the codebase's own `wrap_extravars` mechanism was built to close, just not applied on this call path.

**Fix sketch:** Route `task_args`' values through `extravars` (passing `runner_adapter.run_playbook(transient_name, extravars=task_args, timeout=timeout)` and referencing `"{{ item }}"`-style variables in the task body) so they pick up the existing `wrap_extravars` protection automatically, instead of inlining raw values into the YAML task dict.

### A-VALIDATE-EXTRAVARS-ORPHAN
**Severity:** LOW (dead code)
**Files:**
- `src/general_ludd/execution/engine.py:32-83` (`validate_extra_vars_safe` — confirmed: a complete, working Jinja2-SSTI-pattern guard)
- Confirmed via a full-tree text search: the only other references to `validate_extra_vars_safe` anywhere in `src/` or `tests/` are the 16 hits inside `tests/unit/test_ansible_ssti_guard.py` (its own dedicated test file). Zero production call sites.

**Failure scenario:** None directly (the function is inert) — but its presence is misleading: a reviewer scanning `execution/engine.py` could reasonably believe extra-vars are SSTI-guarded on some real code path when the actual protection is `wrap_extravars`/`wrap_unsafe` (`ansible/unsafe.py`, wired through `ansible/core_runner.py:287-289`), a completely separate mechanism.

**Fix sketch:** Either delete `validate_extra_vars_safe` (and its test) as dead code, or wire it in as a defense-in-depth pre-check alongside `wrap_extravars` on a real `extravars`-accepting call path, and note in its docstring that `wrap_unsafe` — not this function — is the mechanism actually protecting production traffic today.

### A-HUMANGATE-NO-CHECKPOINTER
**Severity:** HIGH
**Files:**
- `src/general_ludd/execution/human_gate.py:62-73` (`_build_gate_graph` — confirmed: `builder = StateGraph(_GateState)` ... `return builder.compile()` at line 73, no `checkpointer=` argument passed)
- `src/general_ludd/execution/human_gate.py:67` (`decision = interrupt(message)` inside `gate_node` — confirmed this is the LangGraph `interrupt()` primitive, which requires a checkpointer to actually pause/resume across separate `ainvoke` calls)
- `src/general_ludd/execution/human_gate.py:128-191` (`await_approval` — confirmed: wraps `await self._graph.ainvoke(...)` in a `try/except Exception as exc: logger.error(...); return None` at lines 186-191)
- `src/general_ludd/event_loop/loop.py:3149-3163` (`d.decision == "complete" and self._human_gate.should_interrupt(...)` gate, confirmed verbatim — when `_gate_decision` comes back `None`, the code falls through and `new_status` is left at its prior value, i.e. COMPLETE proceeds exactly as if the gate had never fired)
- `src/general_ludd/config/user_config.py:130-132,212` (`class HumanInTheLoopConfig(BaseModel): enabled: bool = False` at lines 130-131, `confidence_threshold: float = 0.7` at line 132; instantiated as the `UserConfig` default `human_in_the_loop: HumanInTheLoopConfig = HumanInTheLoopConfig()` at line 212 — off by default, confirming this whole path is dormant unless explicitly enabled. Note: the file lives at `src/general_ludd/config/user_config.py`, not a top-level `src/general_ludd/user_config.py`.)

**Failure scenario:** `_build_gate_graph` compiles a graph that calls `interrupt()` but supplies no `checkpointer`, so the interrupt cannot actually persist a paused state across the async boundary the way LangGraph's HITL pattern requires. Whether that surfaces as an immediate exception, a silently-lost pause, or a same-call return depends on the installed LangGraph version's checkpointer-less behavior, but any failure path inside `ainvoke` is swallowed by the bare `except Exception` at `human_gate.py:186-191` and reported to the caller as `None`. `loop.py:3149-3163` treats a `None` gate decision identically to "gate not enabled" — the decision proceeds to COMPLETE without any human ever approving it, silently defeating the human-in-the-loop review this code path exists to provide. Gated off by default (`HumanInTheLoopConfig.enabled=False`), so this is a real bug that only bites once an operator turns HITL on, not a live default-config exposure.

**Fix sketch:** Compile `_build_gate_graph` with a real checkpointer (e.g. an in-memory or DB-backed LangGraph checkpointer scoped per daemon instance), and change `await_approval`/the `loop.py:3149` call site to distinguish "genuinely still pending, do not complete yet" from "gate mechanism failed" — the current `None`-means-proceed collapse must not be the failure-mode default for a safety gate.

### A-DEBT-EVAL-OFF / A-MISCONFIG-NOT-IN-LOOP
**Severity:** MEDIUM (feature-dead-in-prod)
**Files:**
- `src/general_ludd/event_loop/loop.py:446-448` (`self._debt_evaluator = DebtEvaluator(make_debt_evaluate_fn(self._model_gateway) if self._model_gateway else None)` — confirmed: the evaluator IS wired/instantiated)
- `src/general_ludd/event_loop/loop.py:1988-2029` (`_dispatch_execute_job` — confirmed: `debt_cfg = self.config.get("debt_eval", {})` at line 1995, gated `if isinstance(debt_cfg, dict) and debt_cfg.get("enabled", False):` at line 1996)
- Confirmed via a full-tree text search for `debt_eval`/`DebtEval`: every hit is in `src/general_ludd/planning/debt_applier.py`, `src/general_ludd/planning/debt_evaluator.py`, and `src/general_ludd/event_loop/loop.py` itself — **zero hits** in `src/general_ludd/daemon.py` or `src/general_ludd/config/user_config.py`. Nothing sets `config["debt_eval"]["enabled"]` to a truthy value anywhere in production code, and `UserConfig`'s Pydantic schema has no `debt_eval` field, so it cannot arrive via a user config file either — the flag is permanently `False`/absent in prod.
- `src/general_ludd/infra/model_deploy_check.py:95` (`class MisconfigDetector` — confirmed real, working detector)
- `src/general_ludd/routers/deployments.py:249,298` (`detector = MisconfigDetector()` inside `post_misconfig_check`/`post_suggest_fix` route handlers — confirmed verbatim)
- **Correction/broadening:** two additional construction sites exist beyond the two originally cited — `src/general_ludd/infra/fix_suggester.py:53` (inside `_build_fix_prompt`) and `src/general_ludd/infra/deploy_precheck.py:70` (inside `precheck()`). Both are still HTTP-router-triggered, not autonomous: `deploy_precheck.precheck()` is called only from `routers/compute.py:178`, and `fix_suggester.FixSuggester` is only instantiated from `routers/deployments.py:323`. Confirmed **zero** `MisconfigDetector(` construction sites in `daemon.py` or `event_loop/loop.py`, so the core claim ("not autonomous, no tick-path construction") holds even though "only in `deployments.py:249,298`" understated the router-side call count.
- FIM cross-check: confirmed `src/general_ludd/integrity/` (`__init__.py`, `change_log.py`, `fim_excludes.py`, `overlay_guard.py`, `scanner.py`) is strictly a file-integrity/tamper-detection monitor (hash-baseline + watchdog change detection, signed change-record log) — it has no code-editing capability, and no other feature named "FIM" exists anywhere in `src/general_ludd`. "FIM code-editing" as a concept does not exist in this codebase.

**Failure scenario:** The plan-time debt evaluator is fully wired into the dispatch path but gated on a config key (`debt_eval.enabled`) that no production code path, default config, or `UserConfig` schema field can ever set to `True` — it is permanently inert. Separately, `MisconfigDetector` is real, well-built, and reachable only from three HTTP route handlers (`deployments.py`, `compute.py` via `deploy_precheck`, and `fix_suggester`'s prompt-builder) — never from the daemon's autonomous tick loop — so it can only ever fire when a human/caller explicitly hits one of those endpoints, not as an ongoing background safeguard.

**Fix sketch:** Either add a `debt_eval` field to `UserConfig` (with a documented default) so operators can actually turn the feature on, or remove the dead gate and always run it at a fixed cadence. For `MisconfigDetector`, if autonomous misconfig detection is the intent, wire a periodic call into the event loop tick (mirroring how `preflight.py` is invoked at daemon startup) rather than leaving it purely request-driven.

### A-UNAUTH-READ-SURFACE
**Severity:** LOW (by-design, note for release)
**Files:**
- `src/general_ludd/daemon.py:2459-2463` (`_PUBLIC_PATHS = {"/healthz", "/readyz", "/api/status", "/api/todos", "/api/human-todos", "/api/webmcp", "/docs", "/openapi.json", "/redoc"}` — confirmed verbatim)
- `src/general_ludd/daemon.py:2478-2489` (`_is_public` — confirmed: public status requires an exact match against `_PUBLIC_PATHS` (or a `/docs`/`/render/` prefix) AND a safe method (`GET`/`HEAD`/`OPTIONS`); a mutating verb on any of these paths is NOT public)
- `src/general_ludd/routers/todos.py:497-567` (`GET /api/status`, handler `api_status` — confirmed real payload: `version`, `uptime_ticks`, `todos_total`, `queue_depths`, `tick_metrics`, `filestore_available`, `filestore_binaries`, `binary_versions`, `quality_gate`, `hardware` (CPU/memory via `HardwareProfile.to_dict()` — no GPU), `config_file_count`)
- `src/general_ludd/routers/todos.py:412` (`GET /api/todos/{todo_id}`) and `src/general_ludd/routers/human_todos.py:187` (`GET /api/human-todos/{human_todo_id}`) — these path-parameterized routes exist, but **correction:** `_is_public` only does an *exact string* match against `_PUBLIC_PATHS` (no prefix matching for `/api/todos`/`/api/human-todos`, unlike the special-cased `/docs`/`/render/` prefixes) — so `GET /api/todos/{id}` and `GET /api/human-todos/{id}` do **NOT** match `_is_public` and therefore DO require the PSK bearer token. Only the two collection endpoints (`GET /api/todos`, `GET /api/human-todos`) are actually anonymous-reachable, confirmed by `src/general_ludd/routers/human_todos.py:147-171` (`api_list_human_todos` returns the full `_human_todo_to_dict(r)` per row — real task content, not just IDs).
- **Correction:** `GET /api/status`'s payload does NOT include database dialect/engine info — confirmed by reading `routers/todos.py:497-567` and all of `daemon.py` in full: the literal string `"dialect"` does not appear in either file, and no engine-URL/dialect field is present anywhere in the response. The "db dialect" claim in the original finding is not supported by the code; drop it. "Config paths" is also an overstatement — the endpoint exposes only a numeric `config_file_count` (line ~566), not the actual config directory path(s) or filenames.
- `src/general_ludd/security/auth.py:102-111` (`verify_psk` — confirmed: `hmac.compare_digest(presented, expected)`, constant-time)

**Failure scenario (corrected):** Anonymous callers can reach `GET /api/status` (version, todo counts, queue depths, quality-gate result, CPU/memory hardware facts, and a config-file *count* — not paths, and no DB dialect), the `GET /api/todos` and `GET /api/human-todos` **collection** endpoints (full todo/human-todo content for every row, but NOT the individual `/{id}` sub-routes, which are correctly gated behind the PSK), and `/openapi.json`/`/docs`/`/redoc` (the full admin route map/schema). No unauthenticated state-change path exists (mutating verbs are excluded from `_is_public` regardless of path), and the bearer-token comparison is constant-time. This is a real anonymous information-disclosure surface (full todo/human-todo content plus the complete route map) but narrower than a naive reading of `_PUBLIC_PATHS` suggests, since the per-item sub-routes are not actually public.

**Fix sketch:** If the collection-level exposure (full todo/human-todo row content to anonymous callers) is intentional for the documented webmcp self-bootstrap flow, keep it but note it explicitly in a release note; if not, move `/api/todos`/`/api/human-todos` off `_PUBLIC_PATHS` and require the PSK like their `/{id}` siblings already do.
