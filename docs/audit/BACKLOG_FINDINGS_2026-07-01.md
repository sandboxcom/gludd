# Audit backlog — 2026-07-01 (parallel auditor sweep)

## Adversarial verification verdicts (independent skeptic re-review)

| Finding | Verdict |
|---|---|
| Ansible `process_isolation` silent no-op (runs unconfined if podman on PATH) | **CONFIRMED** (worse: fail-open on podman-present) |
| `/api/traces` XT-3/4 cross-tenant leak (no project_id on ExecutionTrace) | **CONFIRMED** end-to-end |
| Per-project secret isolation dead (`for_project` 0 callers, unscoped resolve) | **CONFIRMED** |
| Security deny-list leading-slash drift (relative `.claude/` evades) | **CONFIRMED** (apply.py + capability_lattice; applier.py disagrees) |
| Filestore executes downloaded binaries with no checksum/signature | **CONFIRMED** (RCE on hijacked redirect) |
| CC-1 lease double-dispatch (requeue "status-only") | **REFUTED** — F1 live-lease SELECT guard (lease.py:79-94) already prevents it; no fencing column needed for THIS vector |
| ToolCallLoop capability-lattice bypass (executes all MCP tools, no role check) | **CONFIRMED** (`_TOOL_USE_WORK_TYPES` gate only, no role param) |
| Worker fails auth-OPEN by default (drifted from daemon fail-closed) | **CONFIRMED** (serves `/jobs/execute` unauth in stock deploy) |
| Integrity baseline store (`integrity_db.json`) unsigned + silent rebaseline | **CONFIRMED** (HMAC only covers approval records, not the store) |
| `TodoRepository.update()` mass-assignment (project_id rewrite, status bypass) + `TaskReturnRepository.get_by_id` unscoped | **CONFIRMED** (all 4 parts) |
| `git merge_branch` bypasses lock+timeout, CWD confusion, squash fail-open | **CONFIRMED** (repo.py:594-637 vs gated_merge) |
| ~24 urllib connectors follow redirects with no SSRF re-check | **CONFIRMED** (25 connectors default-opener; `monday.py` lone fix) |
| mcp `uvx` unpinned + partial-pin `pkg@2` floats | **CONFIRMED** (by-design exclusion; severity dispute) |
| Budget zero-cost estimate defeats pre-call gates | **REFUTED AS STATED** — the `estimate_call_cost`→gate chain is DEAD CODE; the wired path (daemon `_gateway_executor`) passes a POSITIVE projection (`__default__` pricing + max_output_tokens). **Narrower real residual**: literal `0.0` in `execution/engine.py:200/210` `_budget_pre_check` + `budget_guard_check.py:72` make THOSE two pre-checks reactive-only, and daemon `_projected_cost_usd` stays 0.0 only if there's no "default" profile. Downgrade + re-scope. |
| Accounting sync `git diff` subprocess on event loop + no tenant scoping | **CONFIRMED** (accounting.py:46, blocks loop 15s×N) |
| Runtime bundle self-referential checksum (no signature) | **CONFIRMED** (release.py:37-63; skips missing files; CHECKSUMS.sha256 never parsed) |
| Reload worker-registration leaks PSK to arbitrary address | **CONFIRMED** (worker_broadcast.py:34 no validation; Bearer PSK to `w.address`, cleartext over http) |
| Self-improve auto-queue=True default (wired gate); hardened gate + approval + dedup are dead code | **CONFIRMED** (self_improve/gate.py:25, loop.py:2653-2661; controller/approval/dedup 0 prod callers) |
| Gateway `init_kwargs.update(kwargs)` after SSRF check (kwargs base_url override) | **CONFIRMED but LATENT** (gateway.py:541-563; no current caller forwards untrusted base_url — defense-in-depth gap) |

**Net: of the top ~18 findings adversarially re-reviewed, 16 CONFIRMED with precise file:line evidence, 2 corrected by re-review (CC-1 lease REFUTED — F1 guard defends it; budget zero-cost REFUTED-as-stated — dead-code chain, narrower real residual).** This backlog is verification-grade and self-correcting; each still needs line-# re-pin at fix time. Fix proposals (unified-diff, not applied) drafted for the top items — see session artifacts.

## Remediation readiness (test-coverage cross-check)

| Finding | Existing test locks in the bug? | Fix approach |
|---|---|---|
| Ansible isolation no-op | No (redteam test asserts fail-closed) | **RE-PIN FIRST** — `core_runner.py:235-251` may already be fail-closed for podman-ABSENT; the CONFIRMED bug is the podman-PRESENT unconfined path. Reconcile the two paths before fixing. |
| XT-3/4 traces leak | No coverage | **Add-only** — mirror `test_accounting_facet_no_leak.py` |
| Budget zero-cost (residual) | No estimator coverage | **Add-only** if scoped to estimator→None + the two literal-0.0 pre-checks |
| Security deny-list drift | No coverage of the drifted lists | **Add-only** — applier's `PROTECTED_PATH_MARKERS` already asserts the desired end-state |
| Self-improve auto-queue | **YES** (`test_self_improve_slice.py::test_default_auto_queues_for_claimability`) | Must flip the bug-locking test |
| ToolCallLoop capability bypass | **YES** (`test_mcp_redteam.py::TestFinding3CapabilityGate`) | Must update tests + add role param |
| Worker fail-open auth | **YES** (`test_w5_6_worker_auth.py::test_no_psk_set_means_no_auth`) | Must flip test (501→503) + audit other no-PSK worker tests |
| Filestore no-checksum | **YES** (`test_filestore.py::TestBinaryBootstrapper`) | Must supply valid sha256 in tests or expect-rejection |

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
- **F2 HIGH — `RunBudgetGuard.check_per_call` fails OPEN on NaN.** `budget.py:82`
  `NaN > cap` is False → allowed. Add `math.isfinite` guard, fail closed.
- **F3 MED — `RunBudgetGuard` no reservation → concurrent-overshoot TOCTOU** (`budget.py`).
- **F4 MED — `SpendLimiter.restore` uses monotonic ts that don't survive restart** → premature prune evades cap (`spend_limiter.py:74`).
- **F5/F6 LOW — restore extends (double-count); daily rollover leaves stale reservations.**

## Integrity scanner + secrets (`integrity/`, `secrets/`)
- **H1 HIGH — integrity baseline store (`integrity_db.json`) is unsigned** — attacker who edits a monitored file can edit its stored hash; scan reports "no change." HMAC-sign the store with `GL_INTEGRITY_KEY`, verify on load. (`scanner.py:97-108`)
- **H2 HIGH — corrupt/truncated store silently rebaselines** — distinguish missing (first run) from unparseable (tamper → fail closed). (`scanner.py:99-104`)
- **M1 MED — non-canonical `"|".join` HMAC payload → field-injection collision.**
- **M3 MED — `secrets/manager.py:188` `resolve()` skips `_enforce_permission`.**
- Verified GOOD: timing-safe compares, repr/log redaction, fail-closed design.

## Gateway + MCP (`models/gateway.py`, `mcp/`)
- **H1 HIGH — caller `kwargs` override SSRF-validated `base_url`/`api_key`.** `gateway.py:561`
  `init_kwargs.update(kwargs)` runs AFTER the SSRF check → bypass. Validate the FINAL
  effective `base_url`; refuse caller-supplied `api_key`/`base_url` unless intended.
- **M1 MED — no request/connect timeout on provider client → thread hang** (`gateway.py`).
- **M2 MED — `python`/`node` allowlisted with no argv validation** (`transport.py:28`) → RCE if MCP config becomes lower-trust; reject `-c`/`-e`/`--eval`.
- **M3 MED — alias-resolved URL (may contain userinfo) leaked in SSRF error msg.**
- L-tier: DNS-rebinding gap, untyped JSON-RPC frames, incomplete YAML secret scrub.

## Reload (`reload/`)
- **H1 HIGH — TOCTOU between snapshot read and atomic swap** defeats the anti-clobber merge (`hot_reloader.py:200→240`). Add flock/CAS around read→merge→replace.
- **H2 HIGH — unauthenticated worker registration leaks PSK / SSRF** — `broadcast_reload` POSTs `Bearer <GLUDD_PSK>` to any registered `address` (`worker_broadcast.py:34`). Auth+allowlist registration, require https, deny link-local/loopback/metadata.
- **M1 MED — no concurrency guard on reloads** (importlib + shared-state races).
- **M2 MED — dict-changed-size during broadcast iteration** — snapshot `list(...)`.
- **M3 MED — lexical-only protected-path check is symlink/`..`-bypassable** — realpath first.
- **M4 MED — no reload rate-limit / re-entrancy guard → unbounded reload loop.**

## Self-update (`self_update/`)
- **F1 MED/HIGH — deny-list drift:** `apply._HARD_DENY_SUBSTRINGS` + `capability_lattice.PROTECTED_PATH_SUBSTRINGS` anchor `/.claude/`,`/.opencode/` with a LEADING SLASH, so workspace-**relative** `.claude/hooks/...` / `.opencode/plugin/...` evade the guard (while `applier.py` catches them). Unify the marker lists; match as path segments.
- F2 LOW/MED parent-dir TOCTOU; F3 LOW cwd-anchored resolve; F5 empty-targets false "applied".
- Confirmed GOOD: `AtomicSafeWriter._confine` blocks `../`, absolute, symlink escapes.

## Execution engine (`execution/engine.py`)
- **#1 HIGH — `execute()` schedules benchmark via `asyncio.create_task` in a sync method** → RuntimeError swallowed by bare `except: pass`; benchmark recording silently never fires (sync path).
- #3/#4 MED — `execute_async` runs blocking `_run_tests` on the loop + races the deferred commit on the same tree.
- #5 MED — `_background_tasks` never drained → deferred commits lost on shutdown ("commit deferred" can be a false completion).

## Event loop (`event_loop/loop.py`) — loop never hard-pinned, but:
- **#1 HIGH — DB session pinned across multi-minute model/playbook exec** (`_dispatch_execute_job_isolated:1258`) → pool exhaustion under batch. Scope sessions narrowly around the reads/writes, not the long work.
- #2/#3 MED — shared default ThreadPoolExecutor saturation; unbounded `asyncio.gather` fan-out (add a Semaphore).

## Events + metrics (`events/`, `metrics/collector.py`)
- **H1 MED — `hooks.fire()` iterates `_hooks` list while register appends to it** → "list changed size during iteration". Snapshot `list(...)`; lock `_hooks`/`_next_cb_id`.
- **B1/B2 MED — `EventBus` has zero locking** on `_subscribers`/`_next_id`/`_history`.
- **B3 LOW — double-invocation** of async callbacks whose result isn't recognized as a coroutine.
- collector RLock design is sound.

---
# Second-wave findings (same sweep, additional subsystems)

## Self-improve (`self_improve/`, `controllers/self_improve_gate.py`) — the safety layer is DEAD CODE
- **HIGH — approval bypass by default:** wired gate `self_improve/gate.py:25` defaults `auto_queue=True` (loop.py:2660) → self-authored code/test todos auto-QUEUED and executed with NO human review. The hardened `controllers/self_improve_gate.py:72` (safe `auto_queue=False`) is the UNWIRED one.
- **HIGH — `allow_auto_promote` backdoor** rewrites APPROVAL_REQUIRED→QUEUED even when auto_queue=False (`gate.py:38`).
- **HIGH — human-approval path is dead code:** `SelfImproveApprovalManager` (approval.py) has zero callers → a `auto_queue=false` todo lands in APPROVAL_REQUIRED with no wired release mechanism.
- **MED — `/admin/self-improve/run` bypasses the gate** (creates todos directly, no cap/approval). Dedup (`dedup.py`) unwired → same gap re-files every cycle. `harness.write_config_value` is an unconfined self-config-rewrite primitive.

## Security (`security/`) — confirms the reported drift + new escalations
- **HIGH — deny-list leading-slash drift:** `capability_lattice.PROTECTED_PATH_SUBSTRINGS` anchors `/.claude/`,`/.opencode/`; relative `.claude/hooks/...` evades `is_protected_path` → a role can self-modify guardrail files. Match as path SEGMENTS + realpath first.
- **HIGH — `_intersect_constraints` widens file scope:** picks the longer prefix with no containment check (`permissions.py:514`) → subagent gets file access the human spec never granted. Return None if neither contains the other.
- **MED — STS re-delegation escalates TTL ceiling** (`sts.py:142`, is_subset ignores max_sts_ttl_seconds); **denied grants not enforced through delegation** (`intersection` never subtracts denied); self-mod guards match unresolved path (symlink/`..` bypass).

## Tool-call loop (`execution/tool_loop.py`, `dispatch/`)
- **HIGH — capability lattice bypassed on Phase-2 ToolCallLoop:** binds+executes EVERY advertised MCP tool with no role/capability check (only work_type gate). Thread the acting role + `check_dispatch(role,"mcp")`.
- **MED — no per-response tool-call cap / time budget** (dispatcher paths cap at 20, ToolCallLoop doesn't). **MED — tool args never validated against `input_schema`** (JSON-decode failure silently runs tool with `{}`).
- **LOW/MED — VariableStore key injection:** model-controlled tool `name` can forge the `dispatch__last__*` sentinel keys rendered into the next prompt.

## MCP launch (`mcp/transport.py`)
- **MED — partial version pins float:** `_VERSION_PINNED_RE` accepts `pkg@2`/`pkg@1.2` (npm resolves to latest matching) → supply-chain bytes float. Require full semver.
- **MED — `uvx` remote-fetch launcher has NO pin enforcement** (excluded from npm-family pin check) → latest PyPI build at launch.
- **MED — tool-manifest fields (description/input_schema) trusted, unbounded, forwarded to LLM** → tool-poisoning / memory DoS.

## Filestore (`filestore/bootstrap.py`)
- **HIGH — no checksum/signature verification** of downloaded OR bundled binaries before chmod +x and execute (`bootstrap.py:266-308`). `follow_redirects=True` + trusted bytes = RCE on a hijacked redirect. Ship a hardcoded sha256 map, verify before store/chmod. Also: no response/extraction size cap (decompression bomb); non-atomic store writes.

## Git automation (`git_automation/repo.py`)
- **MED — GA-3 `merge_branch` bypasses lock+timeout domain** and mutates the `repo_path` param while `self.repo_path` differs (CWD confusion, index.lock races). Squash commit uses `check=False` → returns `success=True` on failed commit (fail-open). Route through the locked/timed `_run_git`.
- **MED — GA-1 family** (create_release_tag/worktree/init_repo/…) all bypass the lock+timeout wrapper. **LOW — `tag_release`/`tag_checkpoint` miss `_reject_leading_dash`** (option injection).

## Accounting (`accounting/ledger.py`, `routers/accounting.py`)
- **HIGH — blocking `subprocess.run(git diff)` in async route** × N projects → event-loop DoS. **HIGH — no per-caller tenant scoping** (returns all projects' financials to any PSK holder); LocLedger unlocked + bypassed. **MED — NaN/Inf USD poisons JSON**; `points` are actually `priority`.

## Code intelligence (`code_intelligence/`)
- **MED — `rg_search` root unconfined** → arbitrary filesystem read if root/query is model-sourced; unbounded output buffering. **MED — git_intel O(n²) pair blowup**, callgraph quadratic. git_intel injection defense is sound.

## Infra (`infra/`)
- **MED — vSphere `allow_unverified_ssl=true`** (`terraform.py:649`) sends admin creds with TLS verify off → MITM. `allowed_cidr` default RESOLVED (loopback). LOW — Slurm REST no https enforcement (token leak over http).

## Issue sources (`issue_sources/`)
- LOW/MED — literal-only SSRF guards (DNS-rebind + alt-IP-encoding bypass), redirects rely on httpx default, unbounded `resp.json()`, openpyxl XXE/zip-bomb exposure. Credentials env-only + path jailing are sound.

## Runtime release (`runtime/`)
- **HIGH — bundle "integrity" is self-referential** (checksums co-located with artifacts, no signature) → tamper-then-rewrite-manifest passes. Validation ignores missing/extra files. Sign the manifest.

## Remediation (`remediation/`)
- **MED — no idempotency guard:** `remediate` never checks action history/cooldown and never transitions the source todo out of the qualifying state → each `/admin/remediation/remediate` call re-remediates the same blocks (retry-storm if any loop/cron polls it).

## Observability (`observability/`)
- **CRITICAL/HIGH — XT-3/XT-4 `/api/traces` cross-tenant leak CONFIRMED end-to-end:** `ExecutionTrace` has no `project_id`, `_traces_facet` passes none (facts.py:417). Mirror `FeatureRepository.scoped`. `RunHistoryRecorder` is dead code with latent IDOR.
