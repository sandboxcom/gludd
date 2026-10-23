# GHA pipeline and agentic promotion audit

S98.4, 2026-10-23 UTC. This record covers local and hosted build phases,
parallel source work, Make results and release proof. An inspected or locally
repaired phase is not a successful hosted run. All seven v0.1.2 tasks remain open.

## Actual hosted failures and unpublished source

The latest pushed candidate is eb772a658a01db4485087813e4478eb6c59cc328:

| Workflow | Actual result | Failed input and downstream effect |
| --- | --- | --- |
| [Build and Release39826135809](https://github.com/sandboxcom/gludd/actions/runs/39826135809) | Failure;3.11 gate passed |3.12 runtime admission timed out124 at90.5s,job119989841880;14 downstream jobs skipped. All eight shard timings remain unknown. |
| [Molecule39826135844](https://github.com/sandboxcom/gludd/actions/runs/39826135844) | All six jobs passed | This is separate from the skipped Build Molecule job and supplies no missing Build or deployment result. |
| [Pages39826135878](https://github.com/sandboxcom/gludd/actions/runs/39826135878) | Chromium failed;WebKit passed | Chromium dependency acquisition hit its600s limit;JS1 and deployment skipped. Azure font fetches stalled before native install timeout124. |

The newer local source checkpoint aa494265cc427fe79a706461859c6532dec223f2
has no hosted run. GitHub refused the repair push because the shared sandboxcom
OAuth credential lacks workflow scope. Explicit operator consent remains pending.
Read authentication exit0 is not workflow write authority. Do not dispatch an
older remote or manually trigger a run and label it exact-candidate push proof.

Native pipeline-status observations retain both identities separately in
/tmp/gludd-v012-eb772a6-pipeline-status.json and
/tmp/gludd-v012-aa49426-pipeline-status.json. These are generated observations,
not files for manual editing. The former is failed hosted history; the latter
is missing hosted evidence. Local disk96% exceeds the unchanged hard95% limit
and blocks final local admission. It does not replace the independent guarded
development-repair publication route or grant credential consent.

## Complete phase and proof map

[Build](../../.github/workflows/build.yml) and
[Pages](../../.github/workflows/pages.yml) remain authoritative executable DAGs.

| Phase | Existing execution / authority | Required result and present limit |
| --- | --- | --- |
| Local dispatch | work-item,active-work-status,check-system-load | Check ownership/load before each wave;three agents maximum,two coding/testing owners,one public HTTP research owner,one Git writer. Mutating agents require isolated worktrees. |
| Local source repair | test-files,lint-files,separate typecheck-scope | Native failing-first regression,complete affected owners and original assertions. Focused green is source acceptance. |
| Source checkpoint | ship-commit-files PUSH=0 | Explicit paths,content/index guards and one fresh normal collection. A local commit supplies no publication or CI proof. |
| Candidate publication | Existing guarded batch/repair push,verify-remote | Canonical push event,full exact SHA and actual remote verification. Keep batching,auth/runtime guards and unchanged-failure refusal. |
| Hosted triggers / scheduling | Branch/path filters,concurrency,version | Relevant changed inputs must trigger required workflows. Preserve useful push runs;PR coalescing is distinct. |
| Source proof | Tag-only release_source_proof | Exact development SHA and all required successful push workflows before tag publication. |
| Independent acceptance | Claim-before-provision,Postgres lifecycle | Native isolated execution and cleanup;successful unrelated jobs do not clear a failed gate. |
| Gate setup | Exact checkout,locked runtime and EE manifest | Pinned image/requirements and explicit installed interpreter;no mutable replacement or hidden refresh. |
| Early admission | Promotion prerequisite then runtime watchers | Separate original90/60 owners,all native children/statuses,2/1/1 scheduling. The measured3.12 failure remains unverified on hosted repair source. |
| Cheap checks | Lint,strict typing,collection,policy,enforcement,security,CSS | Every original failure remains fatal;plans and diagnostics do not replace execution. |
| Eight test shards | Canonical3.11,batch16,workers1,post-failure diagnostics | All eight real terminal attestations and coverage databases. Skipped/unknown intervals are not zero-time successes. |
| Aggregate coverage | Existing combine/audit owners |85% aggregate and75% each file for LINE AND BRANCH. Imported numbers do not prove live E2E execution. |
| Molecule | Build four shards and independent six-shard workflow | Preserve scenario inventories,budgets and actual all-workflow exact-SHA observations. |
| Packaging | Linux,macOS,Windows,Termux,container,Ansible EE | Native build,installed/extracted artifact smoke,cleanup and complete manifests. Source checks are not cross-platform execution. |
| Upstream / providers / game | Existing conditional/dispatch owners | Retain conditions and scope;missing-secret or intentionally skipped execution is not functional proof. |
| Pages acquisition | Native archive prep,complete installer and launch | Current source repairs exact failed Azure preference;hosted acquisition remains required. Preserve600s and all dependencies. |
| Pages JS0 | Chromium AND WebKit,original20min jobs | Both must pass before JS1;no package removal or browser substitution. |
| Pages JS1 | Separate Chromium full-source coverage job | Actual CDP/V8/c8 measurement at85/75 line AND branch;skipped JS1 grants no measurement. |
| Pages deployment | Master push,needs JS0 AND JS1 | Deploy exact validated artifact,then exact40hex public revision probe. Candidate validation is not deployment. |
| Release publication | Tag fan-in,artifact matrix,checksum/attestation,rollback | Preserve immutable triggering tag,all native artifact/download/completeness checks and real post-publication proof. |

## Confirmed repairs and remaining audit findings

| Boundary | Finding / owned action | Actual evidence / remaining proof |
| --- | --- | --- |
| Missing versus running GHA | pipeline-status now emits outcome-specific stderr guidance and hosted_next_action in each native missing-proof record |11 RED then11 GREEN;724 complete cases on both installed runtimes. Original stdout,exit precedence,current-success invalidation and legacy capacity next_action remain. |
| Draft recovery | Remove only --cleanup-tag from the actual draft cleanup shell | Isolated agent-v012-gha-build-phase:2FAIL/4PASS before repair,then6PASS and303 complete affected cases on each runtime. Main receiving,normal commit and hosted publication remain pending. |
| Pages trigger inputs | Add actual profile catalogue/locks,dependency implementation,stream observer and owned-process inputs to the shared push/PR filter | Source-confirmed omission;native trigger regression and repair pending. Preserve every previous filter and master-only deploy. |
| Commit mutex lifetime | A prerequisite shell releases fd9/fcntl before staging/collection/commit | Source-confirmed;needs two-process native transaction RED and reuse of existing common-directory git_repo_lock,not another mutex. |
| Bulk integration | worktree-merge-all can merge into current candidate,auto-commit foreign dirt,and hide inventory/retirement failures | Source-confirmed;existing integration/retirement owners must fail closed and retain native statuses. No bulk mutation is authorized by this audit. |
| Fresh-branch batching | Failed/missing upstream becomes a measured zero,despite native creation route | Source-confirmed;distinguish genuine zero,absent remote and failed lookup without lowering default threshold or bypassing guards. |
| Development transport | Advertised development-push unconditionally requires SSH despite reviewed HTTPS | Source-confirmed;reuse existing transport-aware auth/publication owners,retain actual consent and exact-SHA proof. |
| Worktree creation | Add/fallback/registration failures can be followed by ready/exit0 | Source-confirmed;native failure/partial-state controls and exact resulting identity verification pending. Root independently verified both newly created worktrees. |
| Read-only helper | Four isolated status reads omitted explicit runtime;one created a3.14 .venv | Actual operational failure. Tests used explicit311/312 and remain distinct. Native reader/no-environment-creation control and safe lifecycle cleanup pending. |
| macOS smoke | Mounted read-only DMG is followed by chmod | Source-confirmed risk;native input-boundary proof and minimal test-x repair pending. Retain actual platform smoke and cleanup. |
| Molecule scheduling | Ref/event push group can replace a pending required SHA | Source-confirmed mismatch with Build's SHA-scoped push policy;native concurrency controls and exact hosted proof pending. |
| Installer cleanup | Direct subprocess.run timeout may leave descendants | Hypothesis only;needs harmless native child/grandchild RED before reusing existing owned-process supervisor. Do not claim an observed privileged apt leak. |

The hosted-status repair retains the same twelve complete receiving owners:
pipeline_status_job_progress,pipeline_status_inprocess_ledger,
pipeline_status_exact_sha,status_heartbeat_terminal,pipeline_status_ssh_transport,
ci_failure_ledger,proof_observations,require_ci_green,require_ci_green_detect_branch,
pytest_param_fixture_collisions,ci_interaction_discipline,ci_verdict_structural_repair
(each tests/unit/test_NAME.py). Source fingerprint before this documentation:
159b170b237a5569aff4a7247cf7a6cfc7e4b5847a8a446cf6d352e74688c5e8.

311 instrumented724PASS66.87s/native69.722s:
.gate-logs/observed/coverage-files/20261023T182411Z-45386-6a49738d.json.
312 complete724PASS42.50s/native43.632s:
.gate-logs/observed/hosted-next-action-receiving/hosted-next-action-union312-aa49426.json.
Registered config/coverage_pipeline_status.ini measures BOTH complete source files.
Native .gate-logs/coverage-20261023T182520Z.json passes99.2% lines/98.8% branches;
pipeline_status.py99.1/98.6,require_ci_green.py100/100. No gate/hosted/deploy claim follows.

## Codified receiving and parallel promotion decisions

One integrator receives small complete source changes sequentially. Preserve
both disjoint fixes and their native source/acceptance receipts. Before a commit,
quiesce relevant readers/writers,run normal collection and retain exact owned
index scope. An accepted source receipt can be reused for unchanged source and
scope;it cannot certify a new merge SHA or deployment.

After each coherent tested candidate batch, inspect exact-SHA pipeline-status.
A missing run means establish actual push evidence through native guarded
publication or repair proven filters;it does not mean wait for a queued job.
A pending run already exists and needs native observation. A failed run needs
its actual failing owner repaired through the CI ledger. Separate local capacity
next_action from hosted_next_action;neither bypasses the other's prerequisites.
Never publish every tiny metadata edit or cancel useful push runs to appear busy.

Development receives reviewed source through existing guarded integration.
That resulting SHA needs its own required hosted evidence. Master promotion
retains the canonical release guard;raw legacy aliases do not replace it.
Master Pages deployment and version-tag release publication have their own
conditions and actual acceptance after candidate validation.

Initial audit dispatch exceeded the stricter three-agent wave ceiling and one
new test was written in Main before isolation. Root preserved that work,
quiesced extra owners and moved mutating work to native registered isolated
branches. Subsequent waves measure load first and reserve two test lanes.
This is a corrected operational failure,not a throughput success claim.

## Research and next action

Parallel audit reports are retained at /tmp/gludd-gha-build-phase-audit-20261023.md,
/tmp/gludd-gha-pages-phase-audit-aa494.md and /tmp/gludd-agentic-promotion-audit.md.
The two no-variable research Make owners are implemented in isolated
agent-v012-gha-research, with12 native controls passing each installed runtime,
291 target contracts and unchanged Make/line/input bounds. Main receiving and
its installed Markdown linter remain required. An initial official HTML response
exceeded2MiB and failed56 despite HTTP200. That failure remains;no bound increased.
Official contents metadata proved the79,465-byte source/blob identity.
Raw Markdown left needs/if reusables unexpanded. The documented official
Markdown API then supplied actual expanded text. Each corrected representation
has its own native receipt;none is a new GHA or repair-success claim.

Final14-body research is .gate-logs/gha-research-live-rendered-311-aa494265.log
inside the isolated worktree, native3.581s/exit0,all14HTTP200/no redirects.
Root read the relevant primary windows through Make. Earlier raw-body,
metadata and failed-HTML artifacts remain distinct. Retrieved issue JSON
contains its initial body and metadata,not all comments.

Primary findings constrain actual fixes:

- [Rendered workflow syntax](https://docs.github.com/api/article/body?pathname=/en/actions/reference/workflows-and-actions/workflow-syntax)
  confirms that failed/skipped prerequisites normally skip dependents and that
  job conditions precede matrix expansion. This explains the observed skipped
  Build/Pages descendants;adding always() indiscriminately would weaken admission.
- The same reference documents default pending replacement despite preserving
  running work. Current queue:max can retain100 pending runs but is incompatible
  with cancel-in-progress:true;waiting order is not dispatch-order proof.
  [Community5435](https://github.com/orgs/community/discussions/5435) contains
  firsthand complaints from2021 and a visible2026 reply. It is historical
  context,not proof that current queue options fail. Any Molecule repair must
  retain PR coalescing and the existing resource/acceptance ceilings.
- [GNU recipe execution](https://www.gnu.org/software/make/manual/html_node/Execution.html)
  and [one-shell limits](https://www.gnu.org/software/make/manual/html_node/One-Shell.html)
  explain why a prerequisite's lock cannot span later recipe processes.
  Global .ONESHELL also changes failure handling;it is not the transaction fix.
- [Git worktrees](https://git-scm.com/docs/git-worktree) distinguishes private
  HEAD/index context from common refs. Reuse the existing common-directory lock
  and native path resolution rather than add a parallel mutex.
- [GitHub CLI release deletion](https://cli.github.com/manual/gh_release_delete)
  explicitly gives cleanup-tag the destructive tag operation.
  The [exact pinned action README](https://raw.githubusercontent.com/softprops/action-gh-release/b4309332981a82ec1c5618f44dd2e27cc8bfbfda/README.md)
  describes draft reuse and default tag creation source. Preserve the triggering
  tag object;the older [issue323](https://github.com/softprops/action-gh-release/issues/323)
  concerns v1 invocations and does not prove a current pinned-action resolution.
- [OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
  confirms workflow-file write authority is separate. Its identical-path/content
  exception is not evidence that our actually refused changed workflow qualifies.
- [Python subprocess](https://docs.python.org/3.11/library/subprocess.html)
  distinguishes run(timeout)'s direct-child termination from explicit
  communicate cleanup. A descendant-leak hypothesis still needs native RED;
  these docs do not prove our privileged installer topology was drained.

Existing firsthand bodies already re-read for the Pages failure include
[runner-images675](https://github.com/actions/runner-images/issues/675),
[runner-images7048](https://github.com/actions/runner-images/issues/7048) and
[Playwright20678](https://github.com/microsoft/playwright/issues/20678).
They describe recurring acquisition problems across older runner releases,
not a general Ubuntu24 outage or proof that our source repair works.

Keep required local admission/full gate and exact-SHA hosted/deployment proof
pending. Resume each blocked owner only after its actual input changes;retain
the existing capacity and selected-account consent requests. Use interruptible
host input waits no longer than60s when independent authorized work is drained.
