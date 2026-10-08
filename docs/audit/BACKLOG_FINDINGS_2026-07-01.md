## RE-TRIAGE: 2026-07-11

### Summary
- **FIXED**: 8
- **OPEN**: 19
- **REFUTED**: 3
- **MITIGATED**: 1

Each finding annotated inline with [FIXED], [OPEN], [REFUTED], or [MITIGATED] and supporting evidence (commit hash, spec item ID, or reason). Cross-referenced against `AGENTIC_IMPLEMENTATION_SPEC.md` §3.0 (already-fixed list), status-update §3.0.1, and current master code.

---

# Audit backlog — 2026-07-01 (parallel auditor sweep)

## Adversarial verification verdicts (independent skeptic re-review)

| Finding | Verdict |
|---|---|
| Ansible `process_isolation` silent no-op (runs unconfined if podman on PATH) | **CONFIRMED** (worse: fail-open on podman-present) **[OPEN — no spec item yet; podman-present path still unconfined per core_runner.py:235-251]** |
| `/api/traces` XT-3/4 cross-tenant leak (no project_id on ExecutionTrace) | **CONFIRMED** end-to-end **[FIXED — C19: ExecutionTrace now has `project_id` field (tracer.py:92); `_traces_facet` scopes by project_id (facts.py:142,162). Commit 86389be.]** |
| Per-project secret isolation dead (`for_project` 0 callers, unscoped resolve) | **CONFIRMED** **[OPEN — `for_project` still has 0 callers in secrets/; SEC-5b only fixed the `resolve()` permission bypass, not per-project scoping]** |
| Security deny-list leading-slash drift (relative `.claude/` evades) | **CONFIRMED** (apply.py + capability_lattice; applier.py disagrees) **[OPEN — C9/C14: deny-list drift between `self_update/apply.py`, `security/capability_lattice.py`; both anchor `/.claude/` with leading slash, missing relative paths]** |
| Filestore executes downloaded binaries with no checksum/signature | **CONFIRMED** (RCE on hijacked redirect) **[FIXED — C16: `bootstrap.py:370-429` verifies pinned digest before chmod+exec; fails closed on mismatch. Commit 2543152b batch 1]** |
| CC-1 lease double-dispatch (requeue "status-only") | **REFUTED** — F1 live-lease SELECT guard (lease.py:79-94) already prevents it; no fencing column needed for THIS vector **[REFUTED — by auditor; F1 guard at lease.py:79-94]** |
| ToolCallLoop capability-lattice bypass (executes all MCP tools, no role check) | **CONFIRMED** (`_TOOL_USE_WORK_TYPES` gate only, no role param) **[OPEN — C15: Phase-2 ToolCallLoop still bypasses capability lattice; no role threaded through MCP dispatch]** |
| Worker fails auth-OPEN by default (drifted from daemon fail-closed) | **CONFIRMED** (serves `/jobs/execute` unauth in stock deploy) **[OPEN — C20: worker still defaults fail-open; daemon has `GLUDD_REQUIRE_AUTH` fail-closed path but worker does not mirror it; `tests/security/test_daemon_auth_redteam.py` asserts daemon-side only]** |
| Integrity baseline store (`integrity_db.json`) unsigned + silent rebaseline | **CONFIRMED** (HMAC only covers approval records, not the store) **[OPEN — C5: store still unsigned; corrupt store silently re-baselines per scanner.py:99-104]** |
| `TodoRepository.update()` mass-assignment (project_id rewrite, status bypass) + `TaskReturnRepository.get_by_id` unscoped | **CONFIRMED** (all 4 parts) **[FIXED — repository.py:258-271 `_validate_update_fields` + `_IMMUTABLE_UPDATE_FIELDS` reject mass-assignment of identity/tenant/audit fields; CAS guard at :280-318 with version+project_id in WHERE clause]** |
| `git merge_branch` bypasses lock+timeout, CWD confusion, squash fail-open | **CONFIRMED** (repo.py:594-637 vs gated_merge) **[OPEN — C17: merge_branch still bypasses per-repo lock+timeout wrapper; squash path check=False fail-open]** |
| ~24 urllib connectors follow redirects with no SSRF re-check | **CONFIRMED** (25 connectors default-opener; `monday.py` lone fix) **[LARGELY FIXED — C1: all 14 diverge sites consolidated onto canonical `security/ssrf.py`; tranche 5 (`4113f206`) + tranche 6 (`2543152b`). Substantially done per spec §3.0.1. Remaining: bug_class_registry.py:232 detector-allowlist fix]** |
| mcp `uvx` unpinned + partial-pin `pkg@2` floats | **CONFIRMED** (by-design exclusion; severity dispute) **[FIXED — C7: version pins enforced for npm-family/uvx path per spec §3.3; verified CLOSED. LOW residual: C27/MCP-1 (python/node argv validation)]** |
| Budget zero-cost estimate defeats pre-call gates | **REFUTED AS STATED** — the `estimate_call_cost`→gate chain is DEAD CODE; the wired path (daemon `_gateway_executor`) passes a POSITIVE projection (`__default__` pricing + max_output_tokens). **Narrower real residual**: literal `0.0` in `execution/engine.py:200/210` `_budget_pre_check` + `budget_guard_check.py:72` make THOSE two pre-checks reactive-only, and daemon `_projected_cost_usd` stays 0.0 only if there's no "default" profile. Downgrade + re-scope. **[REFUTED AS STATED — narrower residual tracked under C4/F1; F2 (NaN) verified CLOSED 2026-07-10]** |
| Accounting sync `git diff` subprocess on event loop + no tenant scoping | **CONFIRMED** (accounting.py:46, blocks loop 15s×N) **[OPEN — C18: blocking subprocess; no tenant scoping; NaN/Inf USD poisons JSON]** |
| Runtime bundle self-referential checksum (no signature) | **CONFIRMED** (release.py:37-63; skips missing files; CHECKSUMS.sha256 never parsed) **[OPEN — release.py now parses CHECKSUMS.sha256 and cross-checks against MANIFEST.json (lines 37-98), but the manifest itself remains unsigned; tamper-then-rewrite-both still passes. No cryptographic signature on the bundle.]** |
| Reload worker-registration leaks PSK to arbitrary address | **CONFIRMED** (worker_broadcast.py:34 no validation; Bearer PSK to `w.address`, cleartext over http) **[OPEN — C8: worker_broadcast.py still POSTs Bearer PSK to any registered address without validation]** |
| Self-improve auto_queue=True default (wired gate); hardened gate + approval + dedup are dead code | **CONFIRMED** (self_improve/gate.py:25, loop.py:2653-2661; controller/approval/dedup 0 prod callers) **[OPEN — C13: self_improve/gate.py:25 still defaults auto_queue=True; hardened gate (controllers/self_improve_gate.py:72) still unwired]** |
| Gateway `init_kwargs.update(kwargs)` after SSRF check (kwargs base_url override) | **CONFIRMED but LATENT** (gateway.py:541-563; no current caller forwards untrusted base_url — defense-in-depth gap) **[OPEN — C6: per-call kwargs can still override SSRF-validated base_url/api_key; defense-in-depth gap]** |

**Net: of the top ~18 findings adversarially re-reviewed, 16 CONFIRMED with precise file:line evidence, 2 corrected by re-review (CC-1 lease REFUTED — F1 guard defends it; budget zero-cost REFUTED-as-stated — dead-code chain, narrower real residual).** This backlog is verification-grade and self-correcting; each still needs line-# re-pin at fix time. Fix proposals (unified-diff, not applied) drafted for the top items — see session artifacts.

## Remediation readiness (test-coverage cross-check)

| Finding | Existing test locks in the bug? | Fix approach |
|---|---|---|
| Ansible isolation no-op | No (redteam test asserts fail-closed) | **RE-PIN FIRST** — `core_runner.py:235-251` may already be fail-closed for podman-ABSENT; the CONFIRMED bug is the podman-PRESENT unconfined path. Reconcile the two paths before fixing. **[OPEN]** |
| XT-3/4 traces leak | No coverage | **Add-only** — mirror `test_accounting_facet_no_leak.py` **[FIXED — C19]** |
| Budget zero-cost (residual) | No estimator coverage | **Add-only** if scoped to estimator→None + the two literal-0.0 pre-checks **[REFUTED AS STATED — narrower C4/F1 residual]** |
| Security deny-list drift | No coverage of the drifted lists | **Add-only** — applier's `PROTECTED_PATH_MARKERS` already asserts the desired end-state **[OPEN — C9/C14]** |
| Self-improve auto-queue | **YES** (`test_self_improve_slice.py::test_default_auto_queues_for_claimability`) | Must flip the bug-locking test **[OPEN — C13]** |
| ToolCallLoop capability bypass | **YES** (`test_mcp_redteam.py::TestFinding3CapabilityGate`) | Must update tests + add role param **[OPEN — C15]** |
| Worker fail-open auth | **YES** (`test_w5_6_worker_auth.py::test_no_psk_set_means_no_auth`) | Must flip test (501→503) + audit other no-PSK worker tests **[OPEN — C20]** |
| Filestore no-checksum | **YES** (`test_filestore.py::TestBinaryBootstrapper`) | Must supply valid sha256 in tests or expect-rejection **[FIXED — C16]** |

Add-only fixes are the safest first candidates. The four "bug-locking-test" items each require flipping a test that currently asserts the insecure behavior as correct — do those deliberately (the flip IS part of the fix).



Findings gathered by read-only auditors run alongside the agent-hibernation feature
work. Each needs re-pinning against current source before scheduling (line numbers
drift). Grouped by subsystem; severity is the auditor's call.

## Budget / spend controllers (`controllers/`)
- **F1 HIGH — zero-cost estimate defeats every pre-call gate.** `pid.py:143`
  `estimate_call_cost` returns `0.0` (not `None`) for unknown tokens/price; all
  downstream gates (`budget_manager.check_todo_budget/check_daily_budget`,
  `budget.check_per_call`, `spend_limiter.would_exceed`) treat `0.0` as free →
  one over-budget call always slips through. Fix: make "unknown" a first-class
  `None`, route it to the fail-closed branches. (Matches the long-known #49 inert-dispatch gap.)
  **[REFUTED AS STATED per auditor re-review — `estimate_call_cost`→gate chain is DEAD CODE; wired path passes positive projection. Narrower residual: literal 0.0 in engine.py:200/210 + budget_guard_check.py:72. Tracked as C4/F1.]**
- **F2 HIGH — `RunBudgetGuard.check_per_call` fails OPEN on NaN.** `budget.py:82`
  `NaN > cap` is False → allowed. Add `math.isfinite` guard, fail closed.
  **[FIXED — C4/F2 verified CLOSED 2026-07-10; `check_per_call` (budget.py:86-91) fails closed on non-finite cost; `ModelGateway.check_budget` at gateway.py:560-563 also fails closed (NaN clamped to 0.0, non-finite cost → +inf)]**
- **F3 MED — `RunBudgetGuard` no reservation → concurrent-overshoot TOCTOU** (`budget.py`). **[OPEN — C4/F3]**
- **F4 MED — `SpendLimiter.restore` uses monotonic ts that don't survive restart** → premature prune evades cap (`spend_limiter.py:74`). **[OPEN — C4/F4; also SPD-1 (spend persistence dead code)]**
- **F5/F6 LOW — restore extends (double-count); daily rollover leaves stale reservations.** **[OPEN — C4/F5-F6]**

## Integrity scanner + secrets (`integrity/`, `secrets/`)
- **H1 HIGH — integrity baseline store (`integrity_db.json`) is unsigned** — attacker who edits a monitored file can edit its stored hash; scan reports "no change." HMAC-sign the store with `GL_INTEGRITY_KEY`, verify on load. (`scanner.py:97-108`) **[OPEN — C5]**
- **H2 HIGH — corrupt/truncated store silently rebaselines** — distinguish missing (first run) from unparseable (tamper → fail closed). (`scanner.py:99-104`) **[OPEN — C5]**
- **M1 MED — non-canonical `"|".join` HMAC payload → field-injection collision.** **[OPEN — C5]**
- **M3 MED — `secrets/manager.py:188` `resolve()` skips `_enforce_permission`.** **[FIXED — SEC-5b: `resolve()` now enforces permission check at manager.py:286-295 before returning a secret]**
- Verified GOOD: timing-safe compares, repr/log redaction, fail-closed design. **[FIXED — CosignKey repr leak also fixed (cosign.py:20,22: private_key+password use `field(repr=False)`)]**

## Gateway + MCP (`models/gateway.py`, `mcp/`)
- **H1 HIGH — caller `kwargs` override SSRF-validated `base_url`/`api_key`.** `gateway.py:561`
  `init_kwargs.update(kwargs)` runs AFTER the SSRF check → bypass. Validate the FINAL
  effective `base_url`; refuse caller-supplied `api_key`/`base_url` unless intended. **[OPEN — C6; defense-in-depth gap, no current caller exploits it]**
- **M1 MED — no request/connect timeout on provider client → thread hang** (`gateway.py`). **[OPEN — C6]**
- **M2 MED — `python`/`node` allowlisted with no argv validation** (`transport.py:28`) → RCE if MCP config becomes lower-trust; reject `-c`/`-e`/`--eval`. **[FIXED — C7: argv allowlisting per interpreter landed; version pins fixed for npm-family/uvx. LOW residual: C27/MCP-1 (python/node launcher argv validation)]**
- **M3 MED — alias-resolved URL (may contain userinfo) leaked in SSRF error msg.** **[OPEN — C6]**
- L-tier: DNS-rebinding gap, untyped JSON-RPC frames, incomplete YAML secret scrub. **[OPEN — C6 M2-M3]**

## Reload (`reload/`)
- **H1 HIGH — TOCTOU between snapshot read and atomic swap** defeats the anti-clobber merge (`hot_reloader.py:200→240`). Add flock/CAS around read→merge→replace. **[OPEN — C8]**
- **H2 HIGH — unauthenticated worker registration leaks PSK / SSRF** — `broadcast_reload` POSTs `Bearer <GLUDD_AUTH_PSK>` to any registered `address` (`worker_broadcast.py:34`). Auth+allowlist registration, require https, deny link-local/loopback/metadata. **[OPEN — C8]**
- **M1 MED — no concurrency guard on reloads** (importlib + shared-state races). **[OPEN — C8]**
- **M2 MED — dict-changed-size during broadcast iteration** — snapshot `list(...)`. **[OPEN — C8]**
- **M3 MED — lexical-only protected-path check is symlink/`..`-bypassable** — realpath first. **[OPEN — C8]**
- **M4 MED — no reload rate-limit / re-entrancy guard → unbounded reload loop.** **[OPEN — C8]**

## Self-update (`self_update/`)
- **F1 MED/HIGH — deny-list drift:** `apply._HARD_DENY_SUBSTRINGS` + `capability_lattice.PROTECTED_PATH_SUBSTRINGS` anchor `/.claude/`,`/.opencode/` with a LEADING SLASH, so workspace-**relative** `.claude/hooks/...` / `.opencode/plugin/...` evade the guard (while `applier.py` catches them). Unify the marker lists; match as path segments. **[OPEN — C9; also tracked under C14 for capability_lattice side]**
- F2 LOW/MED parent-dir TOCTOU; F3 LOW cwd-anchored resolve; F5 empty-targets false "applied". **[OPEN — C9]**
- Confirmed GOOD: `AtomicSafeWriter._confine` blocks `../`, absolute, symlink escapes.

## Execution engine (`execution/engine.py`)
- **#1 HIGH — `execute()` schedules benchmark via `asyncio.create_task` in a sync method** → RuntimeError swallowed by bare `except: pass`; benchmark recording silently never fires (sync path). **[OPEN — C10]**
- #3/#4 MED — `execute_async` runs blocking `_run_tests` on the loop + races the deferred commit on the same tree. **[OPEN — C10]**
- #5 MED — `_background_tasks` never drained → deferred commits lost on shutdown ("commit deferred" can be a false completion). **[OPEN — C10]**

## Event loop (`event_loop/loop.py`) — loop never hard-pinned, but:
- **#1 HIGH — DB session pinned across multi-minute model/playbook exec** (`_dispatch_execute_job_isolated:1258`) → pool exhaustion under batch. Scope sessions narrowly around the reads/writes, not the long work. **[OPEN — C11; partially addressed by session-per-job in `_dispatch_execute_job_isolated`; remaining pins to verify]**
- #2/#3 MED — shared default ThreadPoolExecutor saturation; unbounded `asyncio.gather` fan-out (add a Semaphore). **[OPEN — C11]**

## Events + metrics (`events/`, `metrics/collector.py`)
- **H1 MED — `hooks.fire()` iterates `_hooks` list while register appends to it** → "list changed size during iteration". Snapshot `list(...)`; lock `_hooks`/`_next_cb_id`. **[OPEN — C12; note: SEC-4 fixed webhook delivery (async httpx with redaction at hooks.py:241-296) but list-mutation-during-iteration is a separate bug]**
- **B1/B2 MED — `EventBus` has zero locking** on `_subscribers`/`_next_id`/`_history`. **[OPEN — C12]**
- **B3 LOW — double-invocation** of async callbacks whose result isn't recognized as a coroutine. **[OPEN — C12]**
- collector RLock design is sound.

---
# Second-wave findings (same sweep, additional subsystems)

## Self-improve (`self_improve/`, `controllers/self_improve_gate.py`) — the safety layer is DEAD CODE
- **HIGH — approval bypass by default:** wired gate `self_improve/gate.py:25` defaults `auto_queue=True` (loop.py:2660) → self-authored code/test todos auto-QUEUED and executed with NO human review. The hardened `controllers/self_improve_gate.py:72` (safe `auto_queue=False`) is the UNWIRED one. **[OPEN — C13]**
- **HIGH — `allow_auto_promote` backdoor** rewrites APPROVAL_REQUIRED→QUEUED even when auto_queue=False (`gate.py:38`). **[OPEN — C13]**
- **HIGH — human-approval path is dead code:** `SelfImproveApprovalManager` (approval.py) has zero callers → a `auto_queue=false` todo lands in APPROVAL_REQUIRED with no wired release mechanism. **[OPEN — C13]**
- **MED — `/admin/self-improve/run` bypasses the gate** (creates todos directly, no cap/approval). Dedup (`dedup.py`) unwired → same gap re-files every cycle. `harness.write_config_value` is an unconfined self-config-rewrite primitive. **[OPEN — C13]**

## Security (`security/`) — confirms the reported drift + new escalations
- **HIGH — deny-list leading-slash drift:** `capability_lattice.PROTECTED_PATH_SUBSTRINGS` anchors `/.claude/`,`/.opencode/`; relative `.claude/hooks/...` evades `is_protected_path` → a role can self-modify guardrail files. Match as path SEGMENTS + realpath first. **[OPEN — C14; overlaps C9]**
- **HIGH — `_intersect_constraints` widens file scope:** picks the longer prefix with no containment check (`permissions.py:514`) → subagent gets file access the human spec never granted. Return None if neither contains the other. **[OPEN — C14]**
- **MED — STS re-delegation escalates TTL ceiling** (`sts.py:142`, is_subset ignores max_sts_ttl_seconds); **denied grants not enforced through delegation** (`intersection` never subtracts denied); self-mod guards match unresolved path (symlink/`..` bypass). **[OPEN — C14]**

## Tool-call loop (`execution/tool_loop.py`, `dispatch/`)
- **HIGH — capability lattice bypassed on Phase-2 ToolCallLoop:** binds+executes EVERY advertised MCP tool with no role/capability check (only work_type gate). Thread the acting role + `check_dispatch(role,"mcp")`. **[OPEN — C15]**
- **MED — no per-response tool-call cap / time budget** (dispatcher paths cap at 20, ToolCallLoop doesn't). **MED — tool args never validated against `input_schema`** (JSON-decode failure silently runs tool with `{}`). **[OPEN — C15]**
- **LOW/MED — VariableStore key injection:** model-controlled tool `name` can forge the `dispatch__last__*` sentinel keys rendered into the next prompt. **[OPEN — C15]**

## MCP launch (`mcp/transport.py`)
- **MED — partial version pins float:** `_VERSION_PINNED_RE` accepts `pkg@2`/`pkg@1.2` (npm resolves to latest matching) → supply-chain bytes float. Require full semver. **[FIXED — C7: version pins enforced per spec §3.3; verified CLOSED]**
- **MED — `uvx` remote-fetch launcher has NO pin enforcement** (excluded from npm-family pin check) → latest PyPI build at launch. **[FIXED — C7: uvx path now has pin enforcement]**
- **MED — tool-manifest fields (description/input_schema) trusted, unbounded, forwarded to LLM** → tool-poisoning / memory DoS. **[FIXED — C7; LOW residual: C27/MCP-1 argv validation]**

## Filestore (`filestore/bootstrap.py`)
- **HIGH — no checksum/signature verification** of downloaded OR bundled binaries before chmod +x and execute (`bootstrap.py:266-308`). `follow_redirects=True` + trusted bytes = RCE on a hijacked redirect. Ship a hardcoded sha256 map, verify before store/chmod. Also: no response/extraction size cap (decompression bomb); non-atomic store writes. **[FIXED — C16: bootstrap.py:370-429 verifies pinned digest before chmod+exec; fails closed on mismatch. Commit 2543152b batch 1]**

## Git automation (`git_automation/repo.py`)
- **MED — GA-3 `merge_branch` bypasses lock+timeout domain** and mutates the `repo_path` param while `self.repo_path` differs (CWD confusion, index.lock races). Squash commit uses `check=False` → returns `success=True` on failed commit (fail-open). Route through the locked/timed `_run_git`. **[OPEN — C17]**
- **MED — GA-1 family** (create_release_tag/worktree/init_repo/…) all bypass the lock+timeout wrapper. **LOW — `tag_release`/`tag_checkpoint` miss `_reject_leading_dash`** (option injection). **[OPEN — C17]**

## Accounting (`accounting/ledger.py`, `routers/accounting.py`)
- **HIGH — blocking `subprocess.run(git diff)` in async route** × N projects → event-loop DoS. **HIGH — no per-caller tenant scoping** (returns all projects' financials to any PSK holder); LocLedger unlocked + bypassed. **MED — NaN/Inf USD poisons JSON**; `points` are actually `priority`. **[OPEN — C18]**

## Code intelligence (`code_intelligence/`)
- **MED — `rg_search` root unconfined** → arbitrary filesystem read if root/query is model-sourced; unbounded output buffering. **MED — git_intel O(n²) pair blowup**, callgraph quadratic. git_intel injection defense is sound. **[OPEN — rg_search root still unconfined; `build_argv` flag allowlist improved but no root-prefix jail]**

## Infra (`infra/`)
- **MED — vSphere `allow_unverified_ssl=true`** (`terraform.py:649`) sends admin creds with TLS verify off → MITM. `allowed_cidr` default RESOLVED (loopback). LOW — Slurm REST no https enforcement (token leak over http). **[MITIGATED — compute.py:88 default `vsphere_verify_ssl: bool = True` → `allow_unverified_ssl = false` by default. Configurable but secure-by-default.]**

## Issue sources (`issue_sources/`)
- LOW/MED — literal-only SSRF guards (DNS-rebind + alt-IP-encoding bypass), redirects rely on httpx default, unbounded `resp.json()`, openpyxl XXE/zip-bomb exposure. Credentials env-only + path jailing are sound. **[LARGELY FIXED — C1: SSRF consolidated onto canonical ssrf.py; DNS-rebind/alt-IP-encoding still residual at some sites; C1 meta-test tracks remaining]**

## Runtime release (`runtime/`)
- **HIGH — bundle "integrity" is self-referential** (checksums co-located with artifacts, no signature) → tamper-then-rewrite-manifest passes. Validation ignores missing/extra files. Sign the manifest. **[OPEN — release.py now cross-checks CHECKSUMS.sha256 vs MANIFEST.json and detects missing/extra files, but no cryptographic signature on the manifest itself]**

## Remediation (`remediation/`)
- **MED — no idempotency guard:** `remediate` never checks action history/cooldown and never transitions the source todo out of the qualifying state → each `/admin/remediation/remediate` call re-remediates the same blocks (retry-storm if any loop/cron polls it). **[OPEN — C25]**

## Observability (`observability/`)
- **CRITICAL/HIGH — XT-3/XT-4 `/api/traces` cross-tenant leak CONFIRMED end-to-end:** `ExecutionTrace` has no `project_id`, `_traces_facet` passes none (facts.py:417). Mirror `FeatureRepository.scoped`. `RunHistoryRecorder` is dead code with latent IDOR. **[FIXED — C19: ExecutionTrace now has project_id (tracer.py:92); _traces_facet passes project_id to buffer.snapshot() (facts.py:142,162); None-project traces excluded for scoped callers. Commit 86389be.]**
