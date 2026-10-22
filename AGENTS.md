# Agentic Harness - Agent Rules

## Release Proof Throughput (READ FIRST)

1. During release stabilization, prioritize the active milestone and its required repairs. Do not start unrelated backlog work or pad dispatch waves while release blockers remain.
2. Keep one proof owner and one explicit missing-proof record per milestone task; use [Release Proof Throughput](docs/features/RELEASE_PROOF_THROUGHPUT.md). Distinguish implementation, focused acceptance, source review, full gate, exact-SHA CI, and completion.
3. Before candidate sealing, final combined replay, or a full gate, run `make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0`. Plan mode and passing scheduling admission are NEVER gate or release evidence.
4. Failed capacity/configuration/ownership prerequisites block expensive final proof. Repair the actual prerequisite; a changed HEAD, elapsed time or observer ID does not justify retrying the same failure.
5. Identify blockers by failing owner/node and actual failing input. Preserve failed evidence and name the changed input needed; never add another scanner, runner, daemon or proof cache.
6. Bounded failing-first repairs, required per-commit collection and normal policy-guarded development CI repair pushes remain allowed. Batch known independent CI repairs before another hosted run. Use native ci-failure-status/ci-failure-repair for actual open families before an environment-only handoff; the mandatory Make guide names the repair-owned run-watched route for internal checks. Keep local repair receipts separate from hosted and deployment proof. Hosted and publication admission verify the exact pinned EE manifest, then run the canonical promotion prerequisite through the existing separately labeled90/60 watched owner before the unchanged original90/60 runtime replay and transport. Preserve canonical manifest/runtime/release settings and failure propagation; prerequisite failure starts neither replay nor transport. The mandatory Make guide and process-boundary record name each stage and its proof limits. Failed image availability requires native atomic refresh and independent exact-digest verification, never mutable substitution. The offline publication plan executes none of these checks and grants no proof.
7. Promote cheap deterministic failures into the existing executable admission owner with failing-first routing tests; retain authoritative full-gate replay, coverage, fatal warnings and original assertion intent.
8. After prerequisites clear, freeze one candidate, obtain independent source review, run combined acceptance, the complete canonical gate and hosted CI for that exact SHA. Do not edit it during final proof.
9. Source-only approval and green focus never close a backlog item. Tick TASKS.md only with actual green full-gate evidence and required exact-SHA proofs. Preserve failures and immutable candidate refs.
10. Separate blocker repairs from completed proofs. State missing evidence and exact next action. Never weaken cleanup, ZDD, 85% aggregate/75% per-file coverage or branch/promotion gates for throughput.
11. Do not manually update generated current handoff or observation JSON. Use `make ci-await` or `make pipeline-status`; preserve manual/history evidence as such. Configuration JSON is not a generated observation. Pending/lookup-error/timeout must invalidate prior current success.
12. Hosted timing comes from immutable `make ci-run-summary CI_RUN_SUMMARY_SHARD_TIMINGS=1`, retaining exact run/SHA, all eight shards and censored/unknown intervals. The report is diagnostic-only, never gate or release authority. Measure the exact hosted scheduling SHA before claiming improvement.

## Fast repair loop and compatible candidate batches

Use this sequence with the existing Make owners and release rules above.
The [throughput decision](docs/features/RELEASE_PROOF_THROUGHPUT.md) retains
measurements, rejected approaches and the missing release proofs.

1. Resume from TASKS.md, BUGS.md, config/ratchet.yml and SESSION.md. Use
   work-item/active-work-status and worktree-state for current task, owner,
   branch and source. Consume the existing owner's terminal receipt before
   starting another execution of that work; a yielded session is still active.
2. Prefer a small batch of compatible, already implemented release-required
   changes when their ownership, source and individual criteria are known.
   Two is a conservative pilot, not a measured optimum; one coupled repair is valid. Keep one
   shared-file/Git writer and the existing agent/testing concurrency ceilings.
   Do not add unrelated backlog work to fill a batch.
3. Write and run the narrow failing regression, then repair its actual input.
   Run cheap lint, strict typing and target-contract checks before a broad
   positive replay. Changed Make targets also need their documented behavior;
   preserve expected refusal statuses. For promoted failures, update the native
   manifest, independent seeds/counts and literal rosters together, then drain
   all eight current check-gate-failure-promotions pins (six predecessors plus
   two Make consumers) before the complete routing cohort.
4. Form one deduplicated acceptance union from complete changed test owners and
   affected consumers. Run that union on each required installed runtime after
   the known repairs are ready. Keep each item's criteria/evidence separate.
   Retain mandatory normal collection for every small complete source commit;
   carry its review receipt into the next substantive receiving change when
   possible instead of creating repeated metadata-only proof cycles.
5. Reuse a passed receipt only for its actual unchanged source, tests,
   configuration, interpreter and scope. Preserve old failures and source refs.
   A handoff edit alone does not justify repeating accepted source tests, and an
   old receipt never supplies a new exact-SHA gate, CI or deployment result.
6. After actual prerequisites clear, freeze one candidate for independent
   review, combined acceptance, the complete canonical gate and exact-SHA hosted
   and deployment proofs. A shared green gate can support multiple items only
   when every item's own acceptance criteria also have the required evidence.
7. Use run-watched or the existing observed native owner for long operations,
   with documented variables, visible progress and native terminal status.
   Drain each exact exec session before dependent edits, commits or replacement
   execution. Use make ps for test/audit processes and active-work-status for
   ownership; ps-gludd shows daemons and cannot prove model/audit idleness.
   Keep model-agent names/status separate from OS PIDs.
8. Record the failing owner/input, RED/GREEN counts, runtime/ref, receipt path,
   proof scope and exact next Make action in the owning feature and tracking
   files. Source review, collection, focused acceptance and final proof retain
   distinct authority. Reuse the native owners; no second observer or cache.

### Make results teach the next action

Candidate hosted proof requires the canonical exact-SHA push event. Keep the
existing release-candidate/** push route and all deployment/publication guards;
manual dispatch is operational evidence, not canonical push proof. Use the
existing pipeline-status producer with PIPELINE_STATUS_OUTPUT owned by the same
repo and branch; preserve another branch's record and let the producer refresh
SHA/state. Missing runs and pending stay nonzero. Normal policy-guarded repair
publication is allowed while final proof is blocked; native publication guards
still apply, and source-only approval never clears their refusal.

Keep admission dependencies distinct from escalation dependencies. HumanTodo
service/config/store/auth facts govern filing that request. Actual capacity
clearance permits the existing nine-owner admission even while filing remains
pending; every native owner and final proof still applies. Claim filing only
with its real returned ID. Diagnose the blocked native input instead of adding
an unrelated prerequisite to the release gate. Canonical volume pressure is a
hard limit; generated scratch is a soft cleanup trigger. Native preflight may
admit protected/recent scratch when volume is healthy and inspection/cleanup
have no errors. disk-check is a stricter diagnostic, not a replacement admission
owner. Read native reasons before selecting cleanup; no threshold is changed.

work-item emits advisory stderr prompts from its native result branch while
stdout retains machine-readable JSON. Plan mode does not select focus or check
active-ledger eligibility; prepare-runtime is administrative. Status pointers do
not prove current workers or acceptance. Reconcile potentially applied state after
a refusal, then repair its actual input before retrying the native owner.

Agent-facing target output should include outcome-specific guidance from the
existing result owner. On refusal/failure, identify the actual failed input,
retain its native status/artifacts and name the narrow repair or prerequisite.
On success, state what was measured and the next required owner, including
missing gate/exact-SHA proof. Retained SESSION collection paragraphs name their
fixed source/ref and native receipt; never inherit a changing current header.
The existing first-child SESSION node rejects direct relative claims; source
review still owns semantic attribution. Emit terminal-success guidance only after all
required children finish successfully. Preserve exit codes, original assertions,
fatal warnings, coverage, cleanup and promotion gates. Guidance is advisory
context for the model; native measurements and the established gates remain
authoritative. A zero integration plan validates only its documented inputs;
unchanged ancestry runs no new gate. Repair failed capacity/ownership before
applying, and retain missing exact-SHA proofs after an applied merge. The [result contract](docs/features/MAKE_RESULT_GUIDANCE.md)
and [native merge contract](docs/features/GIT_MERGE_NO_COMMIT.md) retain the
actual output branches, negative controls, decisions and proof limits.
Guarded-target parse refusals teach both quote-values and split-goals repairs.
Keep one guarded target per invocation; correctly quoted values do not permit
intentional extra goals. Use separate Make invocations and drain their native
sessions, retaining the original parse-time status and execution guard.
run-watched reports its native result and directs observed-status to the exact
command/receipt. Check that scope before interpreting success; a first-child
receipt cannot certify later commands outside its watcher. Refusal/failure needs
its actual input/owner repaired and changed evidence before retry. The [watcher record](docs/features/MAKE_RESULT_GUIDANCE.md#watched-command-scope-and-result-prompts)
retains shell/positional semantics, failing-first controls and acceptance limits.
release-proof-admission planned output executes no live prerequisites; blocked
output names the actual failed owner and requires changed input before retry.
Only terminal passed output follows every admission child, and it still grants
no full-gate/exact-SHA proof. Capacity uses existing native lifecycle/HumanTodo
owners; check-mode null, configuration or API refusal never means a filed task.
Preserve a refused request as unfiled until its service prerequisite changes.
HumanTodo check mode performs no write; require the real returned ID and requested
state before claiming filing/resolution. Missing, unusable or mismatched IDs on
HTTP success mean write_outcome=unverified: reconcile before retrying. Read the
existing human-todo-module outcome prompt, retain native errors/status and never
put a raw PSK in JSON/argv or infer credential binding from ambient server auth.
An approved controller binding can be passed explicitly by name through native
Ansible env lookup; reject missing/empty values and retain the [native auth controls](docs/features/MAKE_RESULT_GUIDANCE.md#explicit-controller-credentials).

Before diagnosing pytest timing, verify that the actual test-files consumer
loads the existing trace plugin and records matching protocol pairs. An allocated
path or summary exit0 with no events grants no timing/cause/performance evidence.
Use the native summary's stderr AGENT_NEXT outcome while retaining its unchanged
stdout JSON and native status. Require focused green with the actual recursive
carriers before broad receiving; measure the complete coverage profile and run
remaining complete callers normally. Keep fixture recorders authoritative under
-e, native process-owned basetemps and the logical-Makefile guard intact.
Keep nested trace identities separate and use
pytest-owned fixtures instead of unbounded archive copies. The
[native consumer record](docs/features/XDIST_TRACE_SUMMARY.md#native-consumers-and-result-guidance-s984-2026-10-22-utc)
retains ordinary/-e literal controls, real hosted failures and exact proof limits.

## Make Target Selection Contract

Watched pytest diagnostics opt into the existing native trace with RUN_WATCHED_PYTEST_TRACE=1; mode0 preserves ordinary/inherited behavior. Publication owns mode1 for both watchers, without changing90/60,2/1/1 or proof authority. Native trace_path/run_id and failed artifacts remain producer-owned; no trace/cause/performance claim may be inferred from a different run. Explicit empty RUN_ID/LOG use existing automatic native identities, not a manual fixture. Optional observer arguments preserve CMD's shell positional parameters; retain the native two-mode regression. See the mandatory guide and [publication trace record](docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#retain-a-native-trace-for-publication-admission-2026-10-21).

Before project work, read `make help`, then the mandatory [key Make targets](.agents/policy/key-make-targets.md) before selecting/changing a target. Follow its bounded current-tracking reads and ordered evidence-to-action recipe for release work, including existing-work preservation, complete selected criteria and canonical promotion. Choose the narrowest target. Set every documented target variable explicitly. `config/make_target_contract.json` records each agent-facing target and safe example. After a target change, run `make check-make-target-contract` and its documented behavioral example before claiming it works. Add and test a missing target first. Compatibility cleanup targets delegate to the existing native prune/retirement owner: clean-stale-worktrees and agent-cleanup default to plan, preserve branch refs/locks/checkpoints/active ownership and require explicit literal policy. agent-cleanup requires the exact BRANCH and canonical WORKTREE; a model must never replace a refused native retirement with force removal. For hosted cancellation, that guide and the Pages coverage feature name native annotation/context retrieval, whole-job versus child budgets and required JS0-to-JS1-to-deployment dependencies; a generic cancelled log or legacy retrieval exit0 is not a cause or CI verdict. For runtime attribution, the same guide names test-xdist-trace-summary with explicit LOG, RUN_ID, TRACE_SUMMARY_TIMINGS=1 and TRACE_SUMMARY_MEMORY=0|1; the linked record distinguishes early direct-goal literal protection from native prerequisite/target-specific context and retains the measured GNU Make version. Process-local controller waits do not identify worker CPU, and diagnostic spans provide no gate or CI-repair authority.
Disk's temporary display delegates to the existing tmp-gludd-usage owner for one human-size-sorted largest40 view; it is not exhaustive inventory or deletion authority. A1000-entry JSON cap retains incomplete/nonzero status; choose a known allowlisted narrower root without widening bounds. See [native disk attribution](docs/features/CACHE_RESOURCE_INVENTORY.md#native-temporary-size-attribution-2026-10-21).

The watchdog secrets check uses the existing native observer for the complete process lifecycle; a direct-child timeout alone does not drain descendants. Keep the 60-second scan, 300-second cooldown and current installed interpreter. Legacy/default test fixtures must confine command workspace as well as state and mock the actual adapter seam before an integration call. Preserve exact timeout/signal evidence and unknown-after-interruption semantics. See the [watchdog lifecycle](docs/features/WATCHDOG_SECRETS_LIFECYCLE.md). Historical observer pointers cannot produce owners when the complete native process snapshot contains no stream_command.py command; the cleanup owner preserves gate PID/lock protection in that empty case. A live observer still requires complete metadata and retains both display-cap refusals. Never treat a truncated display, CWD alone or an unverified PID as termination authority.

Full-tree secret checks reuse the existing readonly baseline owner with --scan-cores; upstream scan --baseline can return zero after adding findings. The owner compares its private pre/post inventories through the existing canonical baseline manager's finding_counts, preserves native failures and rejects baseline overrides through pinned native grammar before settings postprocessing. The same namespace refuses native plugin-list, truthy-string and allowlisted-only modes because they return before inventory refresh; empty --string=, regex values and literal filenames keep upstream meaning. Default hook behavior remains unchanged. Recorder fixtures execute the real wrapper and capture actual outer/child argv independently; never invent a transport token to satisfy an old pin. The [security record](docs/security/audit-observability.md#full-scan-refresh-and-finding-admission-2026-10-21) preserves failing-first controls, native source/caller coverage, full-target behaviors and release proof limits.

Coverage scope follows native execution evidence. The shared auditor's imported JSON/XML preserves generic numeric admission but cannot certify E2E execution; only completed owned native shards attach e2e_branch_* fields. Preserve failed native status before reading stale measurements, both85/75 floors and progress/cleanup. Field/context names or an E2E test-file location grant no deployment proof. The [coverage contract](docs/E2E_COVERAGE_AUDIT_CONTRACT.md#imported-reports-and-execution-ownership) names the existing owners and failing-first controls.

The existing coverage-files owner captures and exports private GLUDD_COVERAGE_SOURCE_ROOT from physical pwd -P; use that owner for the required-variable watchdog profile. Canonical source identity remains fixed across child CWD changes, with all canonical files and85/75 BOTH preserved. Altered fixture copies keep distinct source bytes. See [source identity](docs/features/COVERAGE_SOURCE_IDENTITY.md).

Temporary pytest children that retain project configuration with -c must bind --rootdir and --confcutdir to the same physical owned fixture root; assert those values in the actual child while retaining plugins, outcomes and fatal warnings. Lifecycle/observer tests also require native process inspection: preserve /bin/ps or sysctl denial as failed evidence and use the authorized host Make boundary; never substitute an empty inventory or weaken cleanup. See [source identity and fixture scope](docs/features/COVERAGE_SOURCE_IDENTITY.md) and the [native duration comparison](docs/features/INTEGRATION_ADMISSION.md#git-task-handoff-admission-and-confined-duration-fixtures-2026-10-22-utc), which pins the baseline and instrumented children independently.

Cache diagnostics use the bounded cache-resource-inventory owner; cache-disk/disk-user-caches are recipe-free aliases. Its sole shared-UV inventory exception is the fixed /tmp/gludd-uv-cache-public-v2 leaf under the resolved trusted temporary parent, without resolving that leaf into allowlist authority. It grants no generic removal, parent/sibling/descendant access or arbitrary GLUDD_UV_CACHE_DIR admission. Measure that exact root before selecting cleanup; largest40 omission never proves absence or a byte bound. A negligible complete measurement does not justify a prune for capacity. The mandatory guide and owning cache record retain the actual five-child20KiB result and its proof limits. Generic cache removal never owns managed tool caches or proof/temp roots; plan and apply share the same refusal. Its contract's managed-cache example expects exit2; successful plan/apply controls run under native test-files with a confined synthetic HOME. Never create a real cache fixture or retry a refused managed path as apply. Broad cache-clean/clean-gh-run-logs/clean-all-caches entrypoints are retired. See [cache policy](docs/features/CACHE_RESOURCE_INVENTORY.md#native-cache-policy-alignment-2026-10-21).

Native uv-cache-prune uses the existing observer through an installed PYTHON; its four UV_CACHE_PRUNE_* inputs and safe offline mode1 are in the mandatory guide. Keep300s total/heartbeat5/quiet0/retention20, early literal protection plus native target-context binding for direct/dependency/inherited policy, and native nonzero/descendant cleanup. FORCE1 overrides only its process guard, never UV's native lock or ownership. Process inspection failure refuses; a safe plan supplies no cleanup/capacity/release proof. See [the cache record](docs/features/CACHE_RESOURCE_INVENTORY.md#native-uv-prune-status-and-model-contract-2026-10-22-utc).

Scratch permission repair uses native os.fwalk directory descriptors, fchmod and the public symlink-resistant shutil.rmtree capability. Preserve capability/traversal refusals and the existing ownership, age, PID/socket, lease and fresh-identity checks. Confined tests retain target bytes and modes across nested links and native open/chmod races. The [native scratch record](docs/features/CACHE_RESOURCE_INVENTORY.md#scratch-permission-repair-through-owned-directory-descriptors-2026-10-21) names the actual regression, standard-library decision and installed-runtime proof.

Legacy automatic reap/direct force-unlock recipes are retired; historical presence audits and Make -n/-q are not cleanup authority. For selected-owner cleanup, the mandatory guide and [resource lifecycle record](docs/features/GATE_RESOURCE_LIFECYCLE.md#native-alias-plan-and-apply-must-bind-the-same-request-2026-10-20) distinguish native canonical identity, completed logical lease release, valid registered-owner plan/apply controls and both mismatched-mode refusals. Preserve locks, ignored evidence, refs and checkpoints; measure capacity after actual retirement. An absent-path refusal or recorder-only check supplies no valid-plan proof.
For native CLI process reuse, the [runtime boundary record](docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md) names the existing public ledger/pipeline owners, complete-source coverage recipe, command/stream/final-reporting failure boundaries and stale-success invalidation. Both native observations remain fresh. An admitted new repair attempt invalidates older current repair authority before evidence; running/failed/timed-out/interrupted/postcheck-failed evidence cannot reuse an ancestral receipt. Preserve old success as history and unrelated/concurrent evidence through the existing locked atomic ledger owner; invalid/plan-only requests stay nonmutating, and only real successful exact clean-source evidence with the final freshness fence restores authority. The mandatory Make guide and process-boundary record retain the actual stale-receipt incident; never manually edit current JSON. Local diagnostic timings, source approval, receiving acceptance and the original bounded runtime-family proof retain their distinct authority.

For publication history, that guide and the [dated publication record](docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#reviewed-source-blocked-at-publication-deadline-2026-10-21) distinguish an observed failed verdict from green CI. Native pipeline-status can record the exact previous published verdict; inspect its marker before another verdict call. Terminal enrichment can change a failure fingerprint and require fresh repair binding. Earlier local original-budget passes or a faster separate main-only diagnostic cannot clear a later actual publication timeout. Preserve its receipt and change the actual failing input before replay; documentation-only HEAD changes do not satisfy that condition.

For failure-preserving diagnostics, the same mandatory guide links the existing native listing and CPython measurement owners, exact complete coverage example and canonical export compatibility controls. Preserve native child failures and missing-data refusals, explicit fixture-owned LOG paths and the distinction between local timing and original-runtime/hosted proof. Its published-candidate decision retains actual hosted timeouts, explicit proposal rejection and the real UV supervisor boundary; local paired measurements never replace original-runtime or exact-SHA proof.

Emergency ci-cancel defaults to an offline single-RUN plan; its documented explicit apply preserves native errors and requests cancellation without claiming a terminal result. The legacy bulk queued-run cancellation target is retired. Keep its raw input capture before native startup shell expansion, as documented in the mandatory Make guide and [cancellation record](docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#explicit-cancellation-with-literal-inputs-2026-10-21).

Release-proof mutation probes retain the real production binding before adding fixture defaults. The current helper rejects missing/duplicate declarations before fixture creation or a child, while opposite-binding controls still exercise native Make. The mandatory guide and [hosted assertion record](docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#published-unit-3a-assertion-repair-2026-10-21) retain the actual masking failure, original assertions and receiving evidence.

Workflow-level queued can include active jobs. The existing pipeline-status snapshot exposes observed job-state counts and bounded active/failed step progress from its already validated details; complete JSON retains omitted display rows. Keep this diagnostic view separate from terminal workflow, exact-SHA and release authority. The mandatory Make guide and [job-progress record](docs/features/PIPELINE_STATUS_PROCESS_BOUNDARY.md#observed-job-progress-beside-workflow-status-2026-10-21) retain the repeated interpretation error, bounded-output controls and practitioner context.

Literal text edits use write-text-b64/replace-text-b64 through the existing native text owner: strict base64, UTF-8 without NUL, and validation before destination IO; retain the nonempty Make inputs and exact-one replacement rule. The existing native decoder owns this validation. functions.exec is transport only: project workflows and analysis MUST run through named Make owners with documented variables and native argv/status/artifacts. No project verdict may come from ad hoc JavaScript/Python in a tool wrapper or hidden function stores; literal editing/encoding/transport is allowed. Reuse pipeline-status/ci-await for current JSON, work-item/active-work-status and the workstream registry for ownership; never add a second observer/cache. A versioned takeover must name branch/ref/task, source versus acceptance/gate/CI states, blocker and exact next Make action. Native producers own generated current observations.
Native Git diagnostic ancestry uses the documented git-is-ancestor owner with literal A/B, retained exit=N/ErrorN and a nonzero Make result for native non-ancestry/errors. Patch equivalence and genuine ancestry remain distinct; the mandatory guide links native failing-first controls and preserves authenticated cleanup/release authority.
Active-work claims need evidence: `make ps` covers Python test/audit PIDs; `make ps-gludd` projects the existing native project-owned inventory to watchdog/daemon classifications and proves no agent/audit idleness; detached PPID1 alone grants no cleanup authority. Report model-agent names/status separately; never invent PIDs. `make active-work-status` provides workstreams, parent/child PIDs, gate/hash/tasks; `agent_pids: false` is intentional.

Task identity uses the existing canonical numeric-dot or uppercase-hyphenated grammar. Keep unsupported historical labels as explicit aliases after unique canonical primary IDs, with statuses and evidence preserved. Native validate-task-ledger rejects duplicate checked claims too; its complete existing early test owner guards current real-data uniqueness. The [task-source record](docs/features/BACKLOG_AUDIT_TASK_SOURCES.md#canonical-identity-and-duplicate-checked-claims-2026-10-22-utc) distinguishes valid current metadata, immutable historical unknowns and required final release proof.

## NO-PROMPT PROGRESS DIRECTIVE (READ FIRST)

When task-level permission exists, do not call tools or paths that trigger approval prompts. If a route prompts, abandon that path immediately, use or add a make target, and keep working without asking unless required facts make progress impossible.

## ⛔ ANTI-LOOP DIRECTIVE (READ FIRST)

NEVER run `make git-log`, `make ci-verdict` or `make git-diff` as a standalone single tool call. Break compulsive checks with useful Task work. Independent multi-step work warrants useful delegation; one-read/one-edit work stays inline.
The floor plugin blocks those checks with open work; configured nonzero floors additionally enforce streak/thin-wave rules. Delegate/mainthread counters default to2 (third non-dispatch call hard-denied); text.complete nags after2; agent_watchdog resets streak every60s. These runtime descriptions do not override the zero-floor cost-efficiency precedence below.

## ⛔ COST-EFFICIENCY DIRECTIVE (READ FIRST — OVERRIDES ALL FLOOR RULES BELOW)

Maximize productivity per token. Fewer, better subagents beat more, wasteful ones. Inline work preferred for simple tasks. Max 3 subagents per wave; values above 3 are clamped. This directive OVERRIDES all higher mandatory-floor rules below: default adaptive minimum0, hard maximum3, operator floor0..3; historical floors cannot raise them.
Caps:3 concurrent subagents/wave; minimal context;10 actual parallel model-calling HTTP processes; serialized research (at most1); coding/testing≤2 in parallel, disjoint files, isolated worktrees and sequential merges.
Before dispatch count fixes/enhancements, check completed/dispatched ownership and deduplicate the task-spec hash. Prompts≤20 lines must name available tools, Make-only shell restriction, relevant Make targets/scripts, search parameters (path=directory/include=file pattern/pattern=regex), exact objective and “return ≤5 bullet points” or equivalent. Agents read necessary files but return terse summaries/file pointers, never dumps.
Use inline reads/grep/glob for single-file/search work; never dispatch a single grep/status check. Deepseek prefers direct tools over nesting. Dispatch only multi-step synthesis/production that benefits from ownership; never repeat completed work, pad a floor or occupy slots with status.
WRITE automated checkers, don't WALK the codebase: reuse existing native/lint/type/dead-code/coverage/security checkers; create a reusable checker instead of serial manual audits. Before common parsing/large-data/infrastructure code, research existing libraries/modules/CLIs/Ansible collections FIRST through a separate research check (PyPI, brew, ansible-galaxy as applicable). Mature OSS reuse is mandatory; custom application business logic without a standard library is the exception.

### CRITICAL: Enhancement/Fix Dispatch Ratio

At least50% of every useful dispatch wave must be enhancements (new tests/features/docs/tooling/scripts/self-tests/Make targets/guardrails/refactors/observability). In a3-agent wave at least2 are enhancements; fix-only waves are forbidden while Phase D/E/F work remains. The original also requires: “Every single dispatch message must include at least 2-3 enhancement subagents.” Retain that tension with the per-wave ratio, adaptive0..3 and no-filler scopes; do not silently discard it. Ratio is per-wave, not session; “fix first” gets the first slot, remaining useful slots preserve the ratio. Source language says this overrides conflicting priority language; the release-only/no-filler rules above also remain binding under their own triggers.
The enhancement plugin classifies unknown as fix; checks waves≥2 at text.complete, warns/blocks through its hooks; `/tmp/gludd-enhancement-ratio.json` records wave/session data. `make check-enhancement-ratio` is read-only and exits1 on violation. `OPENCODE_SUBAGENT=1` skips enforcement; `GLUDD_ENHANCEMENT_RATIO_ENFORCE=0` disables it.

### Branch discipline (HARD GATE)

NEVER push fix-forward waves straight to `master`; use a `release-candidate/*` branch with exact-SHA CI green and the existing promotion gates.
NEVER push feature work directly to master. Master is for merges from development ONLY, or emergency pipeline fixes. Features land on development/feature branches; shared master/development are never rebased, only merge-forward.
NEVER merge to master from inside a worktree. Before merging development→master: development gate green, exact CI green, then `make release-promote`. `make batch-push` pushes CURRENT branch. Verify which branch you're on with `make verify-state` before pushing. Emergency fixes on master get backported IMMEDIATELY before other feature work.

## CRITICAL: Subagent Task Design — Fix, Don't Check

Every subagent MUST produce a concrete fix or deliverable that persists: committed change, test, config, proposal/decision or other concrete artifact; findings/status alone fail. End EVERY prompt: “Do NOT just report problems. Fix them.”
Forbidden status tasks: “check CI status”, “check if CI is green”, “ci-await”, “make ci-await”, “audit lint”, “run lint and report”, “check for type errors”, “audit typecheck”, “check dirty tree”, “check git status”, “scan for dead code”, “find unused imports”, “review and report”, “read and summarize”, “survey test coverage”, “list uncovered files”, “check for secrets”, “audit for vulnerabilities”, “poll until”, “wait for”, “watch for”. Assign the fix, not the check; do simple checks inline.
Exception: specific unanswered research with a concrete recommendation/comparison/architecture deliverable is valid; a single read/report is not research. Never pad a wave. Tests: test_subagent_fix_not_check.py and quality/deliverable integration owners.

## CRITICAL: Single-Source Feature Development

Each feature, Make target, config or shared-infrastructure change lands on exactly ONE branch first, then merges/cherry-picks elsewhere. Features land on development first; emergency master fixes are immediately backported.
Shared Makefile/opencode.json/AGENTS.md/settings/config/workflows are single-writer: record the branch; the orchestrator must not assign the same file on another branch. No parallel Makefile edits on different branches. `make check-duplicate-targets` is a hard gate; scripts/check_duplicate_targets.py parses column-zero target declarations.

## Mechanical Contract (READ FIRST — numbered priority)

Make-only execution; useful tool continuation with pending work; completion only with green gate+TASKS evidence; test-first and zero collection errors before every commit; fix discovered gaps now; trust actual gate exit/output rather than SESSION assertions; read TASKS for work and BUGS before completion, and update both as you go; mature-tool reuse; visible long operations; bounded tool-unavailability adaptation; restricted file access; local commits/batched pushes; fail-closed required CI.
Never use `continue-on-error` to weaken required validation. A diagnostic may collect independent failures only if every result is retained, a complete summary is published and the enclosing required job fails for any required failure.

## CRITICAL: No External File Access

Read/Write/Edit/Glob/Grep (including path arguments and subagents) may access exactly five prefixes: /Users/shawnwilson/gludd/**, /tmp/**, /Users/shawnwilson/.config/opencode/**, /Users/shawnwilson/.local/share/opencode/** and /Users/shawnwilson/.cache/**. NEVER ask for access to the user's full home directory ever again. No “just reading” exception for ~/.ssh, ~/.aws, ~/.gnupg, Documents/Desktop/Library, /etc or another repo.
If outside data is needed, find workspace data, add a Make owner to expose it, or name the specific needed path in≤2 lines and continue other work; never fire a prompting external tool call. opencode.json uses last-match-wins allow rules and catch-all deny. The older mechanical summary/structural test mention three prefixes; this exhaustive five-prefix clause remains the source's explicit policy, not a grant beyond session/tool permissions. Structural enforcement: `tests/unit/test_no_home_directory_access.py` pins both global and build-agent permission scopes.

## ⛔ PRE-GENERATION CONTRACT (READ BEFORE GENERATING ANY TEXT)

Before ANY text: check TASKS unchecked work and config/ratchet.yml entries. Pending work requires a useful tool call in EVERY response. Delete a text-only summary and replace it with action.
On results, validate/codify by files/tests/lint/typecheck or assign another useful independent deliverable. Never stop with a summary table, “All done”, “Ready for review”, “Everything is complete”, “Shall I continue?”, “What should I do next?”, remaining-work list or pending-work acknowledgment without action.
Enforced by prompt, enforce-stop.ts blanking/HARD STOP and agent_watchdog CONTINUE. Disengage never bypasses the fundamental hasRealPendingWork latch.

## CRITICAL: Session Start Protocol (FIRST action of every session)

Source startup duties retain their scopes: `make clean-tmp` before session; `make watchdog-auto` step0 (10s poll/idempotent); then ONE parallel message reading TASKS.md, BUGS.md, config/ratchet.yml, SESSION.md plus `make git-status` and `make git-log`; then useful inline work or0..3 independent owners.
No elapsed-time dispatch quota or status-only turn between evidence and work. One-word/one-line factual question or “don't multitask yet” permits brief answer then dispatch under the source exception. Prose-first Q&A with backlog is forbidden.
The source also requires push-first before backlog reads, prior-session audit before work and four-file FIRST-message assessment below. These ordering statements are preserved, not silently reconciled; explicit session instructions and the Make/release guards still apply.

## CRITICAL: Bash Tool Unavailability — 4-Step Diagnosis (MAX 4 TURNS)

Adapt in≤2 turns under the mechanical summary; the detailed diagnosis permits MAX4 turns, never10+.
In ONE parallel turn check tool list, agent-tool config, SESSION known banner and opencode.json last-match permissions. Layers: agent config controls visibility, project permission gates available tools, prompt exposes the result. Missing tool is not automatically a provider limitation.
Next turn classify: absent from tool list→agent config gap (fix if allowed, otherwise name exact config/tool in≤2 lines); present but denied→permission ordering (`*: deny` FIRST, `make *: allow` SECOND); correct config but not-found/timeout→provider limitation, adapt with available read/edit/write/grep/glob and update SESSION. Retry after the actual config fix.
No uppercase Bash calls, speculative tool reconfiguration loops, subagents falsely promised Bash, text-only problem explanations or blocking restart requests. The source's grind recovery says `make disengage-enforcement` before other action, repair plugin, `make write-plugin-manifest`, restart; preserve the narrower disengage limits below.

## Completion = Green Gate + TASKS.md Evidence

Complete only with `make gate` green (lint0/typecheck≤baseline/collection0/tests pass), `make check-node-v26-compat`, TASKS tick+gate target/summary/commit hash and `make test-count` collection0. Paste gate evidence for every tick; the evidence requirement is proactive, not falsely described as mechanically checked at response time.
If test-failures/gate output masks collection ERRORs or disagrees with full `make test`, full output is truth; repair the reporting owner first. Empty todowrite never overrides real pending work.

## CRITICAL: "Done" Claims Require Observable Verification Evidence

Done/landed/shipped/deployed/released/resolved/fixed/working/complete/successful/✅ requires cited machine MEASUREMENT in the SAME message, not authorship or stale evidence.
Unit fix: named test+`make test TESTFILE=...` count. Gate: full results+.gate-status PASS. Commit: `make git-log` hash+gate evidence. Push: `make verify-remote BRANCH=<b> SHA=<sha>`→VERIFIED branch@sha. CI: `make ci-verdict BRANCH=<b>` success+headSha==tip. Release: completeness PASS, non-draft release and download URLs. Respect anti-loop/cooldown rules when obtaining evidence.
False-done shell/opencode/verified-claims hooks enforce this; stale CI or gate older than edit is false evidence. `GLUDD_FALSE_DONE_ENFORCE=1`; verified-claims is fail-open and `GLUDD_VERIFIED_CLAIMS_ENFORCE=0` disables.

## No Unseen Events (observability invariant)

“If an event happens and no one can see it, it is not an event.” Operations longer than a few seconds must stream output, phase markers or timestamped heartbeat EVERY poll/wait cycle; never only /dev/null or a buffered file. Background work stays visible. Print failure logs/causes. Daemon jobs publish queue/metrics/structured logs visible through /api/facts.
Every response must have visible UI output: one-line progress between tools/planning, streamed long work or a reporting owner, and blocker+attempted workaround instead of blocking options. Tests/unit/test_observability_guardrails.py and [[gludd-observability-invariant]] cover this.

## CRITICAL: Pre-Response Stop Audit (READ BEFORE EVERY RESPONSE)

Check todowrite pending/in_progress AND real signals before every text response; config/ratchet.yml must be checked before EVERY text response. Only all completed/cancelled permits a terminal text-only response. Status summaries remain blocked with pending work even if they contain truthful hashes/counts: evidence does not permit stopping. STATUS_SUMMARY_RE detects bold-header tables/bullets and named status-summary phrases.

## CRITICAL: Session-Start Orchestration Contract

FIRST action finds tracked work and assesses ownership, no prose first. FIRST tool message reads TASKS/BUGS/ratchet/SESSION concurrently; SECOND identifies useful owners0..3 or coupled inline work. No serial backlog reads, long target before assessment or text-only state preamble.
Default-on enforce-session-start.ts mutation gate requires at least one tracking file read; GLUDD_SESSION_START_ENFORCE=0 is only for focused single-file/Q&A wedging. A single specific factual question with no implied continuation permits brief answer/proceed; backlog or continue/resume/keep-working triggers assessment.
The runtime reference also records dispatch-now60s/hard-deny120s configurable gates and reset on first dispatch; no-quota/adaptive wording is retained. Crash recovery resets on real nonzero PID mismatch or state age>300s, uses PID-unique atomic temp rename, and `make crash-recovery` resets manually.

## CRITICAL: Continuous Multitasking Enforcement (During-Run)

Hard ceiling of three, active floor0 unless operator opt-in; focused inline work is valid at any streak length. Configured floors apply to the whole dispatch message and clamp at3; no read/status filler. Before and after each tool message count dispatches: batch2–3 useful disjoint owners where helpful; one is valid for one useful task; zero is valid for coupled work. The source post-audit zero-streak check applies only to an explicitly configured positive floor.

## CRITICAL: Priority Stacking (AND not OR)

New directives apply ADDITIVELY, AND not OR, never SUBSTITUTIVELY: preserve existing objectives. New priority: first dispatch, first check on return, First follow-up; Multitasking preserved in remaining useful slots within3. Never infer “pause everything else”, “previous priorities are void” or “blocking all subagent dispatch”; never drop coding for research-only waves or pad a wave. One owner is correct for coupled work.
| User says | WRONG interpretation | RIGHT interpretation |
|---|---|---|
| “guardrails NOW” | drop other objectives | first dispatch + first check + first follow-up, keep safe independent owners |
| “don't wait on CI” | stop CI-related work | avoid blocking, continue pushes/fixes, check natural breaks |
| “do X immediately” | pause everything else | X first; preserve disjoint existing objectives |
| “fix the disk space” / “audit my prompts” | forget release/coding | first owner; at most two useful disjoint others |
Tests: tests/unit/test_priority_stacking_rule.py. The explicit “first/before” rule below still applies; neither original scope is silently discarded.

## CRITICAL: Instruction-Following Priority

Specific user instructions contradicting the plan apply IMMEDIATELY before anything else. “fix FIRST/before continuing” means do not concurrently continue other work/new features; “address and continue” means both; “codify a process” means codify now. Never substitute status tables/gap lists/questions for implementation. Status answer≤1–2 lines then tool; after commit start next pending work.

## CRITICAL: Premature-Stop Audit Policy

At EVERY session start, before other work: read BUGS, audit prior SESSION/Next Steps for previously identified unfinished work, repair the root-cause guardrail and log date, unfinished work, guard failure and fix in BUGS.
Pending todos, old Next Steps, status instead of action, asking to continue or listing work and stopping are bugs. Check absent/weak/advisory guards, missing pattern/prompt and missing boundary tracking. Stop enforcement checks exact-tip CI, release completeness, gate and TASKS, not merely todowrite.

## CRITICAL: Mechanical Stop Prevention (10-Signal Binary Latch)

Any ONE means pending: tasksMdUnchecked; tasksMdUnverified (tick lacks hash/test/CI); ratchetEntries; gateStatusRed; gateLiteTestFailed; ciVerdictPendingOrRed; ciNeverRunOnHead; releaseIncomplete; pushBlocked; uncommittedChanges.
All text-only responses, post-result summaries and post-ship text are blocked; configured-underfloor waves block. Text-only is allowed only with ALL10 false. The older “responses with >=3 dispatches” allowance is subject to the explicit cost/adaptive override.
Owners: hasRealPendingWork/enforce-stop, enforce-multitask, enforce-delegate, /tmp/gludd-push-state.json and test_enforce_stop_behavior.py.

## CRITICAL: Task Completion Policy

No victory laps. After completing one objective, immediately start the next. Complete ALL requested work through EVERY requested step or reach a genuine unfixable blocker (credentials/payment/environment). Do NOT stop early to report status or ask to continue. Do NOT get sidetracked; refocus immediately on the requested work. Never ask to continue, treat infrastructure as the real deliverable, take victory laps, stop after a plan/audit, drop returned results, report “next steps” instead of doing them, or hide a stop in a Q&A recap. Low priority sets ORDER, not omission; terminal task states are completed/cancelled.
A gap found while working is owned: fix/test/commit and resume original work, except unavailable credentials/payment/environment. Inline useful work is valid; replacement dispatch is optional. Keep all objectives; status≤brief answer then resume immediately.

## CRITICAL: Q&A Response Pattern — Answer THEN Continue

**When the user asks a factual question ("have you implemented all specs?", "what did you do?", "what happened since the crash?"), the answer MUST include a tool call that continues work — never just the answer alone.**
Factual questions get a factual1–3-line answer, then an IMMEDIATE useful tool call in the same response while TASKS/ratchet/BUGS/gate/CI work remains: dispatch OR Make is allowed under the source rule.
Forbidden terminal recaps include “completed in this session”, “was done since the crash”, “everything committed and merged”, “Here's what was done/completed/changed”, “Summary of what was done”, and bold “What changed/What's left/What was done?” headers.
The original also says: “This is a forbidden Q&A summary without a tool call. The correct response includes the same answer PLUS an immediate dispatch.” That stronger wording is retained alongside dispatch-OR-Make; no silent conflict resolution. Exact original correct/wrong examples remain [source774–799](docs/AGENT_POLICY_V1_SOURCE_3583000.md#correct-response-pattern).
QA_RESPONSE_PATTERNS/text.complete blocks such pending-work recaps without tools; test_stop_pattern_qa.py pins it.

## CRITICAL: No-Manual-Default Policy

Processes must be fully automated: Make/daemon-triggered, safe working configuration defaults with no user-created file requirement, required binaries/resources prefetched during `make dist` rather than first-use download. Every src class must be importable/instantiatable from daemon startup even if calls are lazy. Verify/download/health owners must act/remedy, not merely report absent work; `make bundle-binaries` must bundle. Manual default is a bug; preflight completion_audit checks wiring.

## CRITICAL: Nothing-Dropped Guardrail

Before terminal response every delegated result is committed, marked completed in todowrite OR explicitly cancelled with reason. The stricter source restatement requires committed/cancelled before calling a result done; returning/reading it is not completion.
GLUDD_TODO_GUARD_ENFORCE defaultON denies commit-shaped targets with pending todos absent from MSG or staged TASKS; complete/cancel/stage references first. session.idle injects CONTINUE. GLUDD_TODO_GUARD_ENFORCE=0 is focused-single-file advisory only; GLUDD_TODO_GUARD_BYPASS=1 is documented single-emergency-commit escape, never default. enforce-stop/test_todo_guard_plugin own it.

## CRITICAL: Human Permission Subjects + Intersection Policy

Humans carry PermissionSpec: human-admin/operator/viewer YAML; daemon default_human_role=human-operator unless override. effective_spec=intersection(human_spec,agent_spec,requested_spec): narrowest prefix, host intersection, deny union, minimum TTL; never broadens an entity's spec.
POST /admin/perm/escalation-request requires≥3 distinct alternatives_tried {approach,outcome}; fewer→422. Within human∩agent auto-approves; outside→pending HumanTodo category=permission_escalation. `gludd perm escalations {approve|deny}` resolves; approval STS=(current+requested)∩human_spec. test_permission_intersection.py covers it.

## CRITICAL: Human Todo System (bot→human task requests)

Use HumanTodo, NOT logs/event errors/agent todos, for escalation, external actions, decisions, input or generic human blockers. File via general_ludd.agent.gludd_human_todo or POST /api/human-todos. Make-only sessions use human-todo-module with explicit HUMAN_TODO_ARGS_JSON and HUMAN_TODO_VALIDATE_ONLY: literal1 validates, literal0 applies through the same module; changed=true/id=null is a plan, not a filed request. Preserve a native API failure and never invent an ID when the daemon is unavailable.
parent_agent_todo_id makes parent blocked_on_human/non-runnable; done→pending with human_resolution injected as human_input; dismissed→cancelled OR requeued with reason. CLI `gludd human-todo {list|show|done|dismiss|in-progress|comment|watch|stats}`. Keep separate from TodoModel/event/audit logs.

## CRITICAL: Don't Block Projects on Stalled Tasks

Every blocked task older than its threshold triggers an action (dispatch_agent/schedule_retry/file_human_todo), so no project silently stalls. Hourly/on-demand remediation: BlockerDetector.scan past thresholds emits suggested schedule_retry/file_human_todo/dispatch_agent/no_action (dispatcher may override). Defaults: permission4h, human/generic24h, run_count>3 chronic→resource_contention, stale human-todo→reminder, retry delay4h.
chronic_blockers groups recent BLOCKED_ON_HUMAN by(task_type,blocker_kind), default5 incidents/7days. Operators review `gludd remediation chronic-blockers` weekly and fix systemic causes; tune RemediationConfig through config/remediation.yml/env: tune up if blockers surface too late, down if noisy. Preserve this original direction alongside suggested no_action/dispatcher overrides, without silently correcting its tension. Native detector/dispatcher/reporter and scheduler tests enforce.

## CRITICAL: Project-Collection Precedence Contract

Ansible precedence PROJECT <project>/.gludd/collections → USER ${XDG_CONFIG_HOME:-~/.config}/gludd/collections → BUNDLED <install_root>/collections; higher-tier FQCN shadows lower. Example override: .gludd/collections/ansible_collections/general_ludd/agent/roles/project_init/.
`gludd project init` uses the general_ludd.agent.project_init role, NOT Python; `gludd project paths` prints resolution. At daemon startup/project switch AnsibleRunnerAdapter sets ANSIBLE_COLLECTIONS_PATH and ANSIBLE_ROLES_PATH through ansible/paths.py; bundled ansible.cfg is fallback. Roles remain directly callable via FQCN without registration.

## Meta-Rule: Guardrail Policy

Every NEW restriction needs ALL3: opencode.json config permission hard gate, runtime .opencode/plugin hook with contextual guidance, prominent AGENTS prompt rule. Add missing layers before continuing; use guardrail-pattern skill. Repeated improvements also require codification below.

## CRITICAL: Guardrail Integrity Policy

NEVER remove/disable/weaken a guardrail to fix noise, errors or friction. Trace root cause to make the guardrail smarter, narrow false-positive conditions, keep block/throw for real violations, run guardrail tests. Error→empty block, leaked message→deletion, failing test→weaker assertion are regressions. enforce-make and test_guardrails.py cover it.

## CRITICAL: No Lint-Suppression Comments

Forbidden in src/tests: # noqa, # type: ignore, # pylint: disable/skip-file, # fmt: off/skip/on, # isort:skip. Fix code; no “just once” exception. Reflow/extract, add and narrow object types (never Any), remove unused imports; unfixable third-party attributes use typed getattr wrapper or correct cast.
Only data-pattern allowlist: src/general_ludd/security/fix_not_disable.py and tests/unit/test_type_safety_guardrails.py. Do not add paths; narrow matcher. Runtime hook: `.opencode/plugin/enforce-no-suppressions.ts`, clean deny JSON+exit0, fail-open on exception. Behavior pin: `tests/unit/test_no_suppression_comments_plugin.py` verifies SUPPRESSION_PATTERNS/ALLOWLIST_PATHS. Repo-wide scan: assert violations, never warnings.warn. Apply Guardrail Integrity Policy above.

## Opencode Plugin Ports (Claude Hook Equivalents)

Claude .claude/hooks and both .opencode/plugin and .opencode/plugins layers enforce the same policies in parallel, registered by default with shared knobs. Exact dated port/status tables and incident context remain in the versioned source; current roster comes from `make list-plugins`, not those dated counts.
Before ANY tool call know active plugins/blocks; before plugin/enforcement work read the mandatory [enforcement plugin reference](.agents/policy/enforcement-plugins.md). Preserve its restart, verification and fail-closed requirements. Legacy >=3 floors there are overridden by the explicit cost-efficiency cap/adaptive minimum.

### CRITICAL: Enforcement Plugin Changes Require Restart

After ANY .opencode/plugin/*.ts edit, inform the user restart is required; continue with existing behavior until opencode restarts. Compiled /tmp hot modules are loaded once at startup, not a runtime plugin-loader API. After restart, attempt a text-only response to verify enforcement; if it succeeds, investigate.
Hot reload via shared.ts/fallback does not remove restart duty. Subagents: OPENCODE_SUBAGENT=1 early guards in EVERY exported hook, with /tmp/gludd-subagent-${process.pid}.json fallback; stale/broken hot modules use guarded defaultImpl. Verify via test-hook-runtime and verify-plugin-manifest; repair leak with hot-reload-plugins/reload-enforcement/verify-enforcement. Missing env+fallback is a known framework gap.
State tuning: `make reload-enforcement` resets re-read state; floor-override/tool-streak/watchdog-disengage/enhancement-ratio/task-deadlines/session-start files are reread, not init-cached. `make disengage-enforcement` skips only heuristic completion/QA checks, NEVER hasRealPendingWork. Fundamental pending latch stays active. Floor override0..3 only.
Historical evidence regex requires a hex letter: \b[0-9a-f]*[a-f][0-9a-f]{6,39}\b, not pure digits; task reads accept top-level path and tool_input?.path. Plugin source changes still require restart.

## CRITICAL: Node v26 `--experimental-strip-types` Compatibility (Plugin Code)

ALL files under both plugin directories must parse under native Node26 stripping. Forbid nested try immediately inside catch, catch parameter type annotations, enums/namespaces/export=/import=. Use flattened/helper recovery, bare catch, untyped catch+typeof/instanceof, ES modules; interfaces/type aliases/satisfies/as erase.
`make check-node-v26-compat` (scripts/check_node_v26_compat.py) is a gate; one parse error can prevent ALL plugins loading. Structural compatibility is not runtime hook proof.

## CRITICAL: "Fix" Means Repair, Never Disable

A user-requested fix must restore intended WORKING behavior, never remove/downgrade/stub/comment/disable/weaken it. Only explicit “disable/turn off/make advisory” authorizes that behavior change. Preserve purpose, introduce no new failure, prove actual behavior; do not merely stop errors. Clean blocking uses block JSON+exit0 rather than crashing exit1. Never weaken/delete/xfail a failing assertion to claim repair.

## CRITICAL: Release Cut = Update the README Status Table

Every release goes through `make release-cut TAG='...' MSG='...'`; direct push+tag bypasses README currency. Before tag push refresh every changed Feature & Task Completion Status row and “Status as of v<new> — <date>”; commit README with pyproject.toml/src version/CHANGELOG bump.
check_readme_status_current.py accepts TAG with optional v; `make check-readme-status [TAG='...']` checks. release-cut aborts on first failure: CI-green step0, check-readme-status, git-push-sandboxcom, git-tag-push, release-view. Original descriptions of step counts/polling remain historical, not permission to bypass the actual owner.

## CRITICAL: Release Branch Lifecycle — Green Branches Are Immutable

`make release-branch-new NAME=release/<version>` is the only branch-cut owner; default master base must be CI-GREEN. A green remote release tip is frozen: no added commits via git-push-branch/-nv; _push-green-guard exit0allow/1block/2inconclusive-fail-open is the original runtime contract.
If the release branch goes red, fix-forward on THAT branch, push and obtain green; never dodge by parallel branch/force-push. `make release-promote TAG=<tag>` alone ships: exact-tip CI green, local==remote, annotated tag+push BEFORE ff-only master merge/push, then verify master remote SHA. `make release-recut TAG=<tag>` retriggers existing known-good tag Build/Release failures (delete+repush), not arbitrary code bypass.

## CRITICAL: Release Candidate Discipline — Promote the Green Commit

1. **Green HEAD → promote immediately.** When `make ci-verdict BRANCH=development` reports success and headSha equals development tip, next start or confirm `make test-ci-dual-track-local-bg` has produced an attestation for that SHA, then `make release-promote TAG=<tag>`. Do NOT add new commits, unrelated feature branches, a long unrelated gate or a status-only pause first.
2. **Start local dual-track attestation right after pushing.** Immediately run `make test-ci-dual-track-local-bg` in parallel with hosted CI; inspect `make test-ci-dual-track-local-status` only at natural breaks, never main-thread polling.
3. **Never re-run a full test suite on the same SHA.** Use default `DUAL_TRACK_RESUME=1` incremental resume and `make gate-resume` for partial/interrupted work; it requires FAIL+prior gate log. Dirty-tree fallback legacy resume.json can skip old batches but is not exact-SHA authenticated admission.
4. **While CI is pending, only do release-advancing actions.** README/status/tag hygiene, notes/version bumps or already-surfaced CI fixes only. New features/refactors/unrelateddocs/status-only polling are forbidden; side work is a stop pattern.
5. **No text-only responses or premature stops while a release is pending.** Tag/CI/attestation/promotion in flight requires a release-advancing tool every response. No summary, “waiting for CI” or “what's next?” stop. Full proof prerequisites above still apply. Structural owner: tests/unit/test_release_candidate_discipline.py.

## CRITICAL: Agent At-Rest / Re-Dispatch Policy

Classify STATUS/result, not rest: completed+deliverable→accept, never repeat; completed but partial/wrong→SendMessage specific gap (or redispatch stale context); failed/stalled/died→retry with backoff;529/429/503→backoff exponential if repeated.
Inspect actual exit/result: a completed background turn may have been killed; gate status/pytest summary is truth. Requeue failed/stalled WORK only, bounded retries+backoff (optional watcher example cap3), never completed tasks. NEVER self-relaunch a long-task watcher. Main loop owns long work through run_in_background, re-invoked exactly once on exit; no subagent rest-and-relaunch/ZOMBIE loop.

## CRITICAL: Never Block on Questions — Default to Action

Choose reasonable option, state one-line assumption and proceed; surface options alongside work, never as gate. AskUserQuestion guard is clean deny/fail-open. Rare unapproved destructive/irreversible external action: state plan/risk and take safe default or note/continue; existing authorization suffices. This does not override actual tool/session approval boundaries.

## CRITICAL: Bash Command Policy

ONLY `make <target>`; never direct uv/python/pip/git/which/ls/cat/find/rm/cp/mv/rg/tail/head/command/export/source. Shell | ; && || () $() backticks redirects >/< /2>&1 braces ! and backslash escape are forbidden in agent shell commands. Make targets may use them internally. No cd+make, piped test/tail, make-test;make-lint, ||true or bare venv Python; create/test an owner instead.

## CRITICAL: TDD Policy

Identify behavior→Write a test FIRST that fails for missing behavior→`make test-unit` RED→minimal implementation→same GREEN→refactor green. Never retroactively test implementation or call unproved behavior complete.
Real-time editor gate applies src/general_ludd/**/*.py only; expected tests/unit/test_general_ludd_foo.py or test_foo.py must exist. tests/docs/scripts/config/non-Python pass. Allowlist __init__.py,*.pyi,protocols.py,typing.py,type_defs.py,_types.py matches check_tdd_compliance.py. GLUDD_TDD_ENFORCE=0 only for genuinely blocked test-first legacy refactor; commit check still backs it.
`make check-tdd-compliance` requires a tests/unit file that imports each modified src module AND contains test functions; modified modules below50% block, target85%; documented config/tdd_allowlist.yml reasons required, “don't need tests” is invalid. Release85/75 floors remain mandatory.

## CRITICAL: Commit-After-Green Policy

Commit each small complete tested change before new work: `make test-and-commit [MSG="..."]` runs full tests then commits. Do not leave green work uncommitted.
Clean tree before dispatch: commit or stash, restore with git-stash-pop. enforce-clean-tree.ts denies dirty dispatch (fail-open); GLUDD_CLEAN_TREE_ENFORCE=0 only focused-single-file. Worktree isolation prevents shared index/commit races.

## CRITICAL: No-Commit-Bypass Policy

Every commit-shaped target enforces fresh GREEN .gate-status/_gate-fresh-check, no feature/stash/preexisting/environment excuse. git-commit/commit-no-verify/commit-bootstrap/git-commit-file retain gate; --no-verify skips ONLY hook stash, never gate.
repo-commit is only documented non-code meta escape (version/artifact/docs) and still has _commit-lint-guard; never land red/syntactically broken code. test-and-commit has its inline pytest micro-gate. New commit owners require gate or explicit documented allowlist in test_commit_gate_freshness.py.

## CRITICAL: Don't Push Every Commit — Batch Locally, Push Once

NEVER use COMMIT_THRESHOLD=1 under the mechanical prohibition. ship-commit defaults PUSH=0; local git-commit/ship-commit accumulates, batch-push default5+ commits after ci-verdict-safe confirms idle; PUSH=1 or separate batch-push is explicit push.
The original batch section also documents COMMIT_THRESHOLD=1 immediate and GLUDD_FORCE_PUSH=1 bypass; preserve them as documented mechanisms, NOT permission overriding the prohibition or specific in-message authorization below.
Validate lint/typecheck/collection/targeted tests and background local gate before push. The original “WAIT for CI/use ci-push or ci-wait” wording remains alongside release-cut-only wait restrictions below; do not silently infer a general wait exception.
One CI run in flight; pending blocks; cooldown30min; >3 cancellations/2h blocks (the source also forbids3+). Never push each commit, while pending, inside cooldown or treat CI as sole validation.

## CRITICAL: Evidence-Based Response Policy

Every factual claim MUST have supporting evidence from a tool call, file read, URL fetch, or test result.
- If you say “X tests pass”, cite the make output including the pass count.
- If you say “file Y contains Z”, cite the file path and line number.
- If you say “opencode supports X”, cite the URL or docs page you fetched.
Unsupported claims violate policy. enforce-make and review/evidence_checker.py enforce. This HAVE requirement is not generalized into CITE-every-fact or same-message-every-fact.

## CRITICAL: Verification Before Claim (Anti-Lying Guardrails)

Done/landed/pushed/fixed/green requires verification OUTPUT in SAME response. Before ANY status claim run/paste `make verify-state`, or the specific per-claim command in the Done table. Exact-tip CI and fresh gate only. Commit→git-log hash, push→verify-remote, tests→count.
-nv/--no-verify/GLUDD_FORCE_PUSH=1 require explicit in-message user authorization for that specific invocation; never unprompted. Verified-claims fail-open disable, clean-tree dirty-dispatch guard and per-editor worktree isolation are independent layers.
Verification is external to generating-model memory: SWE-bench FAIL_TO_PASS, CoVe independence, Aider dirty commits and Cline shadow git remain [research context](docs/AGENT_POLICY_V1_SOURCE_3583000.md#research-basis), not fresh proof.

## Project Overview

general-ludd-agent is an autonomous coding system with Ansible runners/multi-model agents. Python3.11+; uv preferred/pip fallback; pytest, ruff, mypy; FastAPI+Gunicorn+uvicorn-worker; PostgreSQL/Alembic; OpenBao/hvac; Ansible Runner; TDD and Molecule for Ansible content.

## Key Make Targets

The mandatory [key Make targets](.agents/policy/key-make-targets.md) must be read before selecting/changing a target. Make Target Selection Contract remains authoritative; legacy master/rebase examples do not relax current branch/Make rules.

## CRITICAL: Session Persistence Policy

Read SESSION.md at startup and update after EVERY logical feature/fix/test-suite unit. Include date, pass/fail/skip+coverage, last hash, completed objectives, gaps and next steps; never leave stale. BehaviorRenderer/AgentBehavior.session_persistence mirror it.

## CRITICAL: Task Self-Tracking (Anti-Forgetting)

TASKS is the machine-verifiable ledger. Every dispatch gets unique W.N/G.N/FIX-N entry BEFORE dispatch; cross-check unchecked/not completed, add missing entry FIRST and record “| dispatched: <timestamp>”. Hash/spec dedup and completed-task grep prevent redispatch.
After EACH result, before next result/wave: find ID; completed→tick+hash/test evidence ONLY when proof obligations allow; partial/failed→blocked/in_progress+reason. Never bulk-delay updates or treat a returned result as committed. Release source/focus never substitutes full-gate closure.

## CRITICAL: Enforcement Plugin Reference (Session-Start Self-Awareness)

Read the mandatory [enforcement reference](.agents/policy/enforcement-plugins.md) before plugin/enforcement work; restart/verification/fail-closed obligations remain authoritative. Run `make list-plugins` at session start and know blocks before ANY tool call. The source's session-first duties/order tensions remain explicit, and adaptive cost precedence overrides legacy>=3 floors.

## Working Conventions

Small atomic testable increments; thin event loop; Ansible playbooks are tool-call boundary; never force-push; Make-only; test-first; commit green; new guardrails all3 layers. Feature branch per feature via feature-start, feature-done after full tests; each commit one logical change. Current development-first/master guards constrain legacy feature-done examples.

## CRITICAL: Self-Audit Policy

After significant work and before done, run EVERY audit; fix all gaps. FIRST thoroughly query ALL prior user messages through `make audit-messages`/native owner at ~/.local/share/opencode/opencode.db; compare each ask to src/tests/SESSION, including prior sessions, not just current prompt.
Exact query: SELECT p.content FROM message m JOIN part p ON m.id = p.message_id WHERE m.role = 'user' ORDER BY m.id;
Audit every new class/module's imports across src (test-only→dead; wire into daemon/event/worker); every schema field creation→JobSpec/EventLoop propagation→Worker consumption→API response; for each NEW SQLAlchemy model or column, require an Alembic revision with linked down_revision and fully reversing downgrade.
Require unit+integration(2+subsystems)+E2E daemon-API tests. Check endpoint/CLI/TUI/config/log project_id/secrets project paths and project overrides. New CLI→TUI; API→CLI+TUI; config→endpoint+flag; view→all relevant views. One-interface-only is not done.
Then `make test` count, `make lint` and `make typecheck` results as evidence; commit green. Exact original execution checklist is retained in the comparison source.

## Branch-landing integrity (codified)

Shared/RC master/main/release/* mutations only on main /Users/shawnwilson/gludd, never isolated agents. After EVERY sandboxcom push `make verify-remote BRANCH=<branch> SHA=<local-HEAD>` must print VERIFIED; silent up-to-date is not proof. CI only counts when headSha==branch tip; stale-run warning means discard, not success.

## Model Utilization — Keep Sonnet Dominant

sonnet is cost-efficient default. Default advisory nudge when sonnet share is>10 percentage points below combined others. Optional `make set-sonnet-target HOURS=24 SHARE=0.67` activates2:1/67% until until_epoch; .claude/sonnet_ratio_target holds target_share/until_epoch. GLUDD_SONNET_TARGET_CONFIG overrides path, GLUDD_SONNET_TARGET_SHARE per-call share; expiry returns to10%-band.
model_utilization_pretool.sh counts CURRENT dispatch before rolling last20 in /tmp/gludd-model-util.json; fail-open, silent when healthy. Agent matcher settings include ceiling/disk hooks. Rebalance next suitable N calls toward model:'sonnet'; stronger complex synthesis may use opus, simple research haiku/sonnet. Never suppress/remove nudge.

## Multitasking / Blockers

Serial only for shared-master mutations, single gate/commit/push slot or conflicts in daemon.py/routers/facts.py/db/models.py/db/repository.py. Otherwise use independent worktrees. Before waiting ask shared-tree mutation? slot needed now? unmerged dependency? All NO→not a blocker. Full procedure docs/ORCHESTRATION.md; caps/load/ownership still apply.

## CRITICAL: Release Pipeline Must Be CI-Green (codified)

Exact tagged commit must have successful Build and Release before tag. release-cut step0 require-ci-green and CI release needs:gate enforce independently. PENDING/queued/running→exit2 abort; failed/cancelled/timed_out/unknown/no matching run→exit1 abort; success→0. `make require-ci-green [SHA=…]` owns check.
Never manual tag, straight-master fix-forward wave as releasable (use release-candidate/* then ship-ff), or green claim without exact run ID/SUCCESS. test-iso is not gate.

## CRITICAL: Pipeline Completion Is The Primary Objective

Pending release is #1 priority above unrelated enhancements. FIRST dispatch in every wave MUST be pipeline-focused: push unpushed commits, check CI verdict, fix CI failures or cut the release. Secondary work is SECONDARY priority and must NEVER consume more than 50% of the dispatch wave; in three slots at least two advance pipeline.
Check push status at session start: with unpushed commits pushing them is the FIRST action before backlog/dispatch/tests, subject to shipping guards; preserve original startup-order tension. “CI is pending” is never a stop; do release-advancing work, not unrelated feature work.
Forbidden: all three slots with unrelated structural-test work while the pipeline is red; new enforcement plugin while commits sit unpushed; documentation while CI is failing; 0 pipeline-focused dispatches with a pending release.
Release is NOT done until `make verify-release-completeness TAG=<tag>` exits 0 with all 28 artifact categories and30-asset minimum. Tag/greenCI alone is insufficient. Tests: test_pipeline_priority.py; OD.1,OD.3,OD.8,DC.1 reinforce.

## CRITICAL: A Release is an Artifact, Not a Tag (codified)

CORRECTION: wherever legacy text says verify-release-artifact, read `make verify-release-completeness TAG=<tag>`. The former checks only non-draft+≥1asset, NOT release completeness. Real gate checks28 categories,30 assets, prerelease/tag shape, stamped names and zero-size assets, blocking CI tags.
`make release-create` is CI-green-gated draft-only single-binary fallback, never public publish. release-cut is sole publish path; full procedure docs/RELEASE_RUNBOOK.md.
Never next-version bump/epic until current exact Build-and-Release GREEN+complete artifacts. TASKS completion needs non-draft gh release view/assets evidence, download URLs, CI run ID+success, plus completeness PASS. No tag-only done; no timed-out release-cut as artifact proof. Legacy asset verifier fails closed on missing gh/network/release.

## CRITICAL: Three-Agent Dispatch Ceiling and Configurable Floor

### Dispatch Floor

Canonical single source of truth .opencode/lib/multitask_config.ts: MIN_DISPATCHES=0,MAX_DISPATCHES=3,HARD_MAX_DISPATCHES=3,MAX_ZERO_STREAK=2; plugins import them. Env/state may lower, never raise cap. GLUDD_MIN_DISPATCHES0..3 clamps;0 disables minimum only, not cap/dedup/ownership.
enforce-multitask blocks breaches of explicit prior-message floors; source's current-zero+pending block description remains constrained by canonical zero-floor override. Positive-floor two-zero-streak and immediate under-floor blocks otherwise apply. GLUDD_MULTITASK_FLOOR_ENFORCE=0 disables; disengage temporary expires MAX_DISENGAGE_MS without bypassing fundamental pending latch.
Task quality2–5min real synthesis/deliverable, sufficient context, no quick grep/status placeholders. No-independent-work means inline, never filler.

## CRITICAL: Up to 3 Useful Subagents

On completion count active agents; promptly refill only if another independent useful deliverable exists, else continue coupled work inline. The active harness floor is configured to zero: CLAUDE_AGENT_FLOOR=0 and GLUDD_MIN_DISPATCHES=0. TypeScript cannot live-count; caps wave width, floor-specific streak/result/refill/start/thin-wave guards only with nonzero floor. Python agent_liveness.py informs shell hooks, not TS. Do not manufacture parallel ownership; never disguise duplicate/coupled/status work as concurrency.

## CRITICAL: Anti-Grinding Enforcement

Positive configured floor only: enforce-floor mutation counter resets after30s inactivity and blocks at5 calls; satisfy floor or documented recovery. Default0 returns early for this delegation path. GLUDD_FLOOR_ENFORCE=0 disables.
Read detector defaults: GLUDD_READ_GRIND_ADVISORY_COUNT=5,ADVISORY_MS=30000,DENY_COUNT=10,DENY_MS=60000,STALE_MS=60000,FILE=/tmp/gludd-read-grind.json. Focused investigation may set DENY_COUNT=20; dispatch always resets0.

## CRITICAL: System-Load Gate Before Dispatch Waves

Before EVERY dispatch wave `make check-system-load` reports1-min load+CPU. Above2x the CPU count: kill owned background gate via gate-kill, exit poll owners, trim wave. Above3x CPU count: HALT dispatch entirely, clean-tmp/gate-kill, wait below2x. Recover with one useful owner/one wave, then≤3 after60+s low load. Never background gate -n auto plus full wave oversubscribing cores; pause/cap one. Use native ownership/cleanup authority, never inferred process identity.

## Pipeline Orchestration Model

Pipeline useful batches; do not drain to0 while independent work remains. Bias disjoint/new files, at mostONE owner per hot daemon.py/loop.py/gateway.py; serialize hot edits. One continuous integrator merges sequentially, retains BOTH independent fixes and gates each merge green.
Older non-isolated new-file preference and~5–6 worktree-disk cap remain source context under stricter file-editing isolation and canonical3 ceiling; they do not authorize shared-file edits or extra agents. Read-only main research has no mutation/commit.

### Worktree-per-subagent (file-editing tasks MANDATORY)

Any mutating subagent gets isolated branch+worktree agent-<short-descriptive-name>; no shared main files, push, master merge or own subagents. Main orchestrator lifecycle:
`make agent-worktree BRANCH=agent-<name>`→WORKTREE_PATH=/tmp/gludd-worktrees/agent-<name>; agent edits/commits there; main merges/cleans. Existing branch redispatch attaches fresh worktree.
Legacy example `make agent-merge BRANCH=agent-<name>` means --no-ff master fan-in; current development-first discipline/lifecycle uses `make agent-merge-dev BRANCH=<name>`. Both example forms remain preserved, not independent authority to bypass current guards.
`make agent-worktree-list` diagnoses. No isolated agents live→clean-worktree-venvs. docs/ORCHESTRATION.md§4/§5 apply.

### Git Worktree Lifecycle — Merge or Clean Up

Every worktree MUST merge to development BEFORE cleanup: agent-merge-dev then IMMEDIATELY agent-cleanup, one atomic unit. No lingering after session; final list only main checkout. >24h unmerged worktree, missing remote branch or abandoned commits violates worktree-health-check; resolve all before gate green.
worktree-merge-all attempts --no-ff development merges, reports conflicts and cleans successes. Health script checks age/reachability/remote. Older320MB/worktree and~5–6 concurrent worktree agents cap remain disk rationale, not override of3.

#### ⚠️ KNOWN GAP: git locking is broken inside worktrees (read before running a wide worktree wave)

Historical verified2026-07-14 git_automation/locking.py git_repo_lock source locator treated .git as directory; worktree .git FILE fell back to in-process RLock, not cross-process. Read-only/own-branch routine commits stay low-risk; concurrent merge/tag/push MUST serialize main, no two owners same wave. The old proposed git-common-dir fix and docs/design/NEXT_RELEASE_BETA2_SPEC.md/docs/MULTITASKING_POLICY.md are context, not current-runtime verification.

### Subagent dispatch reliability rules

Every task needs timeout, under5min/default300000ms; split longer or main owns observable run. enforce-deadline records /tmp/gludd-task-deadlines.json, warns TASK DEADLINE EXCEEDED/stale; orchestrator resplits/replaces/foregrounds. task_watchdog polls5s, records kills to /tmp/gludd-task-killed.json and applies SIGTERM→SIGKILL through native owner.
Use `make task CMD='...'`; GLUDD_TASK_DEADLINE_ENABLED=0 disables detection, GLUDD_TASK_TIMEOUT_MS sets limit; watchdog-auto starts owner. NEVER dispatch make gate (~40min) to subagent; main-loop observable background owner, not cancelled5min task. ONE focused task; file-editing dispatches must specify exactly one file. The source also permits multi-file tasks; retain that tension rather than choosing a looser scope. Read-only is reliable; prompt enough context. No filler refill.

### Main-thread command restriction (ANTI-STALL RULE)

Source allowlist: ci-verdict-fast(<1s), Task dispatch, ship-commit via subagent. NEVER run on the main thread: make gate/test-unit/test/qa/e2e/validate/lint/typecheck/collect/smoke, git-add-all/commit-no-verify/git-push-branch-nv and ANY>3s.
The same source permits bounded foreground work with streams/heartbeats and later requires >~30s backgrounding; preserve this scope tension, not a blanket bypass. Serialized ship ownership remains mandatory.

#### Background-gate workflow (canonical way to run a long gate)

gate-background returns<1s with native log/PID; continue useful work. gate-progress is preferred one-line state/%/elapsed/ETA/counts at natural breaks≤1/~2min (GATE_PROGRESS_COOLDOWN_SECS=120; GATE_PROGRESS_FORCE=1 only immediate genuine verdict). COOLDOWN still prints status/next poll; no stale-number polling.
gate-status-check is for raw phase/terminal/.gate-status/last20-line failure diagnosis; gate-tail must not block main. gate-logs lists historical results; gate-kill TERM thenKILL after5s. On terminal ingest/act next: PASS→commit/push/verify; FAIL→named repair/gate-resume.
No main make gate; gate-background only if nonblocking. Background markers “=== GATE PHASE: <name> ===”, terminal PASSED/FAILED and logs/PID remain.

### Steady-state dispatch (up to three useful agents)

Process evidence/results promptly before reassigning, plan disjoint deliverables, prefer balanced scope, ship/merge sequentially; no fixed read quota. Zero inline or one useful dispatch is valid;2–3 batch when independently justified. Never one-task dispatch then idle when≥2 work items permit inline/nonblocking parallel progress.

## CRITICAL: Background Operations NEVER Block Dispatch (anti-wait rule)

Launch observable -background(<1s), IMMEDIATELY resume useful disjoint work; inspect once at natural break. No main sleep+status, gate-tail, “wait then decide/commit”, silent task or polling owner. GLUDD_NO_WAIT_ENFORCE=0 disables default-on enforce-no-wait.ts; matchers cover sleep+Make/status/tail.

### CI-Poll Subagents Are Forbidden

CI-poll subagents FORBIDDEN. NEVER dispatch a "poll CI until terminal" task; `make ci-await` must NEVER be dispatched to a subagent. `make ci-wait` and `make ci-await` are for release-cut only. `ci-await` uses a 3600s default timeout. No verdict loop until terminal. Push fire-and-forget via batch-push+verify-remote; never hold non-release work for green.
Original exception permits one gate-wait-report call then inspection/redispatch only if unfinished, never “every N seconds” loops. This scoped exception and forbidden polling-task phrases remain, not new permission.

### Machine-Enforced CI Check Cooldown (2026-07-08)

Routine checks use ci-verdict-safe, never bare ci-verdict (release-cut-only/current anti-loop); default600s via CI_CHECK_COOLDOWN_SEC. Canonical deploy-and-forget→work→single check30+min later; OD.3 separately says natural breaks15+min.
Within cooldown→exit3/CI-COOLDOWN and NO query, means UNKNOWN not PENDING. Expired→0 record/query; FORCE=1→0 record/query is release-cut ONLY. Source also says use FORCE for actual state if cooldown>5min old; retain this explicit conflicting scope without silently broadening it.
ci-cooldown-status read-only; /tmp/gludd-ci-check-state.json fields last_check_epoch,last_push_epoch,last_head_sha,check_count; GLUDD_CI_STATE_FILE test isolation. Dispatch-time CI_POLL_DISPATCH_PATTERNS rejects intent; fail-open.

## CRITICAL: No Idle Monitoring While Work Remains

Background running is not a stop: interleave monitors with concrete independent deliverables. Forbidden3+ consecutive gate/CI/git status checks without edit/write/mutating-Make/dispatch. While .gate-background.pid alive every turn has tool (productive work/allowed gate-progress/dispatch), never text-only idle.
Owners: agent_watchdog _gate_background_running/_pending_work_exists, watchdog_components/enforcement.py5s idle, enforce-no-idle-monitoring and enforce_stop_impl gateRunning. Finished PASS→commit/push/verify, FAIL→repair then gate-resume.

## CRITICAL: Long-Running Operations MUST Be Backgrounded

Expected>~30s MUST use tested observable -background target, continue useful work, check only natural breaks, ingest terminal. Missing background owner→add/test first. Never bypass long-op denial by sibling/split command, run multi-minute “just once”, go silent or add auto-relaunch watchers. Stream phases, report current phase/last line, surface failed log.

## Codify Improvements (Meta-Rule)

Better process discovered→codify SAME SESSION before moving on. Priority order: (1) concise AGENTS policy; (2) enforcing .claude/hooks PreToolUse/PostToolUse script registered in .claude/settings.json; (3) memory entry when insight must survive session resets AND applies globally, OR is too detailed for AGENTS. Add guardrail tests where useful. New behavior restrictions still need config/runtime/prompt ALL3 layers above.
Source shell orchestration describes no-wait/floor advisory-by-default, elevated GLUDD_NO_WAIT_ENFORCE=1/GLUDD_FLOOR_ENFORCE=1, alongside earlier blocking-plugin descriptions; retain both descriptions and verify actual runtime.
GLUDD_FORCE_DELEGATE=1 opt-in targeted-mutation guard counts live agents vs CLAUDE_AGENT_FLOOR with GRACE=3/MAXBLOCK=4; read-only/memory writes/dispatch always allowed and dispatch resets. agent_liveness counts Workflow agents. Narrow noisy checks, do not delete/make permanently advisory.

### Todowrite discipline (mandatory for ≥3-ask sessions)

≥3 distinct user asks (including implicit process fixes) require todowrite tracking every ask until codified all3layers, committed and if applicable pushed. Update after EACH result; complete only after committed codification, never merely returned. Do not drop asks.

## CRITICAL: Self-Test Quality — Structural vs Behavioral

Structural-only self-tests prove intent, not runtime. Every plugin needs actual-hook return/side-effect assertions; lifecycle: violation→block, env disable→allow, OPENCODE_SUBAGENT=1→allow, corrupt/exception→fail-open. New plugin ships runtime harness test; `make test-hook-runtime` green before committing ANY plugin change. No Python shadow-state-machine substitute for actual TS.

## CRITICAL: Plugin Hook Invocation Validation (Anti-ReferenceError Gate) (DC.4)

EVERY plugin edit MUST run `make check-plugin-hook-invoke` BEFORE commit. Native validate_plugins_runtime.mjs imports, calls the factory IF default export is callable, then hooks (tool.execute.before, experimental.text.complete, text.complete, session.idle, experimental.chat.system.transform) with null-safe inputs; ReferenceError fails; other errors are ignored (null-input TypeError is the original example), not a stricter new validator scope.
check-plugin-validate is supplementary static compat/import/shape (<1s; undefined-call strict opt-in); check-plugin-runtime delegates to hook-invoke (~5s). ALL3 remain in gate; import alone is not behavior. No undefined cross-plugin function references; import or inline definitions.

## CRITICAL: Root-Cause-Only Fix Policy

ALL application/tool/plugin/guardrail issues must be repaired at root cause, not symptom; this governs integrity/fix-never-disable/constraints/suppressions. Before ANY fix identify causal origin, test whether proposed change removes purpose, and whether a different trigger can recur. Disabling/skipping/bypassing/ignoring/emptying checks, artifact-optional, xfail/retry-race or timeout-only fixes do not address root cause.
Config/runtime/prompt/hook/test layers enforce; historical precedents and exact WRONG/RIGHT examples remain in the versioned source.

## CRITICAL: Operational Discipline Rules (Session 52 Codification)

### OD.1 — Intermediate Progress Is Not Completion
Reporting that a build is running, a tag is pushed, or CI is pending is NOT a stopping point. Completion = `make verify-release-completeness TAG=<tag>` exits 0.

### OD.2 — Follow Explicit Instructions Exactly
Meet each measurable requirement exactly. Do not optimize, substitute or “improve”:16000 words means16000 words;12 artifacts means12 artifacts.

### OD.3 — CI Is Fire-and-Forget
Check at natural breaks15+minutes apart; never main-thread sleep/wait.

### OD.4 — No Text-Only Responses With Pending Work
Unchecked TASKS means a tool call every response; no status without action.

### OD.5 — Answer Direct Questions Directly First
“Yes” or “No” before context/explanation for a binary question.

### OD.6 — Don't Rationalize Stops
Rationalizing pause for CI/build/explanation is a malfunction while work remains.

### OD.7 — Don't Override User Instructions
NO exceptions means every exception violates; don't stop means don't stop. No judgment override.

### OD.8 — Don't Make Artifacts Optional
12/12 means fix builds; never lower required artifacts to accept failure.

### OD.9 — Don't Push Broken Code Without Lint
`make lint` before EVERY commit; hooks are backup.

### OD.10 — No CI Polling as Pretend Work
Checking ci-status more than3 times in a row without code changes is a stop pattern; native limiter enforces.

## CRITICAL: CI Wait Productivity (DC.1)

During CI waits, advance release-related work: fix tests, write structural tests, update docs or investigate slow shards. Dispatch subagents for concrete independent deliverables when useful; zero subagents is valid when none exist. CI waiting never justifies filler. Use up to three useful owners.

## CRITICAL: Polling CI Is Not Work (DC.2)

Checking ci-status more than 3 times consecutively without code changes is a stop pattern; dispatch productive deliverable rather than poll.

## CRITICAL: CI Status Must Be Actionable — No Passive Polling

Reporting only workflow-level ternary state (in_progress/success/failure) is passive polling. Every check needs actionable insight. After one high-level check, do not poll again with `make ci-status` until you used a rich target for job/step state, dispatched work to reproduce a failing shard locally, OR Made and pushed a code change affecting outcome.
Rich targets: `make ci-run-summary RUN=<id>`, `make ci-view RUN=<id>`, `make ci-dashboard`, `make ci-active`, `make ci-annotations-anon RUN=<id>`, `make ci-checkrun-anno CHECK=<id>`, `make ci-job-log RUN=<id> JOB=<substring>`, `make ci-faillog RUN=<id>`, `make ci-diagnose RUN=<id>`.
A failed in-progress shard is enough: immediately use `make test-ci-shard SHARD=<name>` or `make test-ci-shard-slice`; never wait for overall terminal. Identify failedjob→annotations→localrepro. Fix the test or code, verify the shard passes, commit, and push. Respect batch/authorization guards.
Forbidden: repeating “Build and Release is still in_progress”; Calling `make ci-status` three or more times in a row without action; Waiting for a run to finish before investigating a job that already shows `failure`; Reporting CI state without also stating what failed, what runs and the next action. Tests/unit/test_no_passive_ci_polling.py pins this.

## CRITICAL: Git Operations Are Not Grinding (DC.3)

git-add/git-commit/git-push-sandboxcom/batch-push/ship-commit/git-tag-push/release-cut are GIT_SHIPPING_TARGETS terminal actions; enforce-delegate.ts (BP.1) RESETS the streak counter, not grind. Do NOT disengage merely to ship; guards/authorization still apply.

## CRITICAL: Root Cause Escalation (3-Strike Rule)

When the SAME CI failure class recurs for the third time, STOP patching symptoms. Inspect the SYSTEMIC dependency/structure, fix root cause and add a structural test. Increasing timeout, Dropping Python versions, Making artifacts optional or cancelling required work are forbidden symptom fixes when dependency/shard/template/push-tag ordering is causal.

## Constraints Are To Engineer Around

A constraint is a design prompt, never a dead end. “Can't/limitation/no way/have to wait” is allowed ONLY immediately paired with an implemented workaround OR dispatched research to find it; no naked can't/parking on user. Missing owner→add; rate-limited→backoff60s/retry; local OOM→appropriate CI proof path. Exact original GitHub step-granularity/shard/annotation example remains in the versioned source, not authority for bare gh.
no_wait_stop constraint matcher GLUDD_NO_WAIT_ENFORCE=1 and scripts/test_no_wait_hook.py enforce.

## CRITICAL: All Bugs Are Your Bugs — No Pre-Existing Exceptions

Every repository failure belongs to you: fix ALL CI/test/lint/type failures regardless of introduction, never excuse “pre-existing/already broken/someone else's”. No classification/reporting as substitute for repair; red work is work to complete. Release bounded-proof ownership above remains intact.

## Disk Discipline

Do not fill disk. `make clean-tmp` before EVERY session and after large subagent batches; stale-PID state cleanup and watchdog log rotation>10MB. `make check-disk` pre-commit fails /tmp/gludd-* total>100MB or disk>90% (scripts/check_disk_usage.py). Preserve these original limits separately from other native hard-capacity owners; never infer cleanup authority from age/PID alone.

## Keep Opus Lean — Sonnet Carries the Token Load

Target sonnet tokens≥opus main tokens, preferably SEVERAL TIMES (source cost context opus~5x). Heavy reading/editing/testing/synthesis to suitable sonnet; terse main judgment/coordination, avoid rereading transcripts/established facts. Subagents return≤5 bullets+file pointer, not raw text.
No live per-agent token accounting exists; model-utilization dispatch-count proxy≥10:1 is not actual token measurement and remains separate from10%-band/optional67% mode. Caps/default-floor/task-shape constraints still apply.

## Versioned preservation context

This root consolidates repeated instructions; every unique binding actor/trigger/limit/exception and proof obligation is intended to remain above. [Original source and exact historical examples](docs/AGENT_POLICY_V1_SOURCE_3583000.md), [backward/forward map](docs/AGENT_POLICY_V1_PRESERVATION.md), [measured context](docs/AGENT_POLICY_V1_METRICS.md) and [takeover](docs/AGENT_POLICY_V1_TAKEOVER.md) are versioned comparison/handoff context, not broad always-read includes or additional permissions. The two preexisting mandatory policy references retain their original triggers.
Unresolved original ordering/runtime/ratio/WAIT/example tensions are called out rather than silently decided. This applied isolated-branch candidate still requires independent preservation review; heading/literal/format tests alone cannot certify whole-file losslessness.
