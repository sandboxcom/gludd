# Make result guidance

S98.4, 2026-10-22 UTC. Agent-facing Make results should teach the model what to
do with the actual result. Current owners include check-make-target-contract,
check-gate-failure-promotions, git-merge-nc, development-merge-forward and
release-proof-admission, human-todo-module, run-watched, work-item and guarded-target parse refusals. Existing native owners, statuses and artifacts remain
authoritative; no second observer, runner, cache or proof schema is introduced.

## Main contract acceptance, 2026-10-26 UTC

These results measure the five frozen Main paths based on
da86949df467666b42971812cb70eeb7a4cd9e7b:
scripts/check_gate_failure_promotions.py, tests/unit/test_run_watched_make_recipe.py,
tests/unit/test_security_verification.py,
tests/e2e/test_enforcement_plugin_e2e.py and
tests/unit/test_xdist_trace_diagnostics.py. The parser has an explicit program
name. The watcher test accepts exactly one native passed/native_status=0 prompt and
rejects other stderr; the scanner test checks the readonly wrapper, baseline and
--scan-cores inputs. Enforcement source checks require call syntax. Trace changes
are formatting only; no warning filter is added.

Cheap admission first retains Ruff D103 for main's missing docstring, then
70 strict typing errors in the complete E2E owner. An accurate main docstring,
precise fixture/helper/test annotations, typed payloads and two explicit JSON
object narrowings repair those inputs. The other assertions remain. All five
individual strict owners pass on both installed runtimes; Ruff, explicit script
docstrings, test-environment guard and tracked-file line limits pass. The logical
Makefile AST node passes one case in 18.01s/native19.19s. The native promotion
owner validates 180 families and finishes all eight pins in 1.68s/native3.563s
before broad acceptance.

The nine complete receiving owners pass 480 tests each, with fatal warnings and
no skips: Python3.11 takes 23.86s/native25.337s and Python3.12 takes
24.43s/native26.026s. Their exact command
receipts are .gate-logs/observed/main-wip-receiving/
main-wip-receiving{311,312}-20261026.json. Both native trace summaries remain
partial:961 events,344 accepted pairs,133 timing issues, one unfinished interval
and zero failure reports. These are not complete timing or performance proofs.

The unchanged config/coverage_gate_failure_promotions.ini retains both complete
production checkers, branch=True, parallel=True, patch=subprocess and physical
GLUDD_COVERAGE_SOURCE_ROOT includes. Both line AND branch floors remain 85% aggregate
and 75% per-file. The historical 209-test raw receipt
coverage-files/20261022T103756Z-14806-70941393.json is unavailable in this checkout;
its old six-owner argv is not reconstructed or reused.

The fresh current profile uses these ten complete owners under tests/unit:
test_gate_failure_promotion.py, test_make_checker_guidance.py,
test_make_target_contract.py, test_check_make_target_contract.py,
test_make_help_throughput.py, test_development_fast_forward_target.py,
test_release_promote_target.py, test_disk_alias_admission.py,
test_runtime_publish_admission.py and test_backlog_audit_target.py.
Native coverage-files keeps n0/maxprocesses1, literal ID=, fatal warnings and a
diagnostic 600-second total/180-second quiet bound. Both runtimes pass 444 tests
plus 37 subtests: Python3.11 takes 219.57s/native221.799s and Python3.12 takes
234.11s/native236.781s. These scopes overlap the
480-case receiving group; they are not additional distinct-case totals.

Both raw reports have identical measured totals, with zero below-threshold files:

| Measured source | Line coverage | Branch coverage |
| --- | --- | --- |
| Both files |307/312,98.4% |126/132,95.5% |
| scripts/check_gate_failure_promotions.py |201/205,98.0% |78/82,95.1% |
| scripts/check_make_target_contract.py |106/107,99.1% |48/50,96.0% |

Raw reports are .gate-logs/coverage-main-native-contract-profile{311,312}-20261026.json.
Native receipts under .gate-logs/observed/coverage-files are
20261026T152817Z-472-123c47c4.json and 20261026T153316Z-23549-2014614f.json;
both exact sessions are drained and observed-status verifies inactive/passed0.
The native BOTH audits are .gate-logs/coverage-20261026T153159Z.json and
.gate-logs/coverage-20261026T153713Z.json. Each report retains four default excluded
lines in the companion checker; this intake adds no source/config exclusion.

An immutable da86949 export runs the three unchanged native CLI controls against
the original parser in both native-module and canonical imported entrypoints:
3PASS on each runtime at .gate-logs/observed/main-wip-prog-before/
main-wip-prog-original{311,312}-20261026.json. These controls establish no
failing-before evidence for the explicit program name; the D103 and 70-error
failures above are separate static admission diagnostics.

This control-plane/test intake restarts no serving process. Root owns the final
six-path source commit, normal collection and exact-source review. The local
receiving/profile receipts do not supply the canonical full gate/global coverage,
exact-SHA hosted/deployment, artifact publication or release-completion proofs.
Their original runtime bounds, ZDD, cleanup and promotion gates remain required.

## Candidate hosted validation requires an exact push run

Root owns this S98.4 enhancement on
release-candidate/v012-git-mutex-20261022 from exact
8ceb3432446fb53a304aea13597c168bd712ec95. Its independent APPROVE SOURCE ONLY
is fully consumed at /tmp/gludd-v012-capacity-policy-8ceb-source-review.md.
Normal collection retains 129128 selected / 129152 collected, 24 deselected,
zero errors in 49.05s / native 54.357s:
.gate-logs/observed/test-count/20261022T170457Z-17321-487771c7.json.
The prior 213 checks per runtime are local receiving evidence. Neither source
review nor collection supplies hosted validation or deployment evidence.

### Delivery owners and the missing trigger

The user asks whether these checks also run on GHA, which handles deployment.
The latest candidate has not been pushed by Root. A real native exact-SHA query
finds zero workflow runs and two missing required workflows, Build and Release
and Molecule Tests. The existing three workflow push filters omit candidate
refs. A PR or manual dispatch cannot substitute: pipeline_status and
require-ci-green deliberately require matching repository, branch, full SHA
and push event, retaining every required and additional matching workflow.

Add only release-candidate/** to Build, Molecule and Pages push branch filters.
Keep old branch order, tag triggers, PR bases, manual entrypoints, Pages paths,
job bodies, concurrency and every release/deployment condition unchanged.
Build runs quality gates on Python 3.11/3.12 and complete test shards on 3.11;
it is not a promise to replay each local 3.12 focused command identically.
Tagged release publication requires the existing exact development source
proof and successful producers. Pages deploys only on master push after both
validate and javascript-coverage, then probes the public revision. Azure live
proof remains a separate protected manual/scheduled workflow. Candidate CI is
validation; actual promotion, release and deployment acceptance remain required.

### Native observation ownership and measured pending state

The first actual lookup is retained at
.gate-logs/observed/candidate-hosted-status/v012-candidate-hosted-status-8ceb.json,
failed 2 / 1.556s. It finds no hosted runs but refuses to overwrite the
development-owned default current output. This is an ownership/configuration
refusal, not a failed hosted test. Keep that file and guard intact.

The actual changed input is PIPELINE_STATUS_OUTPUT, selecting the same native
pipeline-status producer's existing output facility for the candidate branch:
.gludd/release-proof-status-release-candidate-v012-git-mutex-20261022.json.
The corrected observation is
.gate-logs/observed/candidate-hosted-status/v012-candidate-hosted-status-8ceb-scoped.json,
failed 2 / 1.340s: snapshot written, state pending, release_ready false,
zero runs and two missing required workflows. Pending is intentionally nonzero.
No manual JSON, new producer, observer, cache or fabricated workflow is added.
Reuse that branch-owned current output across later SHAs; the producer owns
freshness and non-success replacement. Never adopt another repo/branch record.

### Guarded publication and proof order

The existing git-push-sandboxcom owner applies its native guards and
_runtime-publish-admission to this branch too: pinned EE manifest, separately
watched canonical prerequisites 90/60, then original runtime 90/60, and normal
Git hooks. Release rule 6 permits such normal CI-repair pushes while expensive
final local proof is capacity-blocked; disk alone does not prohibit every
hosted validation attempt. No source approval, trigger patch or earlier local
pass clears a native publication refusal. Preserve the actual failing input
and require a changed prerequisite before retrying it; never bypass the owner.

The older development60907 timeout remains 124 / 90.554s, null trace and no
transport, with unknown cause. It is not a candidate hosted run or a measured
refusal of a command not yet executed on this candidate. Do not attribute it to
disk or assume this trigger correction repairs runtime timing.

After failing-first/whole-owner receiving and required guarded collection,
commit the coherent change, obtain exact-source review, run verify-state and
the applicable normal publication owner. Then verify-remote for the new full
SHA and use pipeline-status with explicit branch-owned output. Require terminal
exact-SHA push CI, subsequent development identity/proofs and guarded promotion;
retain canonical full gate/global 85/75 BOTH and required deployment acceptance.
Manual dispatch success cannot replace canonical push proof. Pages remains
conditional on its original paths; absence is not a passing deployment.
This repair creates a new SHA; it cannot manufacture hosted proof for 8ceb.
All seven release tasks remain unchecked.

### Failing-first selection and local receiving

All19 predecessor cases and their assertions remain. Six additive workflow
cases retain3FAIL/22PASS1.04s before the three branch-filter edits, native2/1.985s:
.gate-logs/observed/candidate-push-triggers-red/v012-candidate-push-triggers-red311.json.
After those edits the complete owner passes25/0.92s, then two independent
mapping/actual-child-roster controls fail2/0.81s before native early wiring:
.gate-logs/observed/candidate-push-routing-red/v012-candidate-push-routing-red311.json,
native2/3.238s. Both failed receipts and their source identities remain.

Family172, release-candidate-push-workflow-triggers, appends after all171
predecessors. The existing first child selects the exact failing function once;
pytest expands its build/molecule/pages parameters. Independent literal rosters
and actual child-argv controls retain every predecessor, three2/1/1 children,
90total/60quiet,21phases/2850, fatal warnings and required complete gate.
All eight required pins pass2.54s before the registered mode1 plan returns0,
whole-shell0/4.487s:
.gate-logs/observed/candidate-push-pins/v012-candidate-push-pins-final.json.
Plan mode runs no live admission owner and grants no proof.

At stable source8f2e7b1bdef1ea29b6135b7c0829796784d3f5b5dedaf1f2531543559da060d7,
the complete13-owner source/consumer union passes591 per runtime:
Python3.11 56.96s and3.12 61.92s, whole-shell0/121.170s:
.gate-logs/observed/candidate-push-receiving/v012-candidate-push-receiving.json.
The additional workflow-security/safe-parser owners and three documentation
callers pass123 per runtime,2.38s/2.48s, whole-shell0/6.511s:
.gate-logs/observed/candidate-push-callers/v012-candidate-push-callers.json.
These714 local checks per runtime retain zero skips/errors and fatal warnings.
Their diagnostic bounds are not original publication/final-gate admission.
The remaining22 workflow cases stay in complete receiving and canonical shards.

The actual three-file strict checker initially stops on duplicate
scripts.ci_named_shard_files/ci_named_shard_files identities. The established
TYPE_CHECKING import boundary gives static analysis one identity and preserves
the original runtime import and every shard-coverage assertion. The same strict
three-file scope then passes. Initial line-limit/Ruff failures are retained:
literal-only roster compaction and reflow keep both large test owners2499lines
and every selector/value/order, without suppressions or a larger ceiling.
Ruff,278 target contracts,15-path registration, seven Markdown files, SESSION
validation, strict file limits and1157 targets/zero duplicates pass before final
metadata binding. No production Python/profile changes or fresh global coverage
claim follows. Normal guarded collection and independent new-SHA source review
remain required before the actual policy-guarded publication attempt.

### Practitioner evidence and trigger diagnosis

Reviewed2026-10-22, [GitHub Community26840](https://github.com/orgs/community/discussions/26840)
records a September2020 reporter seeing no runs after tag pushes; the reporter
later confirms explicit branch/tag filter advice resolved their case. This
firsthand history motivates checking configured ref filters before changing
tokens, retrying pushes or blaming GitHub. It does not establish Gludd's cause
or prove that its unexecuted candidate push will schedule successfully.

[GitHub's workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onpushbranchestagsbranches-ignoretags-ignore)
defines ref-pattern filtering and requires both branch and path filters when
both exist. The local source and native exact-SHA query supply this repair's
evidence. Keep the narrow candidate pattern, existing Pages paths and native
push-event proof; actual transport, hosted terminal results and deployment
acceptance still require their own measurements.

## Capacity results teach existing hard and soft policy

Root owns S98.4 on release-candidate/v012-git-mutex-20261022 from exact
b5d4a40ffb0394db60fabd38e32b58f191cfc3b6. Independent APPROVE SOURCE ONLY
is fully consumed at /tmp/gludd-v012-capacity-escalation-b5d-source-review.md.
Its normal collection retains129126selected/129150collected,24deselected,
zeroerrors51.32s/native56.555s at
.gate-logs/observed/test-count/20261022T164546Z-7186-f96af399.json.
Those fixed receipts do not recertify this later guidance change.

### Observed interpretation defect and authoritative policy

Root's capacity report placed797.5MB scratch/100MB beside96percent volume/95,
without teaching their distinct native roles. The existing automatic_disk_cleanup
owner treats generated scratch as a soft cleanup trigger. After successful
inspection/cleanup, it can admit protected or recent scratch when hard volume
pressure is healthy; its recheck branches2393-2407 are the authority.
The older [explicit volume authorization](RELEASE_PROOF_THROUGHPUT.md#temporary-volume-headroom-authorized-on-2026-10-14)
already documents this policy. disk-check retains its stricter diagnostic
meaning and is not the canonical admission owner. No second hard scratch
prerequisite follows from its failure or a rounded df display.

The existing capacity-blocked AGENT_NEXT result now teaches those roles and
retains inspection/cleanup refusals. It names no duplicated numeric threshold,
adds no waiver, and retains actual capacity repair before retrying admission.
All nine run_owner calls, order, argv, statuses, phase markers, focus preparation,
original runtime90/60, families171/eight pins,21phases/2850, fatal warnings,
85/75 BOTH, ZDD, retention and promotion remain unchanged. No producer, profile,
target, selector, observer, cache or API/service operation is added.

### Native failing-first controls and receiving scope

Two additive native-Make controls preserve all60 predecessor cases. The capacity
branch retains1FAIL while the configuration-failure negative passes,
60deselected0.79s/native1.7s:
.gate-logs/observed/capacity-policy-guidance-red/v012-capacity-policy-guidance-red311.json.
Native2, the original stopped phase prefix, absent runtime/focus/gate/attestation
and false gate/release authority precede the missing-policy assertion. The
negative forbids capacity policy advice on the unrelated configuration result.
These are output/phase-routing controls, not live capacity or gate proof.
The complete owner is already selected in the existing first child; no family
or early-selector edit is needed.

Complete seven-owner receiving passes213 per runtime,31118.25s/31219.93s,
fatal warnings and zero skips/errors:62 admission cases,92 SESSION/docs cases
and59 fixture-collision controls. The whole-shell AND receipt returns0/40.934s:
.gate-logs/observed/capacity-policy-receiving/v012-capacity-policy-receiving.json.
Tested source is1074217744c780f5d1819468a5520685afe998f48ab35a3aa0c997ba9ba452d0.
The120total/60quiet diagnostic is not original publication/final admission proof.
All eight pins pass1.77s; the exact registered plan then returns0 without live
owners. Their shared receipt returns0/3.474s:
.gate-logs/observed/capacity-policy-pins/v012-capacity-policy-pins.json.
Ruff/strict scope,278 contracts, nine-path registration, seven Markdown files,
SESSION validation, strict line limits and1157 targets/zero duplicates pass.
This receiving receipt precedes final metadata checks; a narrower metadata
binding cannot recertify the213-case cohort. Normal guarded collection and
independent exact-source review remain separate required steps. No production
Python/profile changed and no fresh global coverage is claimed.

A distinct bounded read-only diagnostic is retained at
.gate-logs/observed/capacity-owner-attribution/v012-capacity-owner-attribution-b5d.json,
passed0/native3.757s, with its exact log and command in
/tmp/gludd-v012-prerequisite-handoff-ade6.md. Git reports zero garbage and zero
prune-packable objects; this proves no cleanup eligibility or capacity clearance.
Browser/coverage artifacts and worktree namespaces stay protected. The pending
operator question requests actual freed volume capacity or an existing
authorized cleanup owner; no unchanged cleanup, failed API or final proof is
retried. Full gate/global coverage/exact-SHA CI/deployment/promotion and all
seven unchecked release tasks remain pending.

## Capacity admission and HumanTodo escalation have distinct dependencies

Root owns this S98.4 correction from exact
ade6b6e979e6013b98a1f8b04a8afcda72499fbf on the existing candidate branch.
Independent APPROVE SOURCE ONLY is fully consumed at
/tmp/gludd-v012-work-item-ade6-source-review.md; no correction remains.
Normal ade6 collection retains129124selected/129148collected,24deselected/
zeroerrors48.36s/native57.092s at
.gate-logs/observed/test-count/20261022T161839Z-94676-6fdd0ffa.json.
The prior202-source/294-caller receipts per runtime and physical Main95.5line/
90.0branch remain scoped historical evidence, not a reason for unchanged replay.

### Observed workflow error and native authority

Root incorrectly presented persistent daemon configuration/database/schema/auth
facts as prerequisites to the release admission itself. Those facts are needed
for legitimate startup and HumanTodo filing through the unavailable endpoint.
They are not an extra admission phase. The native recipe in
make/30-ci-and-release.mk owns nine existing ordered phases: capacity, gate
configuration, task registration/integrity, failure promotions, resource
ownership, test-environment writes, source contracts and runtime compatibility.
No HumanTodo or daemon owner appears in that sequence.

The capacity-blocked prompt now states that capacity is the direct admission
blocker and HumanTodo is the escalation route. An unavailable request stays
unfiled and needs changed service prerequisites before an API retry. After
actual capacity clears, documented mode0 admission can run even if that request
is still unfiled. Every native owner and final proof remains required.
A filed claim still requires an actual returned ID; check-mode null, prepared
JSON, elapsed time and a newer HEAD remain insufficient.

This is one advisory string correction. All run_owner calls, argv, defaults,
native statuses, stop-on-failure behavior, read-only cleanup/grace, active-focus
preparation, warning policy, coverage, three2/1/1 children,90/60,21/2850, ZDD,
retention and promotion remain unchanged. No new target, phase, dependency,
observer, cache, service startup or human-input bypass is introduced.

### Failing-first controls and current blocker

Two additive native Make fixture controls preserve58 predecessor cases.
The blocked case retains1FAIL while the all-nine-phase control passes,
58deselected1.34s/native2.3s:
.gate-logs/observed/capacity-escalation-red311/v012-capacity-escalation-red311.json.
Original status, phase order, missing later runtime, false gate/release authority
and no attestation assertions precede the missing-message assertion.
The positive control uses the actual administrative focus preparer in its owned
fixture and requires all nine original owners without configuring a daemon or
HumanTodo. These are confined behavior controls, not live capacity or deployment.
The complete admission-target owner is already selected in the existing first
child; no family/selector/roster change is needed. All171 families/eight pins remain.

The real bounded disk diagnostic remains failed2/native0.876s at
.gate-logs/observed/work-item-capacity-check/v012-work-item-capacity-check.json:
96percent exceeds95; generated scratch797.5MB exceeds100MB. Existing native
cleanup exhausted eligible removals; no protected paths or policy are changed.
No HumanTodo tool is exposed in the current tool catalog. That metadata search
does not establish service absence or filing. The operator input request now
targets a real capacity change or existing authorized cleanup owner. Earlier
daemon/store questions remain relevant only to the pending filing route.
Original60907/full gate/exact-SHA CI/deployment/promotion stay pending and all
seven release tasks stay unchecked.

Complete seven-owner receiving passes211 per runtime,31117.70s/31220.54s,
fatal warnings and zero skips/errors. It includes the complete60-case admission
owner, all five SESSION/documentation owners and the complete59-case fixture
collision guard. One successful whole-shell AND receipt returns0/native40.257s:
.gate-logs/observed/capacity-escalation-receiving/v012-capacity-escalation-receiving.json.
Tested source is5f5c5b53b48911ed72b9d7344e17bab162df1220e99a29677ba703f3db8b9dab.
The120total/60quiet diagnostic is not original publication/final admission proof.

All eight current pins pass1.67s; the exact registered plan example then
returns0 with no live owners. Their shared whole-shell receipt returns0/3.179s:
.gate-logs/observed/capacity-escalation-pins/v012-capacity-escalation-pins.json.
Ruff/strict scope,278 contracts, nine-path registration, seven Markdown files,
structural SESSION validation and strict line limits pass. The211-case receiving
precedes this final metadata record. No production Python, coverage profile,
family/selector or branch policy changes; ade6's physical Main95.5/90.0 remains
prior scoped evidence. Normal guarded collection and independent exact-source
review remain separate required steps for this correction.

## Work-item results preserve administrative scope

Root owns this S98.4 enhancement on release-candidate/v012-git-mutex-20261022
from exact ae5d7471014255376ee77f6c5b16df33f9cd95cd. Its independent
APPROVE SOURCE ONLY is fully consumed at
/tmp/gludd-v012-retained-receipt-ae5-source-review.md. Normal ae5 collection
retains129106selected/129130collected,24deselected/zeroerrors50.82s/native56.229s:
.gate-logs/observed/test-count/20261022T155708Z-79039-9ba51b2b.json.
These fixed receipts do not recertify this later source.

The actual work-item status returns administrative JSON without next-action
context. The same native CLI already distinguishes plans, uninitialized status,
validated focus records, administrative mutations and refusals. Reuse that
owner's actual branch; do not parse its output in a shell wrapper, invent a
second observer or interpret receipt pointers as current proof.

### Native result and model action

Every parsed terminal result now emits one AGENT_NEXT line on stderr; stdout
retains its original single JSON record on success and no JSON on refusal.
Existing argparse usage/error precedes the refusal prompt and retains exit2;
ordinary help retains exit0 without a result prompt. JSON/schema/state producers,
native input checks, locking, admission, retention and all exit codes remain.

| Native outcome | Model action |
| --- | --- |
| planned | Use explicit mode0 status before actual work. Check mode did not select focus, check active-ledger eligibility, read current state or run runtime admission. |
| uninitialized | Select one open canonical active task through work-item focus/mode0, then inspect active-work-status for capacity and ownership. |
| observed | Inspect exact linked native receipts with observed-status and actual owners with active-work-status. Pointers do not prove live workers, acceptance or release. Repair the blocked input before its owner retries. |
| updated/prepared | Treat the result as administrative only. Inspect capacity and ownership before existing bounded acceptance/runtime owners; prepare-runtime itself ran no runtime admission. |
| refused | Preserve the native reason and reconcile existing state before retry. A write may have applied before acknowledgement failed. Repair the actual input; never delete state/locks or manufacture current JSON. |

The prompts state gate_evidence=false and release_ready=false. They execute
nothing and grant no full gate, coverage, exact-SHA hosted/deployment or promotion
authority. No daemon restarts; active processes keep their loaded implementation.
The existing Python stdlib argparse/JSON and native result owner suffice; this
adds no parser, dependency, runner, persistent observation or permission rule.
The existing [reuse research](#reuse-and-operating-decision) remains applicable.

### Failing-first controls and receiving proof

Seventeen additive controls exercise actual Main code, including16 missing-prompt
failures and one unchanged help pass,185 deselected2.14s/native3.2s:
.gate-logs/observed/work-item-guidance-red311/v012-work-item-guidance-red311.json.
Native JSON/status, malformed-state no-read in check mode, read-only status,
original task/reason errors and a genuinely applied write followed by an OS error
are asserted before guidance. All185 predecessor controls remain unchanged.

One routing RED retains1FAIL/71deselected0.80s/native1.7s:
.gate-logs/observed/work-item-routing-red311/v012-work-item-routing-red311.json.
Family171 selects the exact initialized-status case once in the existing first
child, after170 predecessor families. All three literal rosters agree. Two
adjacent-literal compactions retain identical expected bytes and2499/<2500 in
the deep test; no assertion disappears. All eight required pins pass1.82s/native
terminal0 at
.gate-logs/observed/work-item-required-pins/v012-work-item-required-pins.json.
Three2/1/1 children,90total/60quiet,21phases/2850, fatal warnings,85/75 BOTH,
ZDD, cleanup, retention and promotion requirements remain unchanged.

Complete source acceptance passes202 per installed runtime. Python311 coverage
runs all202 controls in50.07s, native52.069s, measuring physical Main
scripts/item_progress.py at95.5line/90.0branch under unchanged85/75 BOTH:
.gate-logs/observed/coverage-files/20261022T161425Z-86442-ad8fa7a9.json,
.gate-logs/v012-work-item-guidance-coverage311.json and
.gate-logs/coverage-20261022T161517Z.json.
Python312 passes202 in35.71s/native38.093s:
.gate-logs/observed/work-item-source312/v012-work-item-source312.json.

The complete eight-owner wiring/routing/SESSION/documentation receiving union
passes294 per runtime,31142.06s/31245.20s, fatal warnings and zero skips/errors.
One native whole-shell AND observation returns0 in89.179s:
.gate-logs/observed/work-item-receiving-host/v012-work-item-receiving-host.json.
Tested source is c40f365a65aeec79cc226d4386183f256842c641b3c635772a1e268b90008049.
These source202 and receiving294 scopes have distinct receipts; neither is a
canonical gate. The receiving union precedes this final metadata recording.
Its240total/60quiet diagnostic changes no original publication/final bound.

All four changed Python files pass Ruff/strict scope;278 contracts,1157 targets/
zero duplicates,13-path registration, seven Markdown files, structural SESSION
validation and strict line limits pass. The exact registered work-item status
plan example returns0 with planned guidance and no authority. No new dependency,
selector cache or extra observer is added. Normal guarded collection and
independent exact-source review remain required. Disk remains above95 and the
real capacity HumanTodo is still unfiled; daemon
endpoint/approved binding and persistent config/store facts remain unverified.
Do not retry original60907, final admission/full gate or exact-SHA proof unchanged.
All seven release items remain unchecked.

## Retained collection receipts survive header moves

Root owns this S98.4 receiving correction on the same candidate branch from exact
d76f21fe6a360fbaf650fc5780b564c6f1971acc. Independent CHANGES REQUIRED against
f2e is fully read at /tmp/gludd-v012-guarded-goal-d76-source-review.md, together
with its SESSION history patch and test proposal. No implementation blocker is
identified; the retained d0a collection paragraph inherits a global header that
now names f2e. Preserve this rejected decision, rather than editing it to approval.

Normal d76 hooks retain129100selected/129124collected,24deselected/zeroerrors
48.76s/native54.256s at
.gate-logs/observed/test-count/20261022T153930Z-71443-937148de.json.
At this receiving checkpoint the global header retains its explicit f2e measurement. This is a
historical-binding correction with an executable regression, not a receipt-only
header refresh or reason to replay the unchanged325-case source cohort.

### Correction and existing admission owner

Apply the review's fixed d0a count/ref/receipt hunk. Omit its temporary statement
about which ref the current header names, because that would create another
future dependency. Bind both retained f2e and d0a collection paragraphs directly
to their fixed sources/artifacts. Do the same for the f2e paragraph when its
previously current section becomes retained. Both historical receipts keep their
original counts and scope; neither recertifies a later source.

The existing first-child
tests/unit/test_status_snapshot.py::TestStatusSnapshot::test_session_md_has_markers
retains both marker assertions and gains a read-only check for direct
collection-to-header claims within Retained H2 sections. Six confined controls
cover explicit d0a receipt data under f2e and d76 headers, section resets,
explanatory notes and a wrapped claim below a nested heading. Actual Main
retains1FAIL/6PASS,31deselected1.31s/native2.7s before the receiving correction:
.gate-logs/observed/retained-receipt-red311/v012-retained-receipt-red311.json.

This is a bounded observation of the repeated failure form, not a complete
historical-attribution validator. Fixed counts/ref/receipt correctness still
requires source review. The native status-snapshot producer and generated
regions are unchanged; its validate-only mode retains structural semantics.
There is no new observer, parser dependency, CLI, schema, cache, family or
selection. Existing literal rosters already select this exact SESSION node.
All170 families/eight pins, three2/1/1 children,90/60,21/2850, fatal warnings,
85/75 BOTH, ZDD, cleanup, retention and promotion remain intact.

Ruff/strict scope pass the single changed test. All eight required pins pass1.74s
and native terminal0 at
.gate-logs/observed/retained-receipt-pins/v012-retained-receipt-pins.json.
The complete five-owner SESSION/documentation union passes92 per installed
runtime,3115.44s/3126.29s, fatal warnings and zero skips/errors. One native
whole-shell AND chain returns0 at13.694s:
.gate-logs/observed/retained-receipt-receiving/v012-retained-receipt-receiving.json.
Tested source is150e1fda98f6bf4b0439b59c59977895e9108eee93b779751d09a0e92f24cf44.
Diagnostic120total/60quiet is not original publication/final proof. Contracts278,
seven Markdown files, eight-path registration, SESSION structural validation and
strict line limits pass. Normal guarded collection and independent exact-source
review remain pending. Latest native active-work-status reports exactd76,
gateFAIL/no running gate, all seven release tasks open and zero observed workers;
model-agent status remains separate. Fresh disk97percent/7.7GiB exceeds95.
HumanTodo is unfiled; operator endpoint/approved binding and persistent config/
store facts remain unverified. Keep original60907 cause/replay, final gate/global
coverage and exact-SHA hosted/deployment/reviewed-head/promotion proof pending.

## Guarded target refusals distinguish input repairs

Root owns this coupled S98.4 result-guidance repair on the same candidate branch
from exact f2e2cf5d910cf8f4e44ab0430ee9a48fae89c0db. Its independent
APPROVE SOURCE ONLY against d0a is fully read at
/tmp/gludd-v012-run-watched-f2e-source-review.md; no correction is required.
Normal f2e collection retains129093selected/129117collected,24deselected and
zero errors47.38s/native52.615s at
.gate-logs/observed/test-count/20261022T150906Z-56373-519db956.json.
The earlier watcher source/receiving suites below retain their measured scopes;
they are not replayed merely to refresh a header.

### Native refusal and intended action

A real lint-markdown invocation with correctly quoted MARKDOWN_FILES and three
intentional extra goals stops before work with Make2. The old message only says
to quote multi-word values, which can send a model back to the same failure.
The existing multi-word guard already requires one guarded target per invocation.
Preserve its exact first/extra-goal condition, original reason and parse-time stop.
The same error owner now adds one advisory AGENT_NEXT outcome=refused,
native_status=2, gate_evidence=false, release_ready=false and
next_action=quote-values-or-split-goals.

When extra argv words belong to a value, quote the whole assignment. When the
values are already quoted and the goals are intentional, run separate Make
invocations. Batch independent invocations in the transport only within resource
limits; drain every yielded process before a dependent edit or normal Git hooks.
Never bypass the guard, discard intended work or treat corrected quoting as a
reason to retry an unchanged multi-goal command.

The mandatory guide already records this single-target rule. AGENTS, the guide
and this result-specific record now connect the native refusal to both actual
repairs. This changes guidance, not permitted goals or execution policy.

### Failing-first controls and receiving point

Five new real-Make controls retain5FAIL/1PASS,17deselected1.32s/native2.3s:
.gate-logs/observed/make-goal-guidance-red311/v012-make-goal-guidance-red311.json.
Four cases cover correctly grouped values with intentional extra goals; another
covers split data. Each preserves native2, original reason, no UV/checkout body,
unchanged Git/input authority and parent environment, and no project sync/import.
The positive proves an actual single guarded search with a quoted path/pattern.
All17 prior native-boundary controls remain unchanged.

One exact routing RED0.84s/native1.7s precedes family170:
.gate-logs/observed/make-goal-routing-red311/v012-make-goal-routing-red311.json.
The quoted-markdown node appends after all169 predecessors in the existing first
child. Update manifest, actual Make selector and both independent literal rosters;
all eight required pins, three2/1/1 children,90/60,21phases/2850 and85/75 BOTH
remain. Three adjacent-literal compactions preserve identical assertion strings
and the deep test's2499/<2500 limit. No new runner, cache, profile or restriction.

All eight required pins pass1.80s, native terminal0 at
.gate-logs/observed/make-goal-required-pins/v012-make-goal-required-pins.json.
Ruff and strict typing pass all three changed Python tests;278 contracts,
1157 declarations/zero duplicates,9CI/12local parity, seven Markdown files,
13-path registration, SESSION validation and the unchanged strict line limit pass.
The complete nine-owner receiving union passes325 per installed runtime,
31147.80s/31250.73s, fatal warnings and zero skips/errors. It contains native
Make execution, workflow failure-class guards, readonly Make/version, both
routing owners and the four complete documentation/contract owners. The actual
whole-shell observation keeps both AND-chained runs in its command identity and
returns0 at101.020s:
.gate-logs/observed/make-goal-receiving-host/v012-make-goal-receiving-host.json.
Tested source isf714e25695d968cfe41d34aae75b79be1dc81792eb3b26c65ec9824b33b863ad.
Host execution retains real active-gate process checks; it does not mock sandbox
restrictions. Diagnostic300total/60quiet does not alter original final budgets.
Normal d76 collection is explicitly pinned in the receiving record above.
Its independent CHANGES REQUIRED decision identifies the retained d0a header
binding defect; the existing SESSION node now owns its failing-first receiving
repair. Source-focused325-case acceptance is retained without unchanged replay.
Root is the single Main/shared/Git/proof writer. All seven milestone items remain unchecked.
Native post-f2e ancestry retains development82db11b57b254c1ca1e035975b5cc86253e2fed5
and Git1/enclosing Make2 for f2e outside development; that negative is not failed
source acceptance. Latest native disk96percent/8.0GiB still exceeds95. HumanTodo
remains unfiled; endpoint/approved binding and persistent config/store facts are
unverified. No unchanged original60907, full gate or exact-SHA/deployment/promotion
replay follows from source approval or the added prompt.

## Watched command scope and result prompts

Required observed-status verification remains the next owner after a watched
pass. Drain the exact exec session before dependent work, then batch that
verification with independently needed resource or raw-report reads within the
already admitted local slots. Inspect every result and drain any yielded reader.
After the exact run, command scope, native exit, receipt and required trace/report
checks are verified, continue the already authorized next Make action in its
reserved capacity without another planning turn. Keep runtime executions of an
identical command serialized. This advice grants no new authority; pending,
refused or failed input retains the existing repair/reconciliation path and no
unchanged retry. A first-child receipt never certifies later unobserved work.

### Native cadence evidence and limits (2026-10-24 UTC)

This five-path repair is received on Main from
546beea7a7122c1bf2d2344b4e7650a0cd2c6c3b. A genuine native success case first
failed against the old result prompt; its three executable recorder controls
for 7/125/130 passed.
The old assertions and failure branches remain unchanged. The complete guidance,
command-boundary and trace owners then pass 90 cases on each installed runtime;
the complete item-progress, observer, target-contract and Make-layout owners
pass 277 on each. These 367 distinct cases per runtime are local acceptance,
not a complete gate, exact-SHA CI, deployment or measured model-speed result.

Retain the immutable native receipts under
.gate-logs/observed/make-cadence-prompt-red/make-cadence-prompt-red311-546beea.json
and .gate-logs/observed/make-cadence-receiving/ with runs
make-cadence-a311-546beea (15.183s), make-cadence-a312-546beea (16.027s),
make-cadence-b311-546beea (76.171s) and make-cadence-b312-546beea (67.903s).
The corresponding pytest outcomes are 13.98s, 14.57s, 73.73s and 65.65s.
Exact terminal drain and observed-status verification precede dependent work.

The earlier diagnostic-feedback packet based on
3c60627bf8e974146de65d7c33f3707693cdbc79 retains four coverage-files receipts:
20261024T084501Z-43248-f1d099b0,20261024T084601Z-44475-d05a5d08,
20261024T084647Z-46824-e27f6afb and20261024T084806Z-52738-5e2add5a;
its diagnostic-feedback-consumers receipts are
diagnostic-feedback-consumers311-3c60627 and diagnostic-feedback-consumers312-3c60627.
Their first-start to last terminal-updated span is 407.787170s; the sum of the
six nonoverlapping recorded intervals is 89.591504s. The 318.195666s complement
is uncovered by those six intervals only. It identifies no CPU time, idle time,
cause, critical path or potential saving. The cadence change preserves mandatory
verification and admitted capacity; the exact hosted scheduling SHA still needs
its own measurements before any performance claim.

Root owns this S98.4 repair and Main/shared/Git on
release-candidate/v012-git-mutex-20261022 from exact
d0aba940a650a14404853cf23499f932ee8ee5e3. Independent APPROVE SOURCE ONLY
against a7 is fully read at /tmp/gludd-v012-human-todo-d0a-source-review.md;
no correction is required. Normal d0a hooks retain129014selected/129038collected,
24deselected/zeroerrors47.58s/native52.718s at
.gate-logs/observed/test-count/20261022T134900Z-23107-0a3fbd8e.json.
The later exactf2e normal collection and fully consumed independent source-only
approval are pinned in the guarded-goal record above. These historical starting
facts never close S98.4 or supply final release proof.

### Complete requested command inside the existing owner

The auth acceptance below exposes a real scope bug: raw recipe interpolation
leaves a shell chain's later commands outside stream_command. The historical
first native PASS covers311 only. Preserve it, the unobserved chained312 pass
and the separate watched312 receipt; later code never recertifies old evidence.

The existing Make target now exports CMD in its actual target context using
the constant directive eval export CMD; CMD text is never reparsed by eval.
It passes the quoted environment value as one script to the selected native
shell inside the existing observer. CLI/environment/-e/late prerequisite context,
Make dollar rules, quotes and empty shell positional parameters remain native.
Actual child argv and admission identity retain the whole shell/script; no
first-command argv is synthesized. stream_command.py is unchanged. No new
runner, observer, parser, CLI flag, dependency, cache or coverage profile exists.

For required multi-step work, use an existing fail-closed Make owner or an
explicit AND chain of named Make commands. Native semicolon/OR/pipeline exit
semantics remain; exit0 does not independently prove every earlier command
passed. Keep user prose and raw credentials out of command code. Child output,
trace, limits, process-group/descendant cleanup and terminal receipt remain
owned by the existing native producer.

The existing item-progress owner uses stdlib shlex only for a conservative
diagnostic/normalization view. Only an exact /bin/sh,-c,script triple and exact
split/join roundtrip can retain established Make diagnostic exemptions and
fixed compatibility lifecycle normalization. Compounds, substitutions,
noncanonical syntax, extra arguments, nondefault flags and spoofed shells stay
scoped. Existing override/budget guards remain. Execution, persisted identity,
reservations, source/private environment, unchanged-failure refusal and retention
always keep the complete actual command.

Root fully consumes /tmp/gludd-v012-run-watched-boundary/decision.md and
production-and-trace-conformance.patch. The GNU3.81 postimage is the accepted
proposal input; the plain postimage retains rejected export-override syntax and
must never be ported. Only the reviewed hunks are integrated around Root's
prompts/PHONY; the frozen full Make file never overwrites Main.

### Outcome guidance and retained counterexamples

Exactly one stderr AGENT_NEXT prompt reports refused/passed/failed with the
actual native_status. Usage1, invalid trace2 and original native failures remain.
Pass directs observed-status to the exact new receipt/log/trace; no receipt means
no verified observation. A failure before native start creates no new receipt:
never reuse an older current success. Preserve its reason, identify the actual
failed owner and changed input before retry; reconcile potentially applied work.
The prompt grants no full-gate/release authority and does not replace native data.

Nine additive controls retain sandbox9FAIL2.12s/native3.0s: eight missing-prompt
failures plus a separate sysctl permission failure, not a product timeout result.
Authorized host Make retains genuine9FAIL2.58s/native3.395s, including actual124:
.gate-logs/observed/run-watched-guidance-red-host311/v012-run-watched-guidance-red-host311.json.
The earlier sandbox receipt stays under run-watched-guidance-red311.
Eight non-timeout focus controls then pass0.87s/native1.6s with one deselection;
that focus is not whole-owner or timeout proof.

A tenth control retains1FAIL0.75s/native1.7s for absent producer PHONY binding.
Moving the same exact-one assertion after actual behavior retains the stronger
native counterexample1FAIL0.65s/native1.6s: Make0/up-to-date suppresses all work
when a fixture file is named run-watched. The source now declares PHONY.
The positive requires the real child, receipt/argv/prompt and untouched foreign
namesake bytes; duplicate/missing producer binding still fails. Both failed
receipts remain under run-watched-namesake-{red311,native-red311}.

The separate proposal retains frozen31-case RED21FAIL/10PASS11.37s/native12.199s.
Its first candidate fails21/10 at0.70s/native1.458s before any child because
GNU3.81 treats export as a prerequisite. Corrected binding passes30 with one
unchanged sysctl-EPERM timeout failure4.54s/native5.283s. Nine cheap predicates
pass0.54s/native1.417s with30deselected. The decision owns all exact archived
receipts. No sandbox retry, cleanup weakening or implementation credit follows
from a rejected candidate. Root's port uses repository defaults and canonical
pytest context; proposal-only absolute paths are absent from product defaults.
Private-source copies prove controlled binding behavior, never physical Main
coverage or actual UV startup. The original trace argv expectation truthfully
describes the newly watched shell; actual-child argument/status/cleanup assertions
remain intact.

Twenty-seven additive tests against actual Main policy retain5FAIL/22PASS1.87s/
native2.8s before27PASS2.25s/native3.4s. They preserve literal diagnostics and
capacity authority, reject unsafe wrappers, retain private input and full identity,
and refuse unchanged failure despite new observer metadata. Receipts remain at
.gate-logs/observed/run-watched-main-admission-{red311,green311}/
v012-run-watched-main-admission-{red311,green311}.json.

### Complete source-specific acceptance and cheap routing

The five complete owners are test_item_progress, test_run_watched_command_boundary,
test_run_watched_result_guidance, test_run_watched_trace and test_observed_command.
One real host observation keeps all three AND-chained Make phases inside its
actual shell identity:311185PASS39.41s with coverage,311131PASS23.07s and
312316PASS65.50s. Thus the complete union passes316 per installed runtime,
fatal warnings and zero skips/errors. The native owner remains running across
all phases and returns0 only at134.052s:
.gate-logs/observed/run-watched-source-host/v012-run-watched-source-host.json.
Tested source is642959992e092eb3db2028bf6e9b0800daf396e1e14ff34789a5e5d07d942d49.
Diagnostic240total/60quiet is not the original publication90/60 replay or final
combined candidate proof. Native host timeout124 and descendant checks now pass.

Unchanged config/coverage_item_progress.ini measures exactly one physical Main
file through all185 cases,95.0line/88.9branch;85aggregate/75each-file BOTH passes.
Native coverage receipt:
.gate-logs/observed/coverage-files/20261022T143523Z-39007-6fddde58.json.
Raw report is .gate-logs/v012-run-watched-item-coverage311.json; the audit is
.gate-logs/coverage-20261022T143604Z.json. No copied fixture coverage, exclusions
or threshold changes substitute for that measured file.

Three routing REDs1.10s/native2.0s precede families167-169 for native failure
guidance, namesake execution and complete-command scope. They append after all
166 predecessors in the existing first child only. Native three2/1/1 children,
90/60,21phases/2850, all eight existing pins and full gate remain unchanged.
Update the manifest, Make selector and BOTH independent literal test rosters;
never derive expected inventory from the manifest. The first required-pin run
catches the second stale literal roster:1FAIL/7PASS2.91s in native terminal
evidence, not an invented per-run JSON. Complete that exact literal conformance;
compact only three adjacent string lines to keep the test file2499/<2500.
The required owner then passes all eight in1.77s/native3.4s, terminal0:
.gate-logs/observed/run-watched-required-pins/v012-run-watched-required-pins.json.

Ruff/strict scopes pass all seven changed Python files. Contracts retain278
targets,1157declarations/zero duplicates and9CI/12local phases. The current documented
actual behavior example passes0 at run-watched/20261022T145626Z-50242-6a62a5c3;
its empty pytest trace is never test/release evidence. The complete seven-owner
receiving cohort passes262 per required runtime with fatal warnings and zero
skips/errors:31142.16s and31246.85s. It contains both routing owners, the four
affected documentation/contract owners and the complete current guidance owner,
including assertions against missing receipt and stale-success reuse. One native
whole-shell observation returns0 at91.423s with both phases in actual identity:
.gate-logs/observed/run-watched-receiving-host/v012-run-watched-receiving-host.json.
Tested source isf6d6807579cd5ea5900ebb6645266c8d66b0033b7882e4b012ead758bd14a3dd.
Diagnostic300/60 never expands original publication/final budgets. A batched
lint-markdown invocation refuses extra goals before work; the already documented
single-target invocation passes without changing that guard. Seven Markdown files,
16-path registration, SESSION validation and the strict line ceiling pass.
Normal guarded collection and independent exact-source review remain separate.

### Reuse and operating decision

The [GNU recipe variable rules](https://www.gnu.org/software/make/manual/html_node/Variables-in-Recipes.html)
and [environment/export rules](https://www.gnu.org/software/make/manual/html_node/Environment.html)
define the existing expansion boundary. The
[GNU eval section](https://www.gnu.org/software/make/manual/make.html#Eval-Function)
explains repeated eval expansion; passing only a constant variable name avoids
reparsing command data. Two direct subpage fetches time out; the fetched full
manual/search supplies that source context, not native GNU3.81 acceptance.
[Python shlex](https://docs.python.org/3.11/library/shlex.html) documents lexical
split/join and POSIX quoting, not whole-script execution. The
[2021 firsthand report](https://unix.stackexchange.com/questions/669864/use-env-variable-in-makefile-target)
records a user's empty-value/double-quoting problem and confirmed correction.
It motivates exact native quote/context controls; forum advice is not local proof.

Batch related deterministic source repairs and complete focused consumers at a
stable receiving point; use explicit fail-closed dependencies and retain each
phase's native result. Fewer coherent observations reduce startup/duplicate work
only when the observer owns the entire command. Keep one Main/shared writer,
at most two test owners and drained sessions before mutation or Git hooks.
After capacity/service prerequisites clear, freeze one candidate and obtain the
canonical gate/global coverage plus required exact-SHA hosted/deployment/reviewed
head/promotion proofs. Do not broaden unrelated backlog work, retry original60907
or claim a measured hosted improvement from these local source results.
All seven release tasks, disk97percent/7.5GiB above95, operator daemon/binding
and persistent config/store facts remain pending; real HumanTodo is still unfiled.

## HumanTodo results require identity and reconciliation, 2026-10-22 UTC

### Explicit controller credentials

The next coupled S98.4 acceptance starts from reviewed local
a7a43b165fd16bf44c45ad6be906003cc25ff0a7 on the same candidate branch.
Root fully consumes /tmp/gludd-v012-human-todo-a7-source-review.md and its prior
843/ebc decisions. The exact reference correction needs no further change.
Normala7 hooks retain129008selected/129032collected,24deselected/zeroerrors
48.56s/native54.819s at
.gate-logs/observed/test-count/20261022T130154Z-8210-4b008f3d.json.
No collection, source approval or fixture result authorizes live filing.

The collection README wrongly promises automatic GLUDD_AUTH_PSK fallback.
The canonical client and its five existing security tests intentionally forbid
that admin-credential scavenging. Correct the documentation and preserve the
client. Native Ansible already supports an explicitly selected controller env
lookup in the existing JSON psk field. Reject missing and empty bindings with
default=undef() followed by default(undef(), true). Preserve credential bytes,
including quotes, dollar signs and Jinja-looking text; never evaluate the
credential as another template or put its value in argv/JSON/Make assignments.
The operator supplies an approved binding in the existing controller environment;
the lookup contains only its name. This is request configuration, not a new
Make variable, credential-discovery rule or default ambient authorization.

This check-mode example performs no HTTP write. Append the complete installed
offline runtime tuple from the mandatory Make guide. The named binding must
already exist and be nonempty; otherwise native Ansible refuses before HTTP.
The JSON backslashes preserve the inner Jinja double quotes through shell data:

~~~console
make human-todo-module HUMAN_TODO_ARGS_JSON='{"title":"Credential binding check","body":"Validation only","psk":"{{ lookup(\"ansible.builtin.env\", \"GLUDD_HUMAN_TODO_PSK\", default=undef()) | default(undef(), true) }}"}' HUMAN_TODO_VALIDATE_ONLY=1
~~~

Literal HUMAN_TODO_VALIDATE_ONLY=1 is check mode; exit0 is a result.
Literal0 is apply and still requires actual owned endpoint/config/store/auth
facts, requested-state verification and a real returned ID. A test fixture ID,
id=null or plan never satisfies those conditions. Do not change a refused real
request or substitute a fixture endpoint to manufacture filing evidence.

The separate mature-tool research is fully consumed at
/tmp/gludd-v012-human-todo-auth-transport-decision.md. The official
[controller env lookup](https://docs.ansible.com/projects/ansible-core/2.19/collections/ansible/builtin/env_lookup.html)
defines undefined defaults; the
[2.19 trust guide](https://docs.ansible.com/projects/ansible-core/2.19/porting_guides/porting_guide_core_2.19.html#template-trust-model-inversion)
and inspected native CLI/legacy-JSON source support literal CLI args templating.
Moving stable2.19/2.21 sources are not exact installed-version acceptance.
The [2016 firsthand ad-hoc report](https://forum.ansible.com/t/using-variables-defined-in-files-when-running-ansible-command/22840)
and [2025 maintainer trust discussion](https://forum.ansible.com/t/core-2-19-templating-changes-preview-and-testing/40759)
motivate version-specific native controls, not local success or release authority.
No fallback, trust relaxation, file-based runner or Make interface is added.

Six controls use real Make, a UV argv recorder that execs the installed UV
unchanged, native Ansible and the canonical module. Their loopback-only server
is an ephemeral test fixture with no daemon store, namespaced joined thread and
closed socket. A marked synthetic ID is never filing evidence. The cases cover
named success, missing, empty, omitted psk despite ambient admin fixture, wrong
explicit binding despite correct ambient fixture, and check mode. They require
exact dual auth headers/body, literal Make/UV/Ansible argv, selected interpreter,
one fork, owned-temp cleanup, no credential in output/argv, no HTTP for missing/
empty/check cases and native error/guidance preservation. Verbosity4 also exposes
native executed module commands for secret-free argv assertions. Shared client
fallback remains forbidden; no existing assertion or coverage requirement changes.

The first sandbox attempt fails all six at socket bind with PermissionError1;
this is six setup errors, not a product RED or transport verdict. Preserve
.gate-logs/observed/human-todo-auth-native311/v012-human-todo-auth-native311.json.
The authorized host Make boundary then passes all six default-verbosity cases,
Python3.11/Ansible2.19.14,5.84s/native6.7s:
.gate-logs/observed/human-todo-auth-host311/v012-human-todo-auth-host311.json.
Logs/XML share /tmp/gludd-v012-human-todo-auth-{native,host}311 names.
The first strengthened verbose/current-quote consumer run retains4FAIL/144PASS
in8.37s/native9.3s on311; fail-closed chaining correctly leaves312 unrun:
.gate-logs/observed/human-todo-auth-consumers/v012-human-todo-auth-consumers.json.
The fixture parser wrongly treats Ansible's chmod staging line as module execution.
Conform only that new parser: check every matching command for secret absence,
identify execution by its script argument, require exactly one execution and its
exact selected interpreter. Retain all auth/header/body/status/cleanup assertions.
This is a new fixture conformance failure, not a product RED.
The corrected five-owner union passes148 per installed runtime with fatal
warnings, zero skips/errors and the same tested source
8e1c8997e9b6a457d5a6561d7725f9eca55d938c54c8d4edbc8a62035aba6a78:
311/Ansible2.19.14 takes10.96s/native12.038s at
.gate-logs/observed/human-todo-auth-consumers-v2/v012-human-todo-auth-consumers-v2.json;
312/Ansible2.21.5 takes9.26s/native10.209s at
.gate-logs/observed/human-todo-auth-consumers-watched312/v012-human-todo-auth-consumers-watched312.json.
The complete owners are the new auth controls, original HumanTodo Make/module,
legacy runtime HTTP and the five unchanged no-scavenge security cases.
Ruff and strict typing of the new test pass. Four affected documentation owners
pass54 per runtime,3117.16s/native8.391s and3128.08s/native9.323s, at
.gate-logs/observed/human-todo-auth-docs{311,312}/v012-human-todo-auth-docs{311,312}.json.
Eight Markdown files, nine-path task registration, SESSION validation and the
unchanged strict line ceiling pass. Normal guarded collection and independent
exact-source review remain separate. The original whole-module coverage retains
97.6line/95.5branch under85/75 BOTH; no production code changed here.

Root's attempted CMD shell chain exposes a separate observer boundary gap:
the v2 receipt covers311 only, because Make evaluates the second command outside
the watcher. Its earlier312 run passes148 in12.44s but has only terminal/XML
evidence, not watched authority. Preserve that evidence and the first failed
receipt; the explicit watched312 run above supplies the missing bounded result.
At that historical checkpoint, only one native Make command belonged in CMD.
Separate watched invocations supplied the missing runtime result; an overall tool
exit or first-child success could not certify that old chain. The
[current repair](#watched-command-scope-and-result-prompts) now owns the complete
command; its newer receipt never recertifies this retained historical result.

No production, Make, profile, family/pin or budget
change follows from the authentication documentation contradiction; original166/eight pins,
three2/1/1 children,90/60,21/2850,85/75 BOTH and all final proofs remain.

### Separate check policy from native exit status

Exact843fe156cd3a2bf6449456bc8365c5740e70cb9c has independent APPROVE SOURCE ONLY
against374, fully read at /tmp/gludd-v012-human-todo-843-source-review.md.
Its normal guarded hooks collect129008selected/129032collected,24deselected/
zero errors47.56s/native52.927s; native receipt:
.gate-logs/observed/test-count/20261022T124815Z-1429-8cc1bca6.json.
Final four-owner documentation receipts pass54 per runtime,3117.29s/native9.008s
and3128.49s/native10.296s:
.gate-logs/observed/human-todo-docs{311,312}/v012-human-todo-docs{311,312}.json.

The review catches ambiguous SESSION wording "check mode0/native0.8s/id=null."
The intended0 is an exit status; literal HUMAN_TODO_VALIDATE_ONLY=0 applies.
Apply the exact source-only review's receiving patch:
HUMAN_TODO_VALIDATE_ONLY=1 selects check mode; exit0/native0.8s/id=null reports
its native result and no filed request. This documentation clarification changes
no source, Make policy, assertions, rosters, budgets or candidate proof state.
Retain accepted source suites; check affected documentation and normal collection
without repeating662 unchanged tests or adding a receipt-only commit.

The frozen review packet includes its exact17-path roster, thirteen historical
native evidence copies and two unchanged843 imports: the canonical HTTP fixture
and module client/result helper. Include those direct support boundaries when
preparing review exports; a changed-path list alone cannot prove restoration or
error-schema compatibility. They are frozen review evidence, never current
observations or another cache/runner. Root consumes the complete decision/patch
and all reader sessions are quiescent before the receiving mutation.

Latest native status retains exact843, gateFAIL/no running gate, seven open
milestone tasks,132 excluded backlog items and zero observed workers; model-agent
status is separate. Disk remains97percent/7.5GiB against95. Operator endpoint/
credential binding and persistent daemon/store ownership remain unverified.
No original60907/final-gate/API replay, startup, cleanup or promotion follows.

### Retained implementation evidence

This coupled S98.4 repair starts from reviewed local
37454900137c21e4d051b9c1fd65cc6c450ac514 on
release-candidate/v012-git-mutex-20261022. Development remains
82db11b57b254c1ca1e035975b5cc86253e2fed5; no integration or push follows.
Root fully consumes APPROVE SOURCE ONLY againstfe2 at
/tmp/gludd-v012-proof-admission-374-source-review.md. Normal374 hooks retain
128913selected/128937collected,24deselected,zero errors49.67s/native55.036s:
.gate-logs/observed/test-count/20261022T120527Z-77538-f06c3086.json.
The earlier630-per-runtime acceptance remains source-specific. Carry these
receipts into this substantive repair; do not create a receipt-only commit.

The actual HumanTodo module accepts HTTP200/201 with a missing, null, blank,
padded or nonstring ID, and accepts a resolution response for a different ID.
That is a false verification boundary even when transport succeeds. The existing
API router serializes the committed record's actual ID; PATCH returns the
requested record. Require a nonempty unpadded string, preserve its exact bytes
and require matching identity for done/dismissed. Do not invent a UUID grammar
or normalize a returned ID. Native transport/HTTP error messages and statuses,
valid response payloads, explicit client binding and check-mode shapes remain.

A malformed successful response fails with its original HTTP status and
write_outcome=unverified. This does not prove the daemon made no change.
Reconcile the real record before retrying; a blind POST could create a duplicate.
No returned ID, fake service, check-mode changed=true or prompt can verify filing.
The existing Make owner emits stderr AGENT_NEXT for refused inputs, temporary/
runtime failure, successful check-mode plan, returned apply response and module
failure. Preserve original stdout, native failure status, literal JSON/policy,
selected interpreter, one fork and owned temporary cleanup. Terminal guidance
follows the native child. A returned apply response still requires capturing the
real ID and verifying requested state; it supplies no milestone/gate authority.

The genuine module RED retains58FAIL/33PASS0.93s/native1.910s, with all nine
predecessor assertions intact. Make guidance retains9FAIL/20PASS2.90s/native4.038s.
Routing retains2FAIL/65deselected0.95s/native2.0s before promotion. Receipts:
.gate-logs/observed/human-todo-id-red311/v012-human-todo-id-red311.json,
.gate-logs/observed/human-todo-guidance-red311/v012-human-todo-guidance-red311.json
and .gate-logs/observed/human-todo-routes-red311/
v012-human-todo-routes-red311.json. Logs/XML use the corresponding
/tmp/gludd-v012-human-todo-{id,guidance,routes}-red311 names.
The82 new module cases reuse the canonical physical module and existing HTTP
adapter fixture. A scoped PATCH recorder restores the shared fake afterwards;
the complete legacy HTTP owner runs with it. Nine additive real-Make controls
invoke all original literal-policy/argv/status/temporary-cleanup assertions.
Two further native controls retain temporary-directory and interpreter failures,
original stderr/status9, no request and one failure prompt. The initial native
registration guard refuses four unregistered HumanTodo paths; explicit S98.4
path registration repairs the real input without adding or ticking a task.
The new throughput cross-reference exceeds the strict ceiling at2501 then2500;
compact that prose, preserving all older content/history and the strict limit.

The complete106-case module/legacy cohort passes1.63s/native3.1s with fatal
warnings and no skips/errors. Whole physical module coverage is97.6line/95.5branch,
with no exclusions and unchanged85aggregate/75each-file BOTH:
.gate-logs/observed/coverage-files/20261022T123757Z-89274-df0d3a26.json,
.gate-logs/v012-human-todo-module-coverage311.json and
.gate-logs/coverage-20261022T123800Z.json. This is generic source coverage,
not E2E execution or global release coverage. The only missed line/arc is the
main guard. Strict scopes for the module and both changed test owners pass;
Ruff passes without suppressions. The actual registered check-mode example
returns0, changed=true/id=null and planned guidance, with no HTTP filing.

Exactly two new cheap nodes append as families165/166 after all164 predecessors.
Both literal runtime rosters and the independent ordered manifest pin retain the
same node order. The routing assertions preserve first-child counts1/0/0, all
eight existing pins, three children2/1/1,90total/60quiet,21phases/2850 and full gate.
Literal-only adjacent-string compaction keeps the existing promotion test below
2500 physical lines without dropping a byte of its expected selection/assertions.
All eight native pins pass1.77s/terminal0;278 target contracts and the documented
actual example pass. The complete16-owner receiving union passes all662 cases
per runtime, fatal warnings and zero skips/errors. Python311 takes104.84s/
native106.129s; Python312 takes110.10s/native111.470s. Exact receipts:
.gate-logs/observed/human-todo-receiving{311,312}/
v012-human-todo-receiving{311,312}.json; logs/XML are
/tmp/gludd-v012-human-todo-receiving{311,312}.{log,xml}. Both native commands bind
unchanged tested sourceea4bdfc207be5ab9ce89b83a1c30685843bea89a0f454e0515c4735c6e954673.
The native receipt retains every deduplicated owner and installed runtime tuple.
These diagnostic240total/180quiet limits never replace original90/60 proof.
Final documentation/registration checks and normal guarded collection retain
their own native results; do not add a receipt-only commit or replay accepted
source tests merely to restate that collection. No hosted speed claim follows.

Capacity remains the measured97percent/7.6GiB against95, not cleared by a new
candidate. Preserve original60907 and the refused/unfiled capacity request.
The updated request configuration is
/tmp/gludd-v012-capacity-human-todo-request-374.json. Its bounded check-mode
receipt .gate-logs/observed/capacity-request-input374/
v012-capacity-request-input374.json returns0/native0.8s/id=null: no request filed.
An unanswered operator question requests the existing endpoint and credential
binding name only, never its value. Do not repeat the same refused API request.

Root fully consumes the independent existing-core-daemon proposal and stdio
patch in /tmp/gludd-v012-core-daemon-proposal/. Decision: HOLD startup/apply.
The public foreground CLI already owns Popen wait and signal forwarding; reuse
that lifecycle if a thin Make entrypoint is later needed. Its --project does not
isolate the store; an optional numeric PID file is not ownership/readiness.
Source child stdio is suppressed, and external binding prints a PSK. No proposed
stdio patch or daemon target is integrated here. Actual persistent config,
database/schema/runtime ownership and explicit safe credential transport are
unverified. GLUDD_DB_PATH cannot override configured database host/url; do not
substitute temporary SQLite or a fake API to manufacture an ID. The shared client
intentionally never scavenges GLUDD_AUTH_PSK. Raw psk in HUMAN_TODO_ARGS_JSON
would enter Ansible --args despite no_log; keep it out of argv/logs/receipts.
A verified native secret binding needs its own confined controls before apply.
No new supervisor, observer, cache, outbox, daemon or cleanup is introduced.

The existing [firsthand Make report](https://stackoverflow.com/questions/10559064/terminate-makefile-command-when-subcommand-throws)
retained in the throughput feature motivates explicit local failure handling.
It does not establish this API defect's cause or authorize a retry. Native
receipts establish the repair; original full-gate, exact-SHA hosted/deployment/
reviewed-head/promotion proofs and all seven milestone checkboxes stay pending.

## Release admission teaches bounded next actions, 2026-10-22 UTC

The next coupled S98.4 change starts at reviewed local
fe2d6a53611a6e1fb38adf96d7fb1392a1069a5d on the same release-candidate branch;
development remains82db11b57b254c1ca1e035975b5cc86253e2fed5. Root fully reads
independent APPROVE SOURCE ONLY against65cb at
/tmp/gludd-v012-merge-forward-fe2-source-review.md. That review accepts the
twelve frozen integration-guidance paths, six native receipts and retained
predecessor assertions. It grants no APPLY=1, cleanup, final gate or release
authority. Normal fe2 hooks retain128897selected/128921collected,24deselected,
zero collection errors48.47s/native53.899s at
.gate-logs/observed/test-count/20261022T114053Z-60590-67fe245a.json.
The first write-tree128/index.lock failure above stays unexplained; serial
guarded retry follows actual ENOENT, never lock deletion or a cause assertion.

The existing release-proof-admission owner now emits one short stderr result
guidance line for planned, refused, blocked or passed outcomes. All original
stdout markers, reasons, native status propagation, phase ordering and policies
remain. The plan explicitly leaves live capacity, configuration, ownership,
source contracts and runtime unexecuted. Input refusals identify mode/task data;
a blocked child names its actual phase and native repair owner, retains failed
evidence and requires a changed input before retrying. Native focus refusal
states that the runtime child has not started. Terminal guidance follows every
successful child and still requires one proof owner, a frozen exact candidate,
independent source review, combined acceptance, canonical gate and exact-SHA
hosted/deployment/reviewed-head proof. No prompt supplies release authority.

Capacity guidance uses existing disk/lifecycle and HumanTodo owners only.
Keep protected resources when no cleanup is eligible. A check-mode
null, request configuration or refused API request is not a filed HumanTodo;
a refused service needs a changed prerequisite before another request. The
same-input original60907/final-gate replay stays blocked. The separate read-only
native cache inventory examines seven children with zero errors in0.161s;
largest pre-commit is77049856 allocated bytes. Its native receipt is
.gate-logs/observed/user-cache-inventory/v012-user-cache-inventory-fe2.json,
terminal0/native0.3s. These measurements cannot clear GiB-scale capacity or
authorize managed-cache removal; no new lifecycle, scanner or daemon follows.

Sixteen additive behavioral controls preserve all42 predecessor cases. They
retain16FAIL/42deselected4.09s/native5.2s before the emitter exists:
.gate-logs/observed/proof-admission-guidance-red311/
v012-proof-admission-guidance-red311.json and
/tmp/gludd-v012-proof-admission-guidance-red311.{log,xml}.
Private real-Make cases cover plan, both literal input refusals, all nine
failed owners, two real focus refusals and both ordinary/native-focus success.
They assert exact original markers/statuses, stderr-only prompts, native child
order and absence of gate/attestation writes. The complete test owner already
runs in the first existing admission child; no new family, pin, phase or
whole-file widening is needed. All164 families/eight pins, three2/1/1 children,
90total/60quiet,21phases/2850 and85/75 BOTH remain unchanged.

One cheap invocation attempts to combine lint-files with two other goals; its
existing strict goal contract refuses before execution. The corrected separate
lint-files invocation and separate strict scope both pass. Do not combine
file-scope owners with extra goals to save calls. No assertions, scope checks,
warnings or guardrails are weakened. Markdown catches a heading-level gap;
the strict line owner also refuses BUGS2515 and TASKS2509 before replay. Only
new tracking notes are compacted into existing paragraph prefixes; all old
history and full decisions in this feature remain. A literal transport first
fails to match the Unicode task heading before mutation; UTF-8 encoding fixes
that exact input. Complete receiving and normal per-commit collection remain
distinct from source review and final release proof.

The complete 17-owner receiving cohort passes all 630 cases per runtime, with
fatal warnings and zero skips/errors. Python 3.11 takes115.07s/native116.282s;
Python 3.12 takes117.69s/native118.985s. Native
receipts are .gate-logs/observed/proof-admission-guidance-receiving{311,312}/
v012-proof-admission-guidance-receiving{311,312}.json; each
progress_identity.command retains the complete deduplicated paths, argv and
installed interpreter. Logs/XML are
/tmp/gludd-v012-proof-admission-guidance-receiving{311,312}.{log,xml}.
Both native receipts bind unchanged tested source
00e2824364e37c92206078c4a0c0dda861f3edbc1aef76ad4b6815c5cfb4397c.
The diagnostic240total/180quiet limits never become original runtime90/60 proof.

All eight existing pins pass1.78s/terminal0;278 contracts, the actual documented
mode1 plan(exit0),1157 targets/zero duplicates/parity, six Markdown files, eight-path
registration, native SESSION mode1, strict scope/Ruff and line ceiling pass.
This production delta changes only Make; no Python owner or coverage profile
changes. Historical6bc95.2line/90.2branch remains source-specific two-checker
coverage, never this candidate's global audit. Final native make disk measures
97percent/7.6GiB against95; make ps shows only the two existing Python controllers,
which is not a Git/model-agent idleness proof. No new daemon or cleanup runs.
Normal guarded collection remains mandatory. No original60907/full-gate,
exact-SHA CI/deployment, integration, release promotion or milestone tick follows.

## Development integration results, 2026-10-22 UTC

The next coupled S98.4 delta starts at local65cb7fb481f95fc3ef18a9808927140ed2ebd128;
development stays82db11b57b254c1ca1e035975b5cc86253e2fed5. The native watched plan
v012-development-integration-plan65cb returns0 and resolves SOURCE to exact65cb,
but executes no merge, branch/clean check, collection or gate. That successful
plan therefore cannot clear capacity96percent/7.9GiB against95 or authorize an
unchanged failed publication replay. Actual APPLY=1 retains its native collection,
secrets, lint, merged-tree gate-refresh, pre-commit and gate-freshness sequence.

The existing development-merge-forward owner now prints planned, refused, failed,
unchanged or passed guidance on stderr. Original reasons/stdout and explicit
refusal2/failure1/success0 stay intact; enclosing Make retains its native failure
meaning. A plan names SOURCE/mode validation after APPLY validation and the
unverified branch/clean/capacity/test inputs. It directs worktree-state and the
documented release-proof-admission mode0 only after repairing failed prerequisites.
An unchanged-ancestry result says no source applied/no new gate. Failed guidance
asks the model to wait for native transaction cleanup and inspect state; it never
claims cleanup succeeded. Applied guidance follows actual git commit and still
requires exact-SHA CI/deployment/reviewed-head evidence. PHONY prevents a namesake
file from suppressing this administrative owner.

Fourteen additive real-Make/Git controls retain all33 predecessor cases/assertions:
four clean/staged plans across content/ancestry-only on a feature branch, seven
original refusal reasons/statuses with exact state, an already-ancestor no-op,
a namesake file and a real external-driver conflict. The first13 fail in4.75s/
native6.0s; the separate native conflict fails0.99s/native1.9s before its guidance.
No configured external driver is installed in Main. The private conflict verifies
its real rollback state without promising arbitrary hooks/concurrent-writer safety.
This change preserves the existing merge options, transaction trap and gates;
it is not a new certification of those other boundaries.

Two routing controls fail0.95s/native1.8s before additive families163/164. Both
new exact nodes run once in the existing first child after162 predecessors;
independent counts, ordered IDs and both literal rosters retain every old entry.
Eight cheap pins, three2/1/1 children,90total/60quiet,21phases/2850 and required
full gate stay intact. No new phase, script, runner, daemon or proof cache exists.

Cheap feedback catches one127-character line and a2502-line fixture before broad
acceptance. Wrapping the new expression and compacting unchanged literal data
preserve all assertions and the strict less-than2500 ceiling. Native exact-once
edit guards reject ambiguous matches before mutation. Native diff inspection
also catches a literal JavaScript replacement collapsing Make dollar escaping;
the exact native byte replacement restores the original SOURCE refspec before
execution. Literal transports must encode the intended bytes: JavaScript string
replacement interprets double dollars unless its replacement is returned literally.

The complete18-owner receiving union passes747 per runtime with fatal warnings,
zero skips/errors:311131.59s/native133.094s and312124.25s/native125.917s. Native
receipts are .gate-logs/observed/merge-forward-receiving{311,312}/
v012-merge-forward-receiving{311,312}.json; each progress_identity.command retains
the entire deduplicated test-path roster, argv and installed runtime. Logs/XML
are /tmp/gludd-v012-merge-forward-receiving{311,312}.{log,xml}. Both native
receipts bind identical tested source99f8c2fbe3ea051eae32b9a1db98fccf78dd31ec35e2f388b2ebc66810721958.
The240total/180quiet diagnostic envelope is not the original runtime90/60 proof.

All three separate strict scopes/Ruff,278 target contracts,1157 targets/zero
duplicates/parity, six Markdown files,12-path registration, SESSION structure
and strict line ceiling pass. Eight current promotion pins pass1.83s/terminal0;
the actual documented SOURCE=HEAD/MODE=content/APPLY=0 behavior returns0 with
explicit non-authority guidance. Source/test/manifest inputs remain stable.
The subsequent documentation additions record only these received measurements.
The first normal ship-commit-files attempt returns Make2 before collection:
pre-commit returns3 because native git write-tree returns128 with index.lock
already present. The existing show-lines owner refuses the external pre-commit
cache-log path; no alternate external read is attempted. The actual console
failure is retained here rather than fabricated as a generated observation.
Exact make list-files DIR=.git/index.lock later returns native find1/Make2 with
No such file or directory. Native worktree-state still names65cb and all12 owned
paths staged, no unstaged/untracked changes. This observed absence permits
normal guarded re-evaluation; it proves no original cause or stale/dead owner.
No lock deletion, abort, restoration, checkpoint removal or bypass occurs.
The next guarded invocation uses make -j1 to serialize its Make jobs; this
scheduling choice is not a demonstrated cause repair. Do not repeat it if the
same failure recurs without a newly observed prerequisite change.

Normal guarded collection remains mandatory and its native hook receipt owns
the next commit's result; no recursive receipt-only commit or unchanged
acceptance replay follows. Normal65cb hooks retain128881selected/128905collected,
24deselected0errors54.53s/native59.849s at
.gate-logs/observed/test-count/20261022T110241Z-40296-fc8e9206.json.

Historical6bc source approval/840-case acceptance/95.2line90.2branch canonical
checker coverage retain their actual source; no Python production owner changes
here and those figures never become this delta's final/global coverage proof.
New exact-source review and final release proof remain separate. Latest native
disk is97percent/7.7GiB against95; only the two existing controllers remain after
the terminal test owners. No new daemon or cleanup is started. Original60907,
complete canonical/global coverage, exact-SHA hosted/deployment/reviewed-head/
promotion and all seven release tasks remain pending.

## Result-to-action contract

Keep the original summary/reasons on their established stream. Follow with a
short AGENT_NEXT line that names the relevant input, next Make action and missing
proof. A refusal needs a prerequisite repair; it never authorizes an unchanged
expensive retry. A pass names only what that owner measured.

Contract PASS directs the changed target's registered behavioral example, every
documented variable explicit and any expected refusal/status preserved. Contract
FAIL names target/help/variable/example repair; ERROR names input/parse repair.
Both failures retain script1; success retains0. Missing or unsafe Makefile
composition now enters the existing ERROR branch through MakefileLayoutError,
retaining its original native cause and failure1 instead of an unowned traceback.

Promotion script PASS says STRUCTURAL ONLY. Its native summary can precede eight
child tests (six predecessors plus two Make consumers), so the prompt requires all eight pins and terminal Make
exit0 before a broader cohort. The Make target emits outcome=passed only after
that child returns0. Manifest/input/child failure emits its failed phase and
repair action, preserves native reasons/status and prevents terminal passed.
All six predecessor pins, their exact argv, worker count and fatal-warning flags remain; two consumer pins add early failure feedback.

Promotion manifest/validation errors retain script1. Native argparse usage and
argument errors retain2; help retains0 and makes no result claim. Guidance does
not suppress errors or execute behavioral examples. The enclosing Make retains
its usual nonzero behavior. Git merge guidance is specified with its actual
state/abort boundaries in [the owning feature](GIT_MERGE_NO_COMMIT.md).

Prompts are advisory context for models. Source review, focused acceptance,
normal collection, a structural pass and a terminal target success supply none
of the missing canonical full gate, global coverage, exact-SHA hosted/deployment
or guarded release promotion proof.

This is a control-plane ZDD change: no serving process is restarted. An active
operation retains its loaded code; later invocations use the updated owner.
Rollback follows the existing reviewed branch flow while retaining failed
receipts, owned checkpoints and cleanup/retention/promotion gates.

## Permanent controls and source coverage

The confined two-script proposal retains25FAIL/2PASS2.25s/native3.007s before
27PASS1.75s/native2.468s. Root fully read its decision, literal production patch,
postimages and terminal receipt under /tmp/gludd-v012-make-guidance-proposal.

Canonical receiving tests retain27 cases and every original assertion:
25FAIL/2PASS2.98s/native3.9s precedes27PASS2.96s/native4.0s at
.gate-logs/observed/checker-guidance-{red,green}311.
The two composition failures expose the real uncaught helper error. Native
disposable CLI execution and the canonical in-process function must have exactly
equal stdout, stderr and native status for each tested result. Canonical real
code objects are measured; a copied fixture's coverage cannot replace them.

The fixture uses the existing synthetic manifest schema, nodes and budgets,
rather than copying another fixture implementation. It keeps ordinary help,
relative/absolute manifests, multiple simultaneous reasons, missing/malformed/
UTF-8/oversized inputs, expected refusal, usage and native output ordering.
The actual external pytest child asserts project config and equal physical
rootdir/confcutdir. It creates no gate observation and runs no example or gate.

Eight real Make-stanza controls retain8FAIL1.62s/native2.6s before
8PASS1.53s/native2.498s at make-result-guidance-{red,green}311 under the same native
observed root. They reuse the existing validator/separate test-child fixture:
each of six pins failing, validator7 and success. Native child5 and enclosing
Make2 remain; terminal passed must follow the child marker and is absent on all
failures. Existing validator/argv/order/worker/assertion checks remain intact.

Coverage reuses config/coverage_gate_failure_promotions.ini, adds the complete
contract checker alongside the complete promotion checker, and retains branch
measurement/subprocess patching and85aggregate/75each-file line AND branch.
Both include paths bind to physical GLUDD_COVERAGE_SOURCE_ROOT, automatically
exported by the existing coverage-files owner. This measures the two canonical
production files; temporary copies prove CLI parity but cannot fragment or
substitute production coverage. No source file or arc is excluded.

Ruff caught a126-character guidance literal and a constant getattr in the new
test. Literal reflow preserves output; direct typed module access preserves the
native calls. The target contract also exceeded the strict ceiling at2501lines;
compacting only its new git-merge-nc entry preserves every field and278 entries.

## Consumer repairs before another broad replay

The first complete614-case coverage run retains2FAIL/612PASS205.47s/native207.0s:
.gate-logs/observed/coverage-files/20261022T102641Z-270-1ff07c48.json.
The guide lost make check-make-target-contract, and the variable guard stripped
recipe indentation before counting Python assignments. Native failure correctly
prevents coverage publication/audit; this run grants no green coverage claim.

Six controlled real-Make inputs retain a separate fixture6FAIL/IndentationError.
After correcting only the fixture's initial command,2FAIL/4PASS1.09s reproduce
the semantic error. The repair checks the leading tab before stripping; the
existing20-limit assertion, exceptions and messages stay. Recipe-only and20-plus-
recipe pass, while genuine21 and leading-space genuine21 still refuse. Actual
native Make executes every fixture before the guard checks it. Guide restoration
plus these controls pass8/0.77s/native1.7s.

This is guard conformance, not a larger allowance or a source-format escape.
[GNU recipe syntax](https://www.gnu.org/s/make/manual/html_node/Recipe-Syntax.html)
distinguishes default-tab commands from Make variables. A custom RECIPEPREFIX is
not certified here. No parser dependency, suppression, weaker assertion or
production merge-formatting workaround is added.

Two native routing controls fail before families161/162 bind both real consumer
nodes to the existing check-gate-failure-promotions owner. Its eight current pins
retain all six predecessors first and pass8/1.87s terminal0. Existing real-child
controls grow from eight to ten outcomes; terminal passed must follow the child
and remain absent on every failure. Current prompts say eight; older six-pin
receipts above retain their historical scope.

Instrument complete source owners and required coverage callers under the same
two-source85/75 BOTH profile. Run remaining complete consumers through ordinary
native acceptance once per required runtime. This retains the deduplicated union
without instrumenting unrelated repository-wide AST traversal. It is a scheduling
choice; no measured speedup or omitted scope is claimed.

## Measured throughput decision

The complete441-case receiving owners take native78.668s on311 and84.474s on312
at exact41b. One unnecessary repeat of both would consume163.142s by arithmetic;
this is not a measured new pipeline saving. Required normal41b collection takes
native57.111s and remains mandatory for every logical source commit. Approval
waiting, optimal batch size, a future two-item speedup and original60907 cause
are unmeasured. The reviewed throughput decision is retained at
/tmp/gludd-v012-throughput-decision/decision.md; source timings remain in the
[admission record](INTEGRATION_ADMISSION.md#git-task-handoff-admission-and-confined-duration-fixtures-2026-10-22-utc).
The current mutex/admission/helper work is coupled S98.4 repair, not another
completed backlog item. Do not rerun a covered subset after its whole owner
passes or repeat accepted41b mid-repair; changed shared foundation inputs still
require affected consumers at stable receiving. The [throughput research decisions](RELEASE_PROOF_THROUGHPUT.md#research-decisions-for-this-pipeline)
support fast feedback/WIP limits, not a proven optimum. Native result prompts
in [the result contract](MAKE_RESULT_GUIDANCE.md) name that next action while
preserving statuses, all eight current child pins and pending final proof.

## Complete receiving measurements

The deduplicated complete receiving union retains these terminal results;
each row names distinct complete test owners, with no failed/skipped cases:

| Scope | Python3.11 | Python3.12 | Native receipt owner |
| --- | --- | --- | --- |
| Six source/Make owners |209PASS27.11s/native29.535s, with coverage |209PASS14.13s/native15.0s | coverage-files/20261022T103756Z-14806-70941393 and make-guidance-source312/v012-make-guidance-source312 |
| Eleven remaining consumers |417PASS111.45s/native112.6s |417PASS100.05s/native101.060s | make-guidance-consumers{311,312}/v012-make-guidance-consumers{311,312} |
| Two merge owners |92PASS81.20s/native83.0s |92PASS81.76s/native83.9s | merge-nc-final{311,312}/v012-merge-nc-final{311,312} |
| Four operating/handoff owners |122PASS5.68s/native6.5s |122PASS9.68s/native10.8s | make-guidance-operating{311,312}/v012-make-guidance-operating{311,312} |

All receipt prefixes above are under .gate-logs/observed; named logs/XML remain
under /tmp/gludd-v012-* except the native coverage log/trace. They are source/
receiving proof, not exact-SHA hosted or final-gate evidence. Diagnostic180/60
and240/180 envelopes do not alter the original runtime90/60 requirement.

The unchanged85aggregate/75each-file BOTH audit is
.gate-logs/coverage-20261022T103826Z.json:95.2line/90.2branch overall, promotion
checker98.0line/95.1branch and contract checker89.7line/82.0branch. It measures
the two complete canonical production files with no below-threshold file and
pytest0. The raw two-source report is
.gate-logs/coverage-v012-make-guidance-final311.json.

Cheap checks retain278 target contracts/actual empty-BR refusal2,162 families/
eight pins1.87s terminal0,1157Make targets/zero duplicates/parity,10 Markdown
files,26-path registration, native SESSION mode1, strict scopes/Ruff and the
less-than2500 limit. Node compatibility uses its existing installed3.14 owner:
six cases pass9.46s; it is separate from the311/312 acceptance above.

## Normal guarded source receipt

Exact6bc6981413c5c1f5b042043cc04f793f17b6841c is committed locally on
release-candidate/v012-git-mutex-20261022. Normal hooks retain zero new clones,
six owned checkpoints and128881selected/128905collected,24deselected0errors
48.65s/native53.765s. The native receipt is
.gate-logs/observed/test-count/20261022T104837Z-32263-e7ef01c1.{json,log,pytest.jsonl}.
Independent APPROVE SOURCE ONLY against41b is fully read at
/tmp/gludd-v012-native-make-review-decision.md. The review accepts all26 frozen
paths and ten native receipts; it preserves literal/native merge state, original
statuses, terminal child ordering, canonical full-file coverage,162 additive
families/eight pins and unchanged runtime/full-gate floors. It does not recertify
prior mutex source or arbitrary hooks/filters/concurrent writers. Its two exact
nonblocking six-to-eight wording corrections are applied in this documentation
receiving; historical six-pin measurements stay intact. The reviewer and all
reader sessions are quiescent. This record supplies no full-gate, exact-SHA
hosted/deployment or release-completion claim.

The documentation receiving runs only its five complete operating/guide owners,
not the accepted840-case source union:133PASS on3117.23s/native8.138s and312
7.85s/native8.820s. Native receipts are
.gate-logs/observed/make-guidance-handoff{311,312}/v012-make-guidance-handoff{311,312}.json;
logs/XML are /tmp/gludd-v012-make-guidance-handoff{311,312}.{log,xml}. Both preserve
fatal warnings/no skips or errors. Seven-file Markdown, registration, SESSION,
line-limit and278-contract checks pass. Normal per-commit collection remains
mandatory; consume its native hook result without a recursive receipt-only commit.

## Receiving sequence and release limits

Run cheap lint, separate strict scopes, target contract, documented behavior,
line checks and all eight current promotion pins before the complete receiving union.
Families157-160 append the staged-test preservation, literal Make branch,
terminal prompt and layout-error witnesses after all156 predecessors. Four
routing controls fail first before additive manifest/ordered-seed/count/literal
roster changes. Each node runs once in the first existing child with no file
widening; three2/1/1 children,90total/60quiet,21phases/2850 and required full gate
remain. Script structural output alone never starts a broader owner.

At stable receiving inputs run the deduplicated complete changed owners and
required consumers on each installed runtime, then normal per-commit collection
and independent exact-source review. The earlier41b441-case acceptance and
APPROVE SOURCE ONLY remain history, not new proof of changed Make inputs.
The native Make guide and AGENTS.md codify the same sequence.

The source review retains capacity97percent/7.8GiB as its frozen input. Latest
native disk96percent/7.9GiB still exceeds95. Original60907/124/90.554s/nulltrace/no transport,
original runtime replay, canonical full gate/global coverage and all exact-SHA
hosted/deployment/reviewed-head/promotion proofs remain pending. The seven S98
items stay open. A new prompt, source commit or elapsed time repairs none of
those inputs.

## Unstaged-edit advice follows native evidence, 2026-10-24 UTC

Root's normal helper commit (session61292) preserved separate intentional Node
edits, but the existing AA043 advisory said pre-commit's stashed edits would be
missing from the commit and instructed the agent to run `make git-stash-pop`.
That advice was unsupported: pre-commit had restored its private patch files.
An unstaged working tree alone establishes neither lost owned edits nor a Git
stash that is safe to apply.

The result owner is the existing `_edit-commit-atomicity-guard` recipe in
make/50-development-and-guardrails.mk. Its Git predicate, observed-unstaged line,
native status and final PASS stay unchanged. The advisory now routes to
`make worktree-state` and `make git-staged` as separate read invocations, then
comparison of exact owned worktree/index paths with intended change/checkpoint
evidence. Preserve intentional unrelated edits and use the existing narrow Make
owner only for proven owned differences. PASS remains advisory and supplies no
commit, full-gate or exact-SHA hosted/deployment/release proof.

The regression uses the existing native pre-commit fixture and real installed
entrypoint, followed by all three actual staged hooks and the literal AA043
recipe. Ordinary and `-e` carriers cover clean and unrelated-unstaged cases.
Native patch/restoration output, exact working bytes, staged blob/index tree,
HEAD/refs and an empty Git stash are independently retained. No pop is executed.

At immutable source713af3ac0869c17f7792e4f4348855efc6f7baed, the initial diagnostic
retains5FAIL/6PASS5.07s/native6.051s at
atomicity-advice-red/atomicity-advice-red311-713.json: two fixture assumptions
incorrectly expected INFO messages from the imported context and three legacy
structural assertions assumed direct public prerequisites. The installed CLI
control corrected only the logging input; genuine advisory RED then retained
2FAIL/2PASS10.51s/native11.7s at
atomicity-advice-native-red/atomicity-advice-native-red311-713.json, failing on the
actual stash-pop advice after native restoration/state assertions. The changed
advice passes all four native cases6.38s/native7.4s at
atomicity-advice-focus/atomicity-advice-focus311-713.json.

Only the three affected P24/AA043 structural assertions conform to the existing
public `_commit-transaction` route and exact private guarded prerequisites.
Global assertion helpers and unrelated policy checks stay intact. Ruff for all
three test files, native Make validation, environment-write guard,289-target
contract and tracked-file line limits pass. Separate strict guidance-only
typing passes. Including the two legacy structural files in strict typing fails
with317errors; a bounded immutable713 export, preserving both package
initializers and current root configuration, independently reproduces317errors
at atomicity-legacy-type-baseline/atomicity-legacy-type-baseline311-713.json
(native2.0s). This remains a failed legacy type scope, not a typing pass or a
reason to suppress annotations/checks or expand this advisory repair.

Complete affected classes, the complete native guidance owner and
parameter-collision owner pass80 cases each with no skips on installed311 and312.
The source stays fixed at the five-path receiving snapshot based on713af3ac.
Native receipts atomicity-advice-receiving/atomicity-advice-receiving311-713.json
and its312 counterpart retain12.99s/native13.931 and14.22s/native15.382,
original300/60,n0,fatal warnings and literal ID=. Root's exact-five-path staged
content guard also passed all three hooks and line limits in2.1s; its clone
owner correctly skipped changed_production=0. Receipt:
atomicity-advice-staged-preflight/atomicity-advice-staged-preflight311-713.json.
The final receipt-only prose update does not recertify or require repeating
those unchanged source tests. Normal per-commit collection, independent source
review, Main receiving, canonical full gate and exact-SHA hosted/deployment
proof remain separate and pending. No production Python, coverage scope,
runner, cache, serving process or promotion gate changes in this advisory repair.
