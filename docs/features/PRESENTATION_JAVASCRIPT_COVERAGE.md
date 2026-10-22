# Presentation JavaScript coverage

The existing presentation-browser-test owner gains opt-in whole-controller
coverage. Ordinary Chromium and WebKit acceptance remains uninstrumented;
its original cold/cached readiness assertions, five-second limit and bounded
600-second commands remain required. Instrumented measurements do not prove
a latency improvement and cannot substitute for those two-engine results.

## Bounded public dependency research (S98.4, 2026-10-22 UTC)

Actual candidate eb772 Pages39826135878 fails Chromium dependency acquisition:
the existing600s command returns124 while azure.archive.ubuntu.com font
downloads stall. The retained job log is
/tmp/gludd-v012-candidate-hosted-job-log-eb772-pages.log. This establishes
neither a general mirror outage nor an IPv6/DNS cause. No unchanged retry or
longer timeout follows. Existing install/launch assertions, both browser engines,
JS coverage, ZDD and85/75 LINE AND BRANCH remain required.

Local tests and exact pushed-SHA GHA remain separate evidence. Candidate pushes
run validation; they do not deploy Pages or publish a release. The existing
[Pages workflow](../../.github/workflows/pages.yml) deploys only a master push
once validate and javascript-coverage pass. The existing [Build workflow](../../.github/workflows/build.yml)
publishes version-tagged releases after its required job fan-in. A green candidate
run cannot replace those deployment and publication proofs.

Root receives the concrete private proposal
/tmp/gludd-v012-browser-source-inspection-proposal/decision.md from
1ca50fa574acea09aaa880f986496f5587d16819 on
release-candidate/v012-git-mutex-20261022. Root remains the sole Main/shared/Git
writer. The prior native trace change's normal hooks selected129192 of129216
collected,24deselected/0errors47.81s/native53.2s in
.gate-logs/observed/test-count/20261022T184335Z-95128-f820750b.json.
Independent APPROVE SOURCE ONLY is fully consumed at
/tmp/gludd-v012-trace-1ca50fa-source-review.md. It grants no gate or hosted proof.

The existing Make source-inspection pattern and installed mature curl provide
the missing research interface; no production parser, daemon, runner or cache.
The two targets have zero public variables:

```console
make inspect-presentation-browser-dependency-source
make inspect-presentation-browser-dependency-source-live
```

The first prints an offline fixed plan. The second explicitly retrieves nine
public inputs: locked Playwright1.63.0 dependency/escalation code and native
packages, upstream APT manual sources, the current runner source tree, two
apt scripts at the observed failed-image tag and two fixed-repository searches.
The lock-binding test retains the exact presentation-test version. Upstream
main-branch context cannot certify the failed hosted image. Its initial tail
lacks the image version; the native setup-context proof below supplies it.

Each request clears private environment/config/proxy/auth inputs and uses
HTTPS/TLS,10s connect/20s total/2MiB,0retries/0redirects. Native begin/end labels,
raw bodies, HTTP/effective URL and native status stream visibly. A failure stops
later requests and retains earlier raw evidence. AGENT_NEXT names the actual
failed source, required changed input and no unchanged retry. Plan/retrieval
guidance says research_only=true, gate_evidence=false and release_ready=false;
issue relevance and apt repair stay unverified even for exit0 or empty results.
The two issue searches retain exact repos, Azure archive and Connection timed
out phrases, created before2026-01-01, oldest first, five results and one page. Inspect actual firsthand
bodies/dates/resolution before using them as long-lived practitioner evidence.

Canonical tests-first receiving retains16FAIL/0.79s/native2/about1.6s:
.gate-logs/observed/browser-source-receiving-red/v012-browser-source-receiving-red311.json.
Adding the actual owner yields16PASS/4.57s/native0/about5.2s:
.gate-logs/observed/browser-source-receiving-green/v012-browser-source-receiving-green311.json.
These are native fake-curl protocol tests, with system/package/auth tripwires,
ordinary/-e paths, native22/28/47 failures and retained prior raw bodies.
They prove no real URL availability, issue relevance, apt or hosted repair.
All nine predecessor assertions and seven result-guidance cases remain.

The native280-target contract and actual offline plan pass. Actual retrieval
then exposes two research-input failures: apt-conf-noble returns28/TLS timeout
both inside and outside the sandbox; native receipts browser-source-public/
v012-browser-source-public-first and v012-browser-source-public-network-route
retain their separate bounded failures. No unchanged third retry follows.
Use the reachable GitHub-hosted upstream APT manual sources instead, explicitly
as upstream context rather than a verified Noble package version.
Five native input controls fail before that URL/label change, then all16 pass
4.62s; receipts browser-source-endpoint-red/v012-browser-source-endpoint-red311
and browser-source-endpoint-green/v012-browser-source-endpoint-green311 retain
5FAIL/11PASS and16PASS.

The next fetch preserves those four successful sources but returns22/HTTP404
for the proposed runner manifest path:
browser-source-public/v012-browser-source-public-upstream. Replace that proven
missing input with the official bounded recursive source-tree API. Four native
input controls fail before the change, then all16 pass4.55s; retain the
browser-source-context-red/green receipts with matching v012 run IDs.
Actual eight-source retrieval passes0/1.701s:
.gate-logs/observed/browser-source-public/v012-browser-source-public-tree.json.
The returned tree identifies build.ubuntu-24_04.pkr.hcl and
configure-apt-sources.sh; a guessed manifest URL is never availability evidence.

The first oldest-first issue cohort largely concerns stale packages,404s,
caching and unsupported versions. Keep its raw output; mere search hits do not
prove relevance. Refine only the two fixed queries with the exact Connection
timed out phrase. All repos/date/page/size/transport bounds and raw-result
assertions remain. Four native controls fail1.75s before the query change,
then all16 pass4.58s. Receipts remain browser-source-query-red/
v012-browser-source-query-red311 and browser-source-query-green/
v012-browser-source-query-green311. The refined eight-source retrieval
passes0/1.900s,439036bytes, at tested source
ce5e6bf7577080842ddd29ebeefa6972d3764ab244349834fa77436cfeb2afb5:
.gate-logs/observed/browser-source-public/v012-browser-source-public-timeouts.json.
Raw bodies remain /tmp/gludd-v012-browser-source-public-timeouts.log.

### Bind source inspection to the actual hosted image

ci-job-log intentionally displays only the final400 lines. Their missing
ImageVersion was a display limit, not proof that setup metadata was unavailable.
The existing exact job context owner retrieves the actual setup without a new
observer/cache. Historical exact example:

```console
make ci-job-failure-context RUN=39826135878 JOB=119989801864 PATTERN='Included Software:' BEFORE=5 AFTER=3 MAX_MATCHES=1 CI_JOB_CONTEXT_VALIDATE_ONLY=0
```

Native receipt candidate-hosted-image-context/
v012-candidate-hosted-image-context-eb772-pages passes retrieval0/about1.7s.
The actual failed job reports Ubuntu24.04 Version20261012.334.1, with source
release tag ubuntu24/20261012.334. This is metadata evidence, not a passed job
or proof that no later step changed effective configuration.

The same public owner adds the tree-confirmed configure-apt-sources.sh as its
ninth request. Six canonical request/count controls fail with10predecessors
passing before the extension; all16 then pass4.81s. Retain
browser-source-apt-sources-red/v012-browser-source-apt-sources-red311 and
browser-source-apt-sources-green/v012-browser-source-apt-sources-green311.
The two apt script requests then bind to the observed image tag, while the
discovery tree and upstream APT manual remain explicitly upstream context.
No repository/date/page/curl-bound or prior native assertion is removed.

Actual pinned receiving runs the280-target contract, offline plan, complete
16-case owner (PASS4.80s) and all9HTTP200 fetches in one native command chain:
.gate-logs/observed/browser-source-pinned-receiving/v012-browser-source-pinned-receiving311.json,
native0/about8.4s. Full source bodies and XML stay in
/tmp/gludd-v012-browser-source-pinned-receiving311.log and .xml. This is source
retrieval plus focused acceptance, never actual package acquisition.

The [tagged mirror-source script](https://raw.githubusercontent.com/actions/runner-images/ubuntu24/20261012.334/images/ubuntu/scripts/build/configure-apt-sources.sh)
declares Azure HTTP priority1, Ubuntu archive HTTPS2 and security HTTPS3 behind
mirror+file. The [tagged acquire configuration](https://raw.githubusercontent.com/actions/runner-images/ubuntu24/20261012.334/images/ubuntu/scripts/build/configure-apt.sh)
declares bounded retries/timeouts. Prefer a guarded change to that known mirror
preference over rewriting suites/components/signatures or bypassing the complete
dependency installer. Actual runtime files must still match the accepted
format before mutation, and the changed source needs actual hosted acceptance.

### Actual primary and long-lived practitioner findings

The [pinned Playwright dependency implementation](https://raw.githubusercontent.com/microsoft/playwright/v1.63.0/packages/playwright-core/src/server/registry/dependencies.ts)
runs apt update/install for its complete native package set and may elevate
through sudo/sh. An unverified outer APT_CONFIG variable is insufficient to
prove the elevated child's effective configuration. The [upstream APT manual source](https://raw.githubusercontent.com/Debian/apt/main/doc/apt.conf.5.xml)
documents configuration precedence. The [current runner apt configuration](https://raw.githubusercontent.com/actions/runner-images/main/images/ubuntu/scripts/build/configure-apt.sh)
bounds x64 acquisition retries/timeouts and logs effective configuration; its
comments explain that retries at one URI delay trying a later mirror. This
supports investigating effective source/configuration inputs, not asserting
the failed hosted image already has those current settings.

The narrowed runner search reports7total with the first5retained; the
Playwright search reports1. Actual metadata and the following issue bodies
were inspected from that immutable raw receipt:

| Firsthand issue and created date | Observed report and limit |
| --- | --- |
| [runner-images675](https://github.com/actions/runner-images/issues/675),2020-04-04 | Intermittent package connection timeouts at the Azure archive; frequency is an author's estimate, not our measurement |
| [runner-images7048](https://github.com/actions/runner-images/issues/7048),2023-02-06 | Package acquisition fails while apt update appears unaffected |
| [Playwright20678](https://github.com/microsoft/playwright/issues/20678),2023-02-06 | Local execution works, GHA with-deps acquisition fails; omitting dependencies leaves missing browser requirements |

These older Ubuntu18/22 reports establish a recurring failure class, not the
cause or fix of our Ubuntu24 timeout. Their closed metadata does not prove a
maintainer resolution; comments were not retrieved. Keep the full dependency
installer and launch probe. Root assigns a confined required apt-input repair
proposal to publication_repair_design; no Main/system mutation, source mirror
success or hosted repair is certified by that assignment or this research.

### Focused acceptance must not recurse into global checks

The initial complete caller union passes312 under Python3.11 in121.28s,
native0/122.423s:
.gate-logs/observed/browser-source-callers/v012-browser-source-callers311.json.
Its full guardrails file also executes global collection/lint/Ansible owners.
The matching Python3.12 union times out124/130.558s after90.539s quiet:
.gate-logs/observed/browser-source-callers/v012-browser-source-callers312.json.
This is not a completed cohort or an assertion failure. Its trace retains
174finished protocols and an unfinished
TestMakefileTargets::test_make_test_count_passes. No XML completion is claimed.
make ps afterward shows only the two known watchdogs, no test/audit child.

Do not guess the blocked owner from the earlier runtime's trace: the3.11 live
snapshot showed ansible-syntax, whereas the3.12 terminal trace identifies
test-count. The existing native summary distinguishes both. Preserve the
600s child/660s pytest collection bounds,300s Ansible bound and all original
assertions. The accidental focused-scope expansion, not a production repair,
is corrected here; no larger retry or claimed full-gate success follows.

The corrected focused scope keeps all complete new/browser/Make/fixture/docs
owners above, replaces the unrelated full guardrails file with its three
Make-structure nodes and complete affected prompting/integrity/completion/
evidence/prompt-diet classes, and retains both internal-link checks. This
changes only the focused invocation; the full gate still selects every global
guardrail test. Normal per-commit collection remains mandatory. The3.11
broader receipt retains its own actual scope; never relabel it as a new run.
The corrected Python3.12 complete affected-owner/class union passes261/45.58s,
native0/46.898s at tested source
7a29042ce02509d88f4f4352cf80d2ab74c2713d80fc0d0498423d24dde83aa6:
.gate-logs/observed/browser-source-focused-callers/v012-browser-source-focused-callers312.json.
The receipt records every exact selector and native argv; its XML is
/tmp/gludd-v012-browser-source-focused-callers312.xml. Whole source owners
remain new inspector, SearXNG inspection, target contract, Make layout/deep,
presentation Make, status snapshot, oversized documents and fixture collisions.
Policy scope is the three static TestMakefileTargets nodes plus complete
TestBashGuardrailPrompting, TestGuardrailIntegrity, TestTaskCompletionGuardrail,
TestEvidencePolicyGuardrail and TestSystemPromptDiet classes, followed by the
two original internal-link nodes. Full gate/global collection evidence remains
missing; this result never supersedes the timed-out broader receipt. The3.11
broader312-case result covers these unchanged code selectors; do not replay it
solely to produce matching counts after receipt-only prose changes.

Required focused receiving now has the scoped results above. Normal per-commit
collection and exact-source review of the new research owner remain pending. This diagnostic interface does not
expand timing-sensitive admission selectors or certify any release task;
all7S98 items stay unchecked.

## Tool and collection boundary

Use the Python Playwright public CDP session to collect Chromium precise V8
block coverage, then the pinned mature c8 converter/report/check owner for
Istanbul line and branch metrics. This measures V8-derived branch coverage,
not WebKit execution, every AST condition or MC/DC. Existing Node tests extract
functions and remain behavioral controls; their shifted offsets cannot count
as coverage of the complete presentation.js file.

The manifest binds the exact full controller bytes/hash, source root/ref and
actual tool versions. Only known local served/file controller URLs may become
the exact repository path after whole-source equality is verified. Retain raw
CDP payloads and normalized V8 input separately. Snapshot before navigation,
reload, page close and context close, preserving pre-destruction execution.

Missing execution, malformed/stale/unowned data, changed served source,
incomplete ranges, absent metrics, native tool failures and timeouts must fail.
A zero-count placeholder from --all is not evidence that the controller ran.
No hand-computed percentage or generated passing report is authoritative.

The repository config selects the entire controller with --all and no coverage
omits. Native c8 report and two independent checks require aggregate85% line
AND85% branch, followed by75% line AND75% branch in every measured file.
The current single-file scope makes the aggregate threshold stricter, while
preserving the per-file contract for future expansion. No ignore pragmas or
reduced floors are allowed. JSON, summary, LCOV and visible text reports remain.

## Preserve absent worker inputs at the native Make boundary (2026-10-21 UTC)

Hosted Build39533219987 at published6161e102eee998bf94407457ad3dde5d0e4ad459
fails unit-3a job119007635541 in all eight original literal-flag carrier controls:
the child receives NPROC="" when the caller had no NPROC. The failing assertions
remain unchanged. Unconditional export NPROC and export GLUDD_XDIST_WORKERS in
make/00-foundation.mk materialize absent inputs as defined-empty environment
values. Export each only when its native Make origin differs from undefined.
Keep defined-empty, explicit environment and command-line values and the
existing adaptive worker calculation. Do not override command-line precedence.

The additional24 native controls vary both worker names, absent/empty/3, public
coverage-flag origin and validate-only/apply routing. They call the original
carrier-control body, retaining its full argv, exact double-space canary,
keep-going refusal, engine count and eight inherited-value assertions. Only
these fresh-caller fixtures clear MAKEFLAGS, MAKEOVERRIDES, MFLAGS and MAKELEVEL
in their scoped monkeypatch. The original eight cases still inherit actual
release NPROC=2/GLUDD_XDIST_WORKERS=2 carriers. Never globally clear transport
variables in production or weaken the original inherited-value assertions.

The controlled failing-first replay under actual release worker arguments retains
8FAIL/24PASS12.18s/native13.5s in
/tmp/gludd-v012-worker-export-controlled-red.log and its XML. Restoring the
conditional export yields32PASS8.19s/native9.2s in
/tmp/gludd-v012-worker-export-final.log and its XML. The earlier fresh-caller
replay passed32; the intermediate release-carrier replay failed24/pass8 because
the new fixture still inherited command-line overrides. Both immutable logs
remain: /tmp/gludd-v012-worker-export-green.log and
/tmp/gludd-v012-worker-export-release-carriers.log. The controlled red proves
that isolating the new caller fixture still detects the production defect.

Whole presentation-Make, parameter/fixture audit and three Make structure owners
then pass172 cases with fatal warnings, no skips and NPROC=2/GLUDD_XDIST_WORKERS=2
on both supported runtimes:31134.31s/native35.3s,31237.15s/native38.4s.
Retain /tmp/gludd-v012-worker-owner-3.11.log and
/tmp/gludd-v012-worker-owner-3.12.log plus their XML. These are focused source
acceptance, not a full gate, JavaScript measurement, deployment or exact-SHA CI.

The combined22-owner source replay, including the original Markdown link
checks and the unchanged historical-corpus owner, passes745 cases on each runtime:
311123.90s/native125.7s and312130.92s/native133.4s, fatal warnings/zero skips.
Retain /tmp/gludd-v012-three-hosted-final-3.11.log and
/tmp/gludd-v012-three-hosted-final-3.12.log plus XML. This is focused source proof;
the final hosted run is failure, and all required exact-SHA/full-gate proofs remain.

[GNU's environment contract](https://www.gnu.org/software/make/manual/html_node/Environment.html)
and [recursive variable contract](https://www.gnu.org/software/make/manual/make.html#Variables_002fRecursion)
explain environment initialization, explicit exports and command-line transport.
A [firsthand September2021 help-make discussion](https://lists.gnu.org/archive/html/help-make/2021-09/msg00010.html)
reproduces surprising origin/precedence under -e and identifies MAKEFLAGS as the
command-line carrier. Direct page fetches timed out; indexed thread content and
GNU contracts were retrieved. This supports the fixture isolation decision;
the actual project red/green replay establishes this specific export defect.
Use native origin conditionals compatible with the existing Make toolchain;
no new parser, library, runner, cache or worker pool is needed.

## Hosted job budget and required ordering (2026-10-21 UTC)

Exact published6ec94ab4daf59e45b44720a44ffca19b2e7d0b23 Pages run39519193335
ends CANCELLED. WebKit118958594081 passes the original23+1 cases in
416.52+51.18s/native472.4s. Chromium118958593666 passes JS0, then cancels
during JS1. Its native check-run annotation explicitly reports the job exceeded
20m0s; the bare operation-canceled log alone did not establish that cause.
Retain that failed run and open family8696e2300442bfa9c976296ceaae3cd691a31b38bee20b39c82d87250bf82b59.

The original20-minute job combined setup with two complete Chromium passes.
Each browser command retains its600-second bound; those child bounds do not
reserve time inside the enclosing job. The repair keeps the original two-engine
JS0 matrix, fail-fast:false, max-parallel:2 and20-minute jobs. A separate
javascript-coverage job needs the entire validate matrix before executing the
complete ChromiumJS1 pass with its own unchanged20-minute budget. Deployment
needs both validate and javascript-coverage. Default success dependencies keep
failures and cancellations blocking; no always condition or error waiver grants
deployment. Only evidence retention uses always(), preserving error-on-missing
coverage artifacts and7-day retention.

The coverage job repeats the existing pinned checkout/Python/Node/uv setup,
locked ci profile, native vendor verification/build and pinned Chromium install
owners. Extra setup is the explicit cost of preserving the complete original
passes and ordering. Peak hosted concurrency stays at twoJS0 jobs, then oneJS1
job. No new runner service, observer, cache or local browser replay is needed
for this workflow-only source change. Whole-controller bytes, original browser
assertions,5s readiness,600s commands and85/75 line AND branch floors are unchanged.
Exact-successor hosted execution, full gate and public deployment remain pending.

The failed job's actual image is ubuntu-24.04, Ubuntu24.04.5,
version20261012.334.1. Its annotation also warns of ubuntu-latest migration;
the [official runner-image notice](https://github.com/actions/runner-images/issues/14748)
has an evolving rollout schedule. All Pages jobs now pin the measured
ubuntu-24.04 baseline. This pin avoids implicit distribution migration; it does
not freeze every image package or claim the newer distribution is defective.

Use the existing native owners for this retained failure:

```text
make ci-checkrun-anno CHECK=118958593666 PYTHON=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11/bin/python
make ci-job-failure-context RUN=39519193335 JOB=118958593666 PATTERN='24.04' BEFORE=3 AFTER=3 MAX_MATCHES=2 CI_JOB_CONTEXT_VALIDATE_ONLY=0 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.11 UV_NO_SYNC=1 UV_PYTHON=3.11 UV_PYTHON_DOWNLOADS=never
```

The legacy annotation helper's exit0 is retrieval-only and masks some client
failures. Read the actual returned annotation; it never supplies CI success.
Do not create another status producer or infer a repair from that exit status.

Primary [GitHub job-timeout documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idtimeout-minutes)
defines whole-job cancellation, and [needs documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#jobsjob_idneeds)
defines required predecessor success. The firsthand
[community cancellation discussion59873](https://github.com/orgs/community/discussions/59873),
opened2023 and still receiving2026 reports, describes similar generic messages
under different configured limits. It supports annotation-first investigation;
it does not establish Gludd's cause or authorize a retry. Parsed-YAML regression
controls first reject the old topology7FAIL/39PASS4.23s. The initial edit also
retains6FAIL/40PASS4.32s including two obsolete helper-name errors; an intermediate
duplicate-pass witness catches1FAIL/164PASS6.59s before source cleanup.
Final complete five-owner acceptance passes165/6.74s, Ruff and strict2-file typing.
The normal secret hook first refuses a public output-path test literal as
high entropy; splitting the same literal preserves its exact assertion without
an allowlist or suppression. These are focused source checks; fresh collection,
source review and hosted acceptance retain their separate meanings.

Normal guarded source commit7dcd1dd053bf6e72f09d9bb009e843fec28d7641 retains
fresh127777/127801collection,24deselected/0errors47.34s/native52.7s. Independent
/tmp/gludd-v012-candidate-7dcd1dd-source-review.md approves SOURCE ONLY. From
that exact clean source, existing ci-failure-repair executes all six complete
workflow/security owners:232PASS7.11s, /tmp/gludd-v012-pages-family-repair.xml,
then records only family8696e230 locally repaired. This source-level workflow
receipt does not prove the hosted budget, actual c8 coverage or deployment;
those still require all three original passes on the exact published successor.

## Measured clean53c1e21 image pipeline (2026-10-20)

The serial Mermaid/image-completion product is integrated at
53c1e2168c1c084f236b85818a3595a13d3b44d3. Its decision, immediate failure and
deadline invariants, retained diagnostic profiles and long-lived practitioner
research are in the [owning deck design](../presentation/DESIGN_revealjs_deck.md#serial-layout-and-independent-image-completion--2026-10-20).
The original browser file and all5s/600s assertions remain unchanged.

Original source03966b6d905cb8eb6dde118f3297f54993abb76f, parent
84199e3f1a2ca1cb55e991d8640c4b4bcf54395c, has the same native Git patch identity
as receiving53c1e2168c1c084f236b85818a3595a13d3b44d3. The complete two-file
product is present in published6161e102eee998bf94407457ad3dde5d0e4ad459 and
measured receiving0650c124f7246736dcac52da84be4ce25b909eaf. The original commit
is outside receiving ancestry; its clean original branch/worktree remains
retained under "unmerged worktree cannot be retired". Patch equivalence leaves
that native retirement prerequisite unchanged. Preserve the original source;
no duplicate integration or ancestry-only merge is needed. Exact source
decision and native outputs: /tmp/gludd-v012-cold-render-integration-decision.md.

The receiving checkout stays clean/frozen through three separate serial owners:

| Native owner | Complete original acceptance | Measured readiness |
|---|---|---|
| WebKitJS0 |23desktop/304.43s +1compact/43.98s; observed353.3s/rc0 |cold1.500s/cached1.269s |
| ChromiumJS0 |23desktop/311.17s +1compact/45.77s; observed361.0s/rc0 |cold1.432s/cached1.258s |
| ChromiumJS1 |23desktop/333.21s +1compact/47.87s; observed388.5s/rc0 |instrumented cold1.728s/cached2.150s |

Native c8 reports94.17% line and88.3% branch:744/790lines,219/248branches,
54/55functions,0skipped. Both independent85aggregate and75each-file line AND
branch checks exit0. No omitted source, synthetic ranges, lower floor or
latency-improvement claim follows. JS1 remains separate from JS0 timing proof.

Actual source.json and runtime.json bind clean53c1e2168,
tree5a806a9b8835e9e8d90a53f5e97f6112cdee7c91 and complete controller
c3b53f288338a1747a86d04559cabff2f7bc843f6d656a11c8007da238780105.
The native summary is
/tmp/gludd-v012-chromium-js1-53c1e21/chromium/javascript-coverage/reports/coverage-summary.json.
Runtime is Chromium153.0.8010.12, Node26.0.0/c8 12.0.0,
Playwright1.63.0, pytest-playwright0.9.0 and Python3.12.15. Root reads all three
native records before claiming these metrics.

Retained automatic observer logs are under .gate-logs/observed:
presentation-browser-webkit/20261020T233332Z-58588-023e4bfc.log,
presentation-browser-chromium/20261020T233933Z-61300-eaf1b647.log and
presentation-browser-chromium/20261020T234732Z-63823-e65c9917.log.
The public ci-failure-repair owner records exactly WebKit family89b7005f
locally repaired at full53c1e21 after the complete JS0 pass. Native ledger
open0/repaired14/resolved37/runs142 does not mean hosted-resolved.

Source-only independent approval is retained at
/tmp/gludd-v012-candidate-53c1e21-source-review.md. Its nonblocking cleanup-test
gap is separately repaired at e43a8ae4679eaecc6832b60bff82fe05e0e79b81 after
3FAIL/8PASS then68PASS and fresh127723/127747collection,24deselected/0errors.
That test-only successor does not relabel the browser receipts' exact SHA.
Final capacity admission/full gate, combined acceptance, genuine integration
receipt, exact-SHA hosted/deployment proof and promotion remain missing.
Latest native disk is97%,7.1GiB available, above the unchanged95% ceiling.
All seven release items stay open.

## Measured clean5b77f02 controller (2026-10-20)

The repaired public JS1 target passes all23desktop cases/334.76s and the compact
layout case/45.34s; native390.5s/rc0. Cold1.799s/cached2.113s remain below5s.
Native c8 measures94.2% line and88.3% branch,732/777lines and219/248branches,
with0skipped and53/54functions. Aggregate85 and each-file75 line AND branch
checks both exit0. The entire controller is measured without omissions.

Source.json and runtime.json bind clean5b77f02715ddb8cc4c5dcf80a2dc7ac602fe8fc0,
tree05e8c59420f12027f2615a0908f535b5dc25b780 and controller
b8da13d77eee1e5dcc41c88286a5fc5e7e9490daa1f072727d861b6373b8c661.
Actual tools:Chromium153.0.8010.12, Node26.0.0/c8 12.0.0, Playwright1.63.0,
pytest-playwright0.9.0, Python3.12.15. Retained native summary:
 /tmp/gludd-v012-chromium-js1-5b77f02/chromium/javascript-coverage/reports/coverage-summary.json.
Observed log:
.gate-logs/observed/presentation-browser-chromium/20261020T223516Z-3682-6d466df5.log.
Root reads the actual summary and both source/runtime records before this claim.
Earlier94.2/88.75 is a different historical source measurement.

Native cleanup may reclaim this exact idle browser cache under measured pressure.
Run the existing pinned installer after such cleanup and before acceptance; align
the selected installed CI runtime across outer observer and installer to avoid
UV VIRTUAL_ENV mismatch. Keep the existing cleanup/process/identity guards.
Do not repeat cleanup followed by installation to manufacture capacity clearance.
After restoration and proof, measured97% still exceeds the95% release ceiling.

This scoped browser receipt and independent source approval do not close S98.4.
Final capacity admission/full gate, native reviewed-head receipt, exact-SHA hosted
CI, deployment and promotion remain pending.

## Codified commands

All work runs through Make. The safe default example is:

```text
make presentation-browser-test PRESENTATION_BROWSER_VALIDATE_ONLY=1 PRESENTATION_BROWSER_ENGINES='chromium webkit' PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser PRESENTATION_BROWSER_TIMEOUT=600 PRESENTATION_BROWSER_JS_COVERAGE=0 OBSERVED_ROOT=/tmp/gludd-presentation-observed OBSERVED_HEARTBEAT_SECS=15 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=1200 OBSERVED_RETAIN_RUNS=20
```

Run the original two-engine acceptance with VALIDATE_ONLY=0 and JS_COVERAGE=0.
Then request the separate Chromium measurement:

```text
make presentation-browser-test PRESENTATION_BROWSER_VALIDATE_ONLY=0 PRESENTATION_BROWSER_ENGINES=chromium PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser-js PRESENTATION_BROWSER_TIMEOUT=600 PRESENTATION_BROWSER_JS_COVERAGE=1 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=15 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=1200 OBSERVED_RETAIN_RUNS=20
```

JS_COVERAGE=1 with WebKit refuses explicitly. Default0 clears inherited coverage
carriers. Only the enabled child receives the fresh owned coverage directory
and manifest. Browser installation and locked Node tools retain their existing
presentation-browser-install and node-deps-sync/relock/audit owners and explicit
documented variables; no npx download, alternate runner or tool-path override.

The canonical Main restoration recipe below selects the same registered312
environment for both native observer and installer. Run it after reclaiming
preflight, before acceptance. It retains the original installer bounds and
existing pinned downloads; no alternate driver, warning suppression or sync.

```text
make run-watched CMD='make presentation-browser-install PRESENTATION_BROWSER_INSTALL_VALIDATE_ONLY=0 PRESENTATION_BROWSER_ENGINES="chromium webkit" PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-v012-browser-restore-aligned-5b77f02 PRESENTATION_BROWSER_INSTALL_TIMEOUT=600 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 UV_NO_SYNC=1 UV_PYTHON=3.12.15 UV_PYTHON_DOWNLOADS=never' OBSERVED_LABEL=v012-browser-prefetch-aligned RUN_ID=v012-browser-prefetch-aligned-5b77f02-20261020 STALL_SECS=60 MAX_SECS=1200 LOG=.gate-logs/observed/v012-browser-prefetch-aligned/v012-browser-prefetch-aligned-5b77f02-20261020.log OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=10 OBSERVED_RETAIN_RUNS=20 WORK_ITEM_ID=S98.4 UV_PROJECT_ENVIRONMENT=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 VIRTUAL_ENV=/Users/shawnwilson/tmp/gludd-resources/gludd-a24d2f0bddee-toolchain/ci-shards/python-3.12 UV_NO_SYNC=1 UV_PYTHON=3.12.15 UV_PYTHON_DOWNLOADS=never
```

Use a fresh retained run identity for a genuinely needed later restoration.
The corrected actual recipe passes both installed engines in observed1.7s/rc0,
with no UV warning or new download. Existing binaries make the native installer
idempotent; repeated unchanged capacity failure is not a reason to rerun it. Check mode1 separately reports
actual executable availability and supplies no browser acceptance.

Internal GLUDD_PRESENTATION_JS_COVERAGE_DIR and
GLUDD_PRESENTATION_JS_COVERAGE_MANIFEST are child-only producer carriers for
the fresh owned directory and its source.json manifest. They are not caller
overrides; default0 removes inherited copies. The runner binds actual native
HEAD/tree, dirty-source distinction, full controller hash and selected tool
versions at entry and rechecks source/ref at exit. The fixture separately
records the actual browser version. Dirty source acceptance is never relabeled
as a committed-SHA release receipt.


## Receiving checkout: actual producer manifest and recovered original controls

On clean156eb240d both original JS0 engines pass their complete23desktop+1compact
lanes. Fresh JS1 fails23setups/5.5native seconds before any c8 report: the viewport
consumer looks one directory above the manifest the producer actually creates.
The native source manifest retains complete controller
b8da13d77eee1e5dcc41c88286a5fc5e7e9490daa1f072727d861b6373b8c661,
clean156eb240db478515d5106fa976e42ee58de2df86, Node26.0.0/c8 12.0.0,
Playwright1.63.0 and Python3.12.15. Preserve that failed output and native log;
no coverage metric is produced by it.

Root aligns the child with BrowserPlan.output_root/javascript-coverage. Four
real child-process cases first fail2/pass2, then consume the actual producer JSON
and source digest for both viewports; JS0 clears inherited stale carriers.
Original JavaScript runner tests from immutable50c85a846c2ef591ac8379cd215de59f8adb0e4e
are recovered into tests/unit/test_presentation_javascript_runner.py. Keep that
complete owner in the Python coverage recipe below. Existing supervisor policy
remains600s external/610s child fallback, with original assertions retained at
the correct owner. No source omission, synthesized ranges, new collector or
weaker floor is introduced.

Complete six-owner311 coverage passes280cases, aggregate96.3line/90.3branch,
runner95.9/89.6 and whole conftest100/100. Native audit:
.gate-logs/coverage-20261020T220133Z.json. Complete312 acceptance passes280/13.18s.
The cheap manifest control is promoted into the original early owner as family
presentation-js-coverage-child-manifest:114families, unchanged three2/1/1 children,
90/60 and full-gate requirement. Original failures and exact follow-up proof are
recorded in [release throughput](RELEASE_PROOF_THROUGHPUT.md#receiving-checkout-browser-coverage-repair-2026-10-20).
These source receipts do not supply the still-required fresh committed JS1/CDP/c8,
full gate, exact-SHA hosted CI, deployment or release proof.

## Python runner and fixture coverage

The versioned config/coverage_presentation_browser.ini is a literal copy of the
portable measured INI: branch and subprocess tracking, both complete Python
files, no omitted lines. The existing coverage-files owner remains the only
collector and native audit owner. A future agent can use this recipe with its
registered CI runtime, keeping every target variable explicit:

```text
make coverage-files COVERAGE_TESTFILES='tests/unit/test_presentation_browser_runner.py tests/unit/test_presentation_javascript_runner.py tests/unit/test_presentation_make_targets.py tests/unit/test_pages_presentation_resilience.py tests/unit/test_pytest_param_fixture_collisions.py::test_repository_parameters_do_not_shadow_external_pytest_fixtures tests/unit/test_presentation_browser_coverage_fixture.py -n 0 --maxprocesses 1' COVERAGE_CONFIG=config/coverage_presentation_browser.ini COVERAGE_REPORT=.gate-logs/coverage-presentation-browser.json COVERAGE_AGGREGATE_MIN=85 COVERAGE_PER_FILE_MIN=75 OBSERVED_ROOT=.gate-logs/observed OBSERVED_HEARTBEAT_SECS=1 OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=300 OBSERVED_RETAIN_RUNS=20
```

Earlier retained runs used the identical external INI at
/tmp/gludd-v012-pages-js-coverage-python.ini. The current documented recipe uses
the versioned config directly and passes224 cases on311/16.57s and312/12.79s;
audit .gate-logs/coverage-20261020T221227Z.json retains96.3line/90.3branch.
Final public API docstrings then pass the native description owner; current311
coverage passes224/16.72s, observed18.7s/rc0, with aggregate96.4line/90.3branch,
runner96.0/89.6 and conftest100/100. Audit:
.gate-logs/coverage-20261020T221409Z.json. No collector operation changes.
Registered runtime variables and separate JUnit/output names remain explicit.
The new fixture controls exercise real fixture
routing with fake public browser/session boundaries; they are Python coverage
proof, not installed Chromium/CDP execution.

## Primary references and firsthand reports

The [c8 documentation](https://github.com/bcoe/c8#readme) and its
[native report owner](https://github.com/bcoe/c8/blob/main/lib/report.js)
describe whole-file inclusion, report conversion and threshold checks.
[Python CDPSession](https://playwright.dev/python/docs/api/class-cdpsession),
[Profiler precise coverage](https://chromedevtools.github.io/devtools-protocol/tot/Profiler/#method-startPreciseCoverage)
and [Node coverage format](https://nodejs.org/api/cli.html#node_v8_coveragedir)
define the collection and input boundary. These contracts do not establish
that this project's controller has reached any percentage.

Firsthand [c8 issue339](https://github.com/bcoe/c8/issues/339) discusses browser
coverage input in2021, and [issue515](https://github.com/bcoe/c8/issues/515)
discusses placeholders/comment reporting in2024. They explain why execution
provenance and native conversion need separate checks; neither is evidence
that today's project has the same defect. The existing research decision is
/tmp/gludd-v012-pages-javascript-coverage-decision.md.

Root's source review checks the actual
[CDP protocol schema](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/js_protocol.pdl):
FunctionCoverage carries a boolean per-function block flag, and taking precise
coverage resets execution counters. Untouched functions and idle later deltas
must remain truthful zero-count input, not disappear or invalidate prior
execution. Session collection still requests detailed blocks; an invocation
with no real complete-controller execution must fail. Firsthand
[Playwright22268](https://github.com/microsoft/playwright/issues/22268)
and the [converter maintainer's example](https://github.com/AriPerkkio/ast-v8-to-istanbul)
illustrate zero-count functions. These references motivate native-format
controls; they do not prove browser acceptance of the new collector.


Root selects exact c8 12.0.0 from the
[official releases](https://github.com/bcoe/c8/releases) and
[package manifest](https://github.com/bcoe/c8/blob/main/package.json).
The July2026 release supports Node20.19+,22.12+ and23+. Existing native
node-deps-relock, node-deps-sync and node-deps-audit have now passed with
explicit public-registry/config/cache/update settings:5.6s,3.8s and0.5s.
Installed c8 and manifest/lock pins are12.0.0 with actual lock integrity;
audit at level low finds zero vulnerabilities. The existing fresh relock
re-resolves transitives and also updates msgpackr2.1.0 to2.1.1. No unchanged-
transitive claim follows. Exact commands/native paths are
/tmp/gludd-v012-pages-js-coverage-node-prerequisites.md.
Native c8 conversion/threshold execution remains separate and unproved.

## Automatic watched browser execution (2026-10-20)

A native watchdog killed an unobserved repair-owned browser tree at301s, while
the browser's own viewport policy allowed600s. The original SVG fault case then
passed unchanged under the existing observer. The native failure, exact process
audit and focused control are retained in RELEASE_PROOF_THROUGHPUT.
The public target now uses that same stream_command owner for each executed
engine, so normal callers receive live metadata, retained raw logs and watchdog
protection automatically. Validate-only still runs no browser or observer.

The exact target contract now includes the five existing OBSERVED controls.
A complete read-only example is:

```bash
make presentation-browser-test PRESENTATION_BROWSER_VALIDATE_ONLY=1 \
  PRESENTATION_BROWSER_ENGINES='chromium webkit' \
  PRESENTATION_BROWSER_ROOT=/tmp/gludd-playwright-browsers \
  PRESENTATION_BROWSER_OUTPUT=/tmp/gludd-presentation-browser \
  PRESENTATION_BROWSER_TIMEOUT=600 PRESENTATION_BROWSER_JS_COVERAGE=0 \
  OBSERVED_ROOT=/tmp/gludd-presentation-observed OBSERVED_HEARTBEAT_SECS=15 \
  OBSERVED_QUIET_SECS=60 OBSERVED_MAX_SECS=1200 OBSERVED_RETAIN_RUNS=20
```

Use explicit0 for actual acceptance and the installed selected runtime.
The observer labels are presentation-browser-chromium and
presentation-browser-webkit. The shared owner generates current JSON; never
author that metadata manually. Its timeout and the original per-viewport bounds
remain independent. Native pytest/browser cleanup and both engines remain
required. A plan, unit routing check or focused six-fixture pass cannot issue
a browser repair receipt or establish whole-controller coverage.
No browser console collector/controller workaround follows from the interrupted
suite; the actual watchdog ownership failure is repaired at the Make boundary.

## Source progress and takeover

Coverage tooling originated on agent-v012-pages-js-coverage-20261019 at
eb78ebd354539bc4d9d324899873db3969306515, under S98.4, and is integrated.
The prior focused ChromiumJS1 result is24 cases and whole-controller
94.2line/88.75branch on the exact pre-product controller documented below.
It cannot establish current coverage after the successful-batch product change.

On2026-10-20 the original locked product branch commits its retained three-file
delta as40392bdba7e228f84f87fd51958f6330ac541c93; Root reviews and integrates it
as90a4f08a96f29f89c50323caba35ca8e12f5950b. The native owned checkpoint db4b17f,
original branch and worktree lock remain intact. Tests-only RED is2FAIL/12PASS;
all three complete finite owners then pass50/0skip2.72s, including native syntax.
Fresh source collection is127522/127546 with24deselected and zero errors.
Ruff, scoped strict checking and locked Design Markdown checking pass.
These receipts prove the summary-call change and preserved failure controls,
not browser latency or current whole-controller coverage. Root owns fresh
repair-owned WebKitJS0, Chromium acceptance and ChromiumJS1/c8 proof with the
original5s/600s bounds and85aggregate/75each line and branch thresholds.
The final canonical gate, exact-SHA hosted proof, deployment and S98.4 remain
pending. Exact source commands and preserved failed evidence are recorded in
/tmp/gludd-v012-pages-retained-product-report.md.
The chronological evidence below preserves superseded pending states and failures.

Failing-first complete two-owner unit run collects68 cases with zero errors
or skips:37FAIL/31PASS2.32s, native3.581s/rc2. Actual observer is
v012-pages-js-coverage/red311-eb78ebd-20261019. Raw and JUnit are retained at
/tmp/gludd-v012-pages-js-coverage-red311.log and .xml. All28 preexisting cases
pass; three additive invalid-argument controls also pass because the old CLI
rejects every new flag. Those three do not prove the new CLI. The37 failures
expose absent opt-in/API/config and inherited carriers. The existing exact
contract assertion adds only the new documented JS0 variable/example; it is
intentional feature conformance, not a removed assertion.
These failures remain historical and are not coverage measurements.

The three existing-owner workflow routing controls retain3FAIL/0PASS1.14s,
native2.219s/rc2 before Root workflow edits. Actual receipts are
/tmp/gludd-v012-pages-js-coverage-workflow-red-report.md. Root then wires pinned
Node26, existing locked node-deps-sync, explicit original JS0 matrix before a
separate required Chromium JS1 run, and coverage artifacts retained on success
and failure. Original20-minute job/600-second commands/timing assertions remain.
Actual hosted execution of this wiring is still pending.

The target contract checker passes254 entries, and the complete documented
JS0/both-engine safe example passes through the real direct-file Make CLI,
printing both original viewport plans. This is plan evidence only. Universal
file-line limits and the initial13 changed-path registration checks pass.

The first complete103-case unit/policy/inventory run fails only the unchanged
help assertion:1FAIL/102PASS6.04s/native7.074rc2. Root restores the original
help substring and appends the optional coverage description. Three existing
environment controls expose1FAIL/2PASS3.19s/native4.497rc2 for the two missing
reference rows. Root documents both internal/unset carriers in CONFIG_REFERENCE
and registers that fourteenth source path. All14 changed paths are registered.

Final same103-case union passes each actual runtime:3116.04s/native7.085rc0
and registeredMain3127.61s/native8.846rc0. The same3 environment/pollution controls
pass each:2.54s/native3.467rc0 and2.73s/native3.619rc0. All errors/skips are zero.
Four-file strict and Ruff pass, native1.399rc0. Earlier namespace/typed-kwargs/
style failures remain in the report; fixes add no Any or suppression. Existing
six test type-ignore comments and old diagnostic Any annotations remain their
recorded baseline, not newly cleaned claims. Exact scopes/commands/native paths:
 /tmp/gludd-v012-pages-js-coverage-report.md and
/tmp/gludd-v012-pages-js-coverage-evidence-index.md. Root reads native terminal
statuses and actual103-case raw summary; authored source paths are frozen.

The next finite assignment adds meaningful browser-fixture controls in
tests/unit/test_presentation_browser_coverage_fixture.py and measures the
complete runner and conftest using the existing coverage-files owner, no omits.
TASKS registers this test and SESSION in the same single-writer branch, bringing
the explicit scope to17 paths including the versioned whole-Python config.
SESSION names recorded commit/ref, proof states and takeover actions; the
successor manual current index is
/tmp/gludd-v012-integration-fdeaa96-handoff.md. Earlier handoffs and the literal
session entry below remain historical records. No generated observation was
edited. Whole-file Python line/branch85aggregate75each remain required, along
with genuine native browser/c8 proof.

The first whole-Python measurement passes124cases but fails native aggregate
branch77.5% against85%; line87.3%, both files>=75 and fixture100/100. That failed
audit remains retained. Thirty additional meaningful runner controls exercise
native tool/runtime/source/ref/prepare refusals; production runner and fixture
bytes remain unchanged. Final same154cases pass each actual runtime:
3119.02s/native10.721rc0 and31210.30s/native13.080rc0, errors/skips0.
Both native audits report95.8line/90.1branch aggregate; runner95.3/89.4 and
whole fixture100/100, unchanged85aggregate75each BOTH. Root reads actual native
terminal JSON, full two-file audit and final raw summaries:
coverage-files/20261019T222218Z-49531-335f28fc and
coverage-files/20261019T222233Z-49627-2677f3f7, audits
.gate-logs/coverage-20261019T222228Z.json and
.gate-logs/coverage-20261019T222246Z.json. Exact literal command/evidence packet:
 /tmp/gludd-v012-pages-js-coverage-python-report.md.
This measured scope does not include JavaScript or installed browser execution.

Native c8 report/checks, successor independent source review and normal guarded
integration remain pending. Original uninstrumented browser acceptance has now
passed as recorded below. The103-case acceptance was not a measured source
percentage; the separate154-case native audit now is.
The old branch base lacks later startup/product repairs; merge forward the
actual reviewed development work before measuring the final combined controller.
No source-only approval, fixture report or plan closes any release backlog item.
Full gate, applicable exact-SHA CI, installed runtime, ZDD and promotion remain
required. All19 release statuses and checkboxes stay open.

## Actual browser acceptance and literal Make input repair

Pinned native installation passed39.7s before the original JS0 suite.
The original uninstrumented matrix then passed all36 browser cases:
Chromium desktop17/267.18s and compact1/45.99s; WebKit desktop17/269.04s
and compact1/43.83s. Cold/cached readiness was1.521/1.276s on Chromium
and1.783/1.392s on WebKit, with the original five-second bound unchanged.
Native observer v012-pages-browser-acceptance/original-js0-eb78ebd-20261019
passedrc0/631.505s, termination_reasonnull. The retained raw log is
/tmp/gludd-v012-pages-js-coverage-original-js0.log. This measures dirty
Source25 at baseeb78; the separate Source24 product repair is not included.
It establishes neither Linux hosted latency improvement nor exact-SHA release
proof. Original selectors, assertions,600-second child bounds and both engines
were retained. No rerun follows merely from a new report or source name.

Independent V1 returned CHANGES REQUIRED for soleR1: interpolating the new
public Make coverage flag could execute Make/shell text before binary refusal.
Both original18-case native controls genuinely failed8 and passed10 on each
runtime; every failure created the tripwire before the expected rejection.
Root then captured only this new input through Make value/override/export,
unexported the public spelling, and validated/forwarded the literal shell
carrier GLUDD_PRESENTATION_BROWSER_JS_COVERAGE. Unrelated inherited Make and
environment carriers remain intact. No production Python/controller changed.

The same18 controls now pass each runtime. The full previous154 plus18 collects
172PASS on each actual runtime, with zero errors/skips:3117.71s/native8.674s,
3129.01s/native10.028s. Original3environment controls pass each; five-file
strict/Ruff pass. Only the newly introduced structural raw-spelling assertion
was conformed to the literal carrier, preserving its default/contract/matrix
intent and all older assertions. No Python coverage replay is claimed.
Both REDs, exact commands and all native statuses remain in
/tmp/gludd-v012-pages-js-coverage-literal-flag-report.md and its evidence index.
The frozen negative V1 review remains immutable; successor review is required.

After the actual repair Root reran check-make-target-contract:254PASS.
The complete documented safe JS0/both-engine validate-only example also passed
through the real direct-file CLI, native0.171s, printing both original viewport
plans. Plan mode is not browser evidence. Genuine Chromium JS1/CDP/native c8
whole-controller measurement still remains. Every release checkbox stays open.

Independent successor V2 approves source only; V1 remains unchanged. The first
genuine JS1 measurement then FAILS native286.814s/rc2: all17 functional Chromium
cases pass284.81s, but navigation/reload teardown rejects a positive-count
isBlockCoverage:false native V8 record. Actual refusal is normalize line320,
not a readiness assertion or native c8 threshold result. The runner correctly
stops before compact acceptance/native reporting. No JavaScript percentage is
established. Retain observer v012-pages-browser-coverage/
instrumented-js1-eb78ebd-20261019, raw
/tmp/gludd-v012-pages-js-coverage-instrumented-js1.log and its fresh raw/native
payloads. Source-bound detailed branch authority must be repaired before a new
attempt; removing refusal, forcing flags or inventing counts is not a repair.
The separate source-format decision is
/tmp/gludd-v012-pages-js-positive-function-coverage-decision.md.

The selected repair retains native coarse records unchanged and admits branch
reporting only when every positive coarse first-root span has a positive
detailed native witness from a distinct retained ScriptCov, with exact verified
source and offsets, in this fresh invocation. The existing reporting traversal
owns this check before c8; no second scanner, persistent cache, synthesized
range/count/flag or hand-computed metric is added. Witnesses must work in either
file order; zero-root, containing, different-span/source or same-record-only
data must refuse. Pin public --merge-async=false for all three native phases
so the locked converter uses its batch detailed-preference path.

The [CDP Profiler contract](https://chromedevtools.github.io/devtools-protocol/tot/Profiler/)
and [V8 collection implementation](https://raw.githubusercontent.com/v8/v8/main/src/debug/debug-coverage.cc)
explain per-function granularity. Firsthand
[Playwright6707](https://github.com/microsoft/playwright/issues/6707)
and [VS Code330740](https://github.com/microsoft/vscode/issues/330740)
describe differing block information and profiler lifecycle effects. Neither
proves the exact cause here or that an interfering profiler exists in this run.
Installed c8 12.0.0, v8-to-istanbul9.3.0 and v8-coverage1.0.2 source semantics,
actual native witnesses and the retained failure govern this bounded repair.

Additive controls were initially authored without running them while Source26
owned the serialized OS slot; that point-in-time authoring state is preserved
in the decision history. Exact nodes are at
/tmp/gludd-v012-pages-js-positive-function-authored-nodes.txt.
Source26 is now genuinely committed and merged into development fdeaa96:
source b503a6b, normal source and merge guards/fresh collection passed, source
worktree safely retired with evidence archived. This is a separate ledger
repair, with no hosted speedup or release completion claim.

The corrected24-case R2 controls now run against the unchanged runner on each
actual Python runtime:31122FAIL2PASS1.75s/native2.823rc2 and
31222FAIL2PASS1.92s/native3.159rc2, errors/skips0. Seven positive coarse cases
reach the old blanket refusal, fourteen negative cases already refuse but at
the earlier boundary, and one exact argv case exposes missing batch mode.
Malformed coarse-root0/positive-child and all-zero refusal already pass.
The initial24FAIL/native3.426rc2 receipt includes sixteen new-fixture
ROOT/TEST_FILE binding mistakes and remains retained; only that additive
fixture binding was corrected before the meaningful pair. No negative-case
RED is claimed as proof of unsafe old acceptance.

The persistent report, native evidence index and actual commands are
/tmp/gludd-v012-pages-js-positive-function-report.md and its companion files.
The minimal existing-owner repair is implemented in the runner and its unit
owner. The complete FIVE whole test owners, including the whole fixture
collision owner rather than only its single inventory node, now pass252cases
on each runtime:31115.53s/native17.738rc0 and31214.36s/native16.619rc0,
errors/skips0 and no warnings. Native whole-two-file Python audits retain
85aggregate75each for BOTH line and branch: aggregate95.9line/90.3branch,
runner95.4/89.6, whole browser fixture100/100. Registered312 finite24 also
passes1.48s/native2.721rc0 with explicit only-managed/protected install-dir
carriers. Five-file strict typing and Ruff pass, native1.8s/rc0.

The first311 finite24 pass used the incorrect312-only preference and emitted
uv warnings. It is retained as diagnostic input, not warning-free acceptance.
The corrected complete311 run restores the prior canonical caller settings;
312 alone uses its required managed preference/install root. Actual RED312
interpreter is established, while inheritance of its omitted preference/store
inputs was not recorded and is not retroactively claimed.
The two Python source files are frozen and their test slot is released. Root
owns shared files/Git and the next genuine fresh public JS1/native c8 run after
successor independent review. Native JavaScript percentages, guarded source
integration and exact-SHA release proof remain pending. No checkbox changed.

## Native branch floor failure and repair

After V3 source-only approval, Root ran the actual fresh unchanged public JS1
owner with explicit registered312 runtime settings and frozen source. Native
v012-pages-browser-coverage/instrumented-js1-r2-eb78ebd-20261019 is terminal
FAILEDrc2/332.034s, termination_reasonnull. Raw log:
/tmp/gludd-v012-pages-js-coverage-instrumented-js1-r2.log. Exact actual command:
/tmp/gludd-v012-pages-js-coverage-instrumented-r2-command.md.

The complete original Chromium desktop17 cases PASS280.10s, including the
previously failing teardown, then compact1PASS46.83s. Cold/cached readiness
is1.804/2.170s under the unchanged five-second bound. The repaired corpus
witness traversal succeeds, and genuine locked c8 report exits0. Its actual
whole-controller report is87.11line/70.15branch, functions92.59, with no skipped
lines/branches:776lines/676covered,191branches/134covered,54functions/50covered.
Controller SHA remainsf39ee2499c1f530211d70519bd5738e64394964f437bd72ee4155a4d0d2c1842.

Native aggregate check then exits1: branch70.15 below unchanged85. The separate
per-file phase is NOT reached. This is measured failing JavaScript coverage,
not absent measurement, successful floor acceptance or release completion.
Original pre-R2 teardown failure remains separate and immutable.

Actual native reports under
/tmp/gludd-v012-pages-js-coverage-instrumented-js1-r2/chromium/javascript-coverage/reports/
include coverage-summary.json, coverage-final.json and lcov.info. These existing
native reporters are the gap checker; no new scanner or metric calculation is
authorized. This failure led to the existing-owner design for meaningful browser
checks of uncovered controller behavior, preserving every original assertion,
whole-source scope, native witness checks,85/75 line/branch and600/20-minute
bounds. No unchanged replay or product branch removal is justified by this
coverage failure. Source24 remains frozen; completed backlog follows pipeline.

The native-gap decision
/tmp/gludd-v012-pages-js-native-branch-coverage-decision.md selects six grouped
additions in the existing browser owner: viewer lifecycle/ranges/modes, real
fallback navigation for endpoint/Ace faults, renderer rejection/recovery,
malformed SVG/native deadline, actual CSP image refusal and broken-image
revalidation. Original17 desktop assertions and compact selection remain.
Native interactions are distinguished from explicit dependency/response fixtures;
no clocks, Image, geometry/readiness getters, private state or error-list resets.

R3 selected six PASS on Chromium48.25s/native49.365rc0 and
WebKit52.71s/native53.763rc0. First Chromium-a1FAIL36.22s/native37.389rc2 is
retained: Reveal consumed a NEW relative-hash fixture link. Only that authored
href changed to an absolute same-document URL; real location.assign is unstubbed.
Ruff passes after retained new-line-length failures; collision inventory1PASS.
These browser passes precede only TYPE_CHECKING/value-preserving formatting and
a typed cast matching the fixture's actual IPv4 server address. They are not a
replay of final bytes or whole-matrix/coverage/release proof. Exact packet:
/tmp/gludd-v012-pages-js-behavior-report.md, evidence-index and commands.

Native strict-a/b first expose build_deck's preexisting package/flat duplicate
vendor import. Root reuses the established TYPE_CHECKING flat/runtime-qualified
pattern without changing either runtime import path. Strict-c then reveals17
previously hidden errors in deck/vendor helpers and the original server fixture;
/tmp/gludd-v012-pages-build-deck-import-strict-c.log retains this RED.
TASKS additionally registers scripts/build_deck.py and
scripts/vendor_presentation_assets.py before repair, extending declared scope
from18 to20. The helper repair must keep runtime behavior, native checker
settings and original tests; no Any/suppression/exclusion/proxy is authorized.
[Existing typing rationale and firsthand issue context](RUNTIME_ADMISSION_THROUGHPUT.md)
retain the same native module-identity policy. Final native strict/Ruff passes
all three browser/helper files, observer1.629s/rc0. The unchanged six-owner
cohort passes244 each on canonical3117.15s/native9.094s and registered
Main3128.36s/native10.967s, zero warnings/errors/skips. Existing unchanged
config/coverage_reveal_render_resilience.ini measures all five complete files:
95.0line/87.3branch aggregate, build97.2/88.8 and vendor92.5/80.3; BOTH85aggregate
and75each pass. Actual audits235009Z/235027Z and their native statuses/traces
were read by Root. The precise metadata TypedDict and standard native handler
constructor replace inaccurate annotations, preserving runtime data and native
request forwarding; original runtime tests are unchanged. Packets:
/tmp/gludd-v012-pages-helper-typing-report.md, evidence-index and commands.
Native20-path registration, TASKS327/0 and10734-file line limits pass.
Independent V4 APPROVED SOURCE ONLY was read by Root and passed locked Rumdl:
/tmp/gludd-v012-pages-js-coverage-independent-review-v4.md. Root then froze all
20 paths and ran ONE fresh complete public ChromiumJS1 command. Native observer
v012-pages-browser-coverage/instrumented-js1-r3-eb78ebd-20261019 is terminal
PASS/rc0/387.023s, termination_reasonnull. Desktop23PASS336.50s includes all
original17 and six additions; originalcompact1PASS45.27s. Cold1.796/cached2.240
remain below5, with original180s/600s/Pages20min unchanged.

Genuine locked c8 report, aggregate85 and independent per-file75 each exit0.
Wholecontroller native94.2line/88.75branch:776lines731covered,249branches221covered,
54functions53covered, skipped0. R2's191-branch denominator is not reused.
Root read actual coverage-summary, source.json, runtime.json and terminal native
status. Controller SHA remainsf39ee2499c1f530211d70519bd5738e64394964f437bd72ee4155a4d0d2c1842;
c8=12.0.0/Node26.0.0/Playwright1.63.0/Python3.12.15 and dirtyHEAD/tree/status are
recorded separately. Native collectors, distinct witnesses and batch merging
supply metrics; no flags/counts/ranges are fabricated. Exact actual command:
/tmp/gludd-v012-pages-js-coverage-instrumented-r3-command.md. Native artifacts:
/tmp/gludd-v012-pages-js-coverage-instrumented-js1-r3/chromium/javascript-coverage/.
Original pre-R2 teardown and R2 branch-floor failures remain immutable FAILED.

This passes focused dirty-source Chromium coverage, not committed exact-SHA
release proof or a new whole JS0/WebKit matrix. Source24 controller repair stays
excluded and still needs combined product acceptance after tooling integration.
Normal source guards/commit/development integration, final full gate and exact-SHA
hosted/release evidence remain pending; all release task statuses stay open.

The first normal guarded commit then stops at the existing secrets hook before
full collection: two public output/cache-path literals in NEW workflow tests
are classified high-entropy. Native exec98118 rc2 and original bytes are retained:
/tmp/gludd-v012-pages-js-coverage-commit-secrets-failure.md and its source copy.
Only ordinary adjacent Python literal spelling changes; exact expected values,
assertions and every production byte remain. No baseline/hook/allowlist/pragma/
policy change. Whole Make/workflow owner29PASS on canonical3112.70s and
registered3122.94s after this conformance; strict1/RuffPASS. Actual native logs/
JUnits/statuses are /tmp/gludd-v012-pages-js-secrets-conformance-311 and312 stems.
Those test-only receipts do not relabel earlier browser/coverage measurements.
The same staged secrets owner then passes all three unchanged hooks. Normal
full commit guards and fresh collection remain required.

## Retained integration entry

The following literal session entry is retained in this owning feature to avoid
duplicating the detailed handoff in SESSION. It remains source/diagnostic history,
not generated current state or release approval.

```text
## S98.4 whole-controller coverage tooling - recorded base eb78ebd354539bc4d9d324899873db3969306515

Last updated2026-10-19. Branch agent-v012-pages-js-coverage-20261019 is the
single source for optional native Chromium/CDP collection plus pinned c8
whole-controller line/branch85aggregate75each proof. Existing uninstrumented
Chromium/WebKit acceptance remains required with the same600s and<5s bounds.
[Feature and exact Make actions](docs/features/PRESENTATION_JAVASCRIPT_COVERAGE.md)
record research, source identity, native owner and missing proofs. Root owns
shared Make/config/workflow/Task/Session and all Git. compact_policy_conformance
owns four Python runner/fixture/test files plus the meaningful new fixture
control owner registered in TASKS. No parallel tooling implementation.

Native relock/sync/audit passed; exact c8=12.0.0 installed/locked,146packages,
LOW audit0vulnerabilities. The same103whole focused cases and3environment
controls pass on actual3.11.14 and registeredMain3.12.15; strict4/Ruff pass.
These do not establish measured Python/JavaScript coverage or real browser
execution. Earlier failed receipts remain separate; no task checkbox changed.
The complete154-case cohort now passes on both actual runtimes,0errors/0skips,
with native whole-file Python coverage95.8line/90.1branch aggregate, runner
95.3/89.4 and whole conftest100/100. Initial124PASS/77.5branch failure remains
retained. Thirty meaningful runner controls close missing behavior coverage;
production runner/fixture bytes did not change for this follow-up. Strict5 and
Ruff pass. Literal portable config is now config/coverage_presentation_browser.ini,
and the feature documents its existing coverage-files recipe;17source paths
are registered. Measured commands used identical external INI bytes, not the
new pathname. Native evidence and commands:
 /tmp/gludd-v012-pages-js-coverage-python-report.md.
Final browser/c8, source review, guarded commit, integration and exact-SHA
release proof remain pending. No task checkbox changed.

Main development is separately clean eb20128176e3533743c23d888e675df690b25814:
reviewed Source23 native startup imports are merged. The actual original
runtime-startup-diagnostic on registeredMain312 passed rc0/12.504s with
1991+1+11 collected. It is collection/import diagnosis ONLY; CI=true was not
set, native source binding is declared, captured import attribution remains
unknown. The full native report is /tmp/gludd-v012-current-startup-eb20128-312.md.
The original hosted90/60 timeout and WebKit cold5.001225731<5 remain unproved.
The previous canonical gate was ABORTED, current gate UNKNOWN, no release tick.

The subsequent current native execution diagnostic passes MAIN1991/62.75s and
help1/1.13s, then tail2FAIL9PASS/6.85s; native75.117s/rc2. This is a retained
diagnostic failure, not admission. Outer Make tracing carriers conflict with
native focused child focus-<64hex> run identity. The shared trace also includes
four intentionally failing child controls, not six release failures. Preserve
.gate-logs/observed/v012-current-main-attribution/current-eb20128-312-20261019.json
and its raw log; no passing artifact was authored. Native registered312 inspect
then verifies correct managed-root configuration with mismatches=[] and no UV
warning: v012-registered312-configuration/managed-root-eb20128-20261019 rc0/.7s.
Future only-managed invocations must explicitly select the actual protected
managed-python install root recorded in the runtime prerequisite index.

External takeover /tmp/gludd-v012-integration-eb20128-handoff.md names current
commit/ref/source versus focused/gate/CI states, slot ownership, protected
checkpoints and next native Make actions. Generated current JSON belongs only
to existing pipeline-status/ci-await/observer/registry producers. Pipeline and
gate repairs precede preparing completed backlog items as the user requested.
```
