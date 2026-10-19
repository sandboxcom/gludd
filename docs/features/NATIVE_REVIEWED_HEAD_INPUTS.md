# Native reviewed-head inputs

`make reviewed-head-inputs` produces the missing immutable inputs consumed by
`make reviewed-head-receipt`. Its four actions are `admit`, `record`, `focus`, and
`finalize`. It reuses the existing Git/schema, item-progress admission,
observed-command, pytest trace, native JUnit, receipt authentication, shard policy
and outer-gate attestation owners. It adds
no test runner, watcher, receipt cache, dependency or release authority.

## Approval and native facts

A trusted reviewer explicitly supplies a schema-1 intent document after reviewing
the actual named original source. The producer accepts only the fields below.
`verdict` must be `approved`; passing tests, Markdown `ACCEPT`, task ledger flags,
anonymous reviews and caller-supplied SHA, parent, operation or pass fields cannot
stand in for that reviewer instruction.

```json
{
  "schema_version": 1,
  "integration_base_ref": "refs/tags/reviewed-integration-base",
  "candidate_ref": "refs/heads/development",
  "heads": [
    {
      "source_ref": "refs/heads/reviewed/feature",
      "reviewed_base_ref": "refs/tags/reviewed-source-base",
      "reviewer": "trusted-source-review-owner",
      "verdict": "approved",
      "focused_test_files": ["tests/unit/test_feature.py"]
    }
  ]
}
```

The intent is an explicit trusted review input, not an authentication mechanism
for an arbitrary external reviewer identity. Supply it only on that reviewer's
actual instruction. The producer derives all SHAs, source diffs, changed files,
ordered application parents, prerequisites and application modes from native Git.
It performs no checkout, ref creation, cherry-pick, merge, deployment or history
repair. Original source and base refs must already exist and remain pinned.

The complete first-parent chain must contain exactly one application per named
source. A cherry-pick requires a distinct original single-parent source, its
reviewed parent and an equal native stable patch ID. A two-parent merge requires
the exact before/source parent order and Git's native clean merge result, without
additional conflict resolutions. Direct source==after commits, fast-forwards,
octopus merges, absent original sources, unresolved prerequisites and unrelated
extra commits are rejected. Existing differing-base prerequisites must be either
reachable from the integration base or a named earlier original source; unsupported
patch-equivalent ancestry remains rejected.

This cannot retrofit `fc27b23e200c8de2c0960a32dbd75720c0197d9c` or manufacture
original source history to make that candidate eligible. A new implementation
candidate requires its own admission, review, focused proof, full gate and hosted
exact-SHA proofs.

## Make contract and sequence

Every target variable is explicit in this no-write behavioral example:

```makefile
make reviewed-head-inputs REVIEWED_HEAD_INPUTS_ACTION=record REVIEWED_HEAD_INPUTS_INTENT=/tmp/gludd-native-review-intent-absent.json REVIEWED_HEAD_INPUTS_OUTPUT=/tmp/gludd-native-reviewed-inputs-absent REVIEWED_HEAD_INPUTS_REPO_ROOT=. REVIEWED_HEAD_INPUTS_GATE_ATTESTATION=/tmp/gludd-native-gate-attestation-absent.json REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=1 WORK_ITEM_ID=''
```

`REVIEWED_HEAD_INPUTS_VALIDATE_ONLY=1` is the default and prints only a plan,
without reading absent inputs, executing tests, creating directories or writing
evidence. That plan is not admission or release proof. With the same explicit
variables set to genuine paths and validation policy `0`, the actions mean:

| Action | Native work and resulting authority |
| --- | --- |
| `admit` | Read-only clean-SHA, original-ref, patch, topology and approval admission; no artifacts or gate evidence. |
| `record` | Repeat admission; atomically publish schema-1 per-source review JSON and `inputs.json` outside the candidate repository. |
| `focus` | Run the stable union of reviewed test files once through the existing `make test-files` and observed-command owner; bind native results. |
| `finalize` | Require final-tip focused evidence, native terminal all-shard policy evidence and separately authenticated completed outer full gate; publish the canonical `reviewed-head-receipt.json`, then `manifest.json` last. |

Run `make release-proof-admission RELEASE_PROOF_ADMISSION_VALIDATE_ONLY=0` before
candidate sealing, final replay or the expensive full gate. This producer does
not run that gate or hosted CI. Finalization retains the existing builder's typed
receipt instead of discarding it or requiring a second builder invocation;
release-readiness, 85% aggregate/75% per-file coverage, warnings, ZDD,
exact-SHA hosted CI and branch/promotion gates retain their separate authority.

`REVIEWED_HEAD_INPUTS_INTENT` selects the explicit review intent for `admit` and
`record`; `REVIEWED_HEAD_INPUTS_OUTPUT` selects the external owned bundle;
`REVIEWED_HEAD_INPUTS_REPO_ROOT` selects the actual clean integrated checkout.
`REVIEWED_HEAD_INPUTS_GATE_ATTESTATION` is required only by `finalize` and must be
the existing native local shard attestation at the resource owner's canonical
`ci-shards/attestation.json` path for that checkout's completed gate. Identical
caller-authored facts at another path are refused.
These values are exported literally to Python, without shell interpolation.
Actual `focus` additionally requires the existing native item-progress primary
and open work item, or an explicit existing `WORK_ITEM_ID`. The canonical progress
owner validates that task; the producer does not invent a task or proof identity.

## Focused and final evidence

The focused command is fixed: `make test-files` receives the derived union of
test paths, `-W error`, a native JUnit output path, and one worker. The caller
cannot supply command text, selectors, collect-only flags or pass/run-count facts.
The binding checks the identical native clean SHA/content before and after the
run and verifies actual native JUnit passing outcomes. Explicitly loaded pytest
hooks append the executing child's interpreter hash/version, installed
distributions, enabled plugin-manager facts and adapter implementation hashes
through the existing native trace owner, at collection and terminal completion.
Each actual test executor must have matching runtime records and successful native
trace completion, bound to the observer's real make-child session and source.
The producer process's interpreter or installed entrypoints cannot substitute for
the executing child's facts. It records the exact retained observer
run/status/log/trace/XML hashes and authenticates them with the existing receipt
envelope owner and gate key lifecycle.

The trusted local `GLUDD_GATE_KEY_PATH` configuration selects the existing gate
key; an absent key is refused. No key is created to make an evidence claim pass.
The existing signer expiry, `GLUDD_RECEIPT_REVOKED_SIGNERS` revocation configuration,
and trusted local key policy remain authoritative. Raw
observer JSON, altered logs/XML, wrong SHA/content/runtime, unsigned or expired
bindings and mismatched command references are ineligible.

A repeated `focus` request verifies an already completed binding; it does not
rerun tests. A failed or incompletely bound reserved run is retained and refused.
The existing canonical item-progress admission owns candidate-input and focused
command identity independently of the external bundle. The fixed command uses
an artifact-only environment path for JUnit; bundle, observer-root and label
choices cannot change failed-input admission. Source, runtime or actual test
inputs must change as required by that owner; a different bundle, observer ID or
commit label cannot clear the original failure. Investigate the retained evidence
before selecting a genuinely changed candidate. No second retry ledger is added.

`finalize` derives `gate:<actual native run_id>` from the checkout's producer-owned
`.gate-logs/gate-background-state.json`, after terminal `finished/gate-exited`.
It independently authenticates `.gate-status` against the candidate's exact
native content, and requires all eight shards with the existing release execution
policy validator. The focused execution must precede the gate and the shard run
must lie within that outer lifecycle; its times must also match the authenticated
outer status timestamp. Schema-3 shard evidence alone, partial focus,
caller-supplied gate IDs, pending state and unauthenticated outer results cannot
produce a final manifest.

The outer command is validated by the existing background producer's shared argv
builder and native executable selector. Literal `make` and its host-selected
absolute executable are eligible only with the exact candidate Makefile, `gate`
target and watchdog-ownership argument. On Darwin the system shim is resolved by
the native `xcrun --find make` selector, bounded to five seconds. An arbitrary
same-basename binary, different Makefile, additional target or missing ownership
argument is refused. Explicit custom Make prefixes remain supported by the
background CLI but do not acquire canonical release authority from that support.

A newer inspector may verify an unchanged historical focus only by comparing its
authenticated child adapter hashes to the two fixed ordinary Git blobs at the
exact tested candidate: `scripts/reviewed_head_inputs.py` and
`scripts/stream_command.py`. Missing, symlink, tree, empty or oversized blobs fail
closed. Inspecting-process bytes never replace those tested bytes. Fresh focus
also requires the executing adapters to match that candidate before launching;
retained focus is authenticated without rerunning or rewriting it. Every existing
source, signature, runtime, timing and shard requirement remains mandatory.

CLI refusals expose only bounded reason categories (`outer-gate-command`,
`focused-adapter-identity`, `candidate-adapter-blob` or `native-provenance`), never
raw exception text, commands or paths. They do not publish a successful handoff.

## Artifact ownership

Each JSON publication reuses the native fsynced exclusive-link writer. Existing
bytes are accepted only for an identical owner retry; divergent or symlink outputs
are never overwritten. Canonical receipt serialization remains owned by
`build_reviewed_head_integration_receipt.write_receipt`; its additive
`exclusive=True` mode uses a no-overwrite atomic link, while existing callers keep
the legacy atomic-replacement default. Identical concurrent receipt bytes may be
reused; a foreign or symlink arrival is refused without changing that entry.
Per-source reviews precede the admission owner marker; the gate binding and
canonical receipt precede `manifest.json`. An interrupted unrecorded directory is
refused and preserved. All source refs, checkout files, historical gate evidence
and generated operational handoffs remain owned by their existing producers.

The bundle contains immutable review/admission records and authenticated focused
binding, then the final gate hashes and manifest. Native observations remain under
the checkout's canonical `.gate-logs/observed/reviewed-head-focus` owner; the bundle
references their retained status, log, trace and JUnit bytes. Existing item-progress
APIs own admission and failure state. There are no background processes added by
this feature. The existing observer owns child/process-group cleanup, timing bounds
and its one-run namespace.

Successful finalization keeps the existing manifest return API and prints one
`REVIEWED-HEAD-INPUTS-HANDOFF` JSON record containing the exact receipt path,
candidate SHA and explicit `make release-readiness` safe-plan argument list.
The guidance is data, not another subprocess; it does not rerun focus, gate or
the builder. Its `release_ready` and `gate_evidence` fields remain false. The
suggested validate-only consumer checks the receipt for that candidate; actual
hosted CI, incomplete tasks, deployment, full-gate and release/promotion evidence
retain their independent authority. No task is ticked. A repeated authentic
finalization preserves the existing receipt bytes/inode, and `record` recognizes
the owned final artifact; changed authentication, source or proof remains refused.
Both API validate-only and the default CLI plan publish no receipt or handoff.

## Long-lived user reports and design choices

A 2022 [Monterey user report](https://stackoverflow.com/questions/73395378/cant-use-make-on-mac-os-monterey)
shows both `/usr/bin/make` and the Command Line Tools absolute Make path within
one native build. The report is operational context, not executable identity or
release proof. Apple's primary [command-line tools FAQ](https://developer.apple.com/library/archive/technotes/tn2339/_index.html)
documents the system shims and native `xcrun` tool selection. The producer uses
that selector rather than trusting a filename or hard-coding a developer folder.

In a 2023 [GitHub Community discussion about identical changesets represented by
different commits](https://github.com/orgs/community/discussions/74385), a user
reported SHA churn and duplicate changeset tracking after rebases. This is user
experience rather than release authority. The primary
[Git patch-id documentation](https://cdn.kernel.org/pub/software/scm/git/docs/git-patch-id.html)
explains the native stable patch identity used here for distinct source/application
commits. Neither equal filenames nor a matching commit message establishes that
equivalence.

The 2019 [GitHub Community report about GITHUB_SHA differing from the PR
head](https://github.com/orgs/community/discussions/25341) illustrates confusion
between a tested merge commit and a source head. The producer therefore obtains
actual parents and clean native HEAD and keeps original review identity, focused
identity, native shard identity and outer gate authority separate. The primary
[Git cherry-pick documentation](https://www.kernel.org/pub/software/scm/git/docs/git-cherry-pick.html)
also describes why merge commits need an explicit mainline; unsupported shapes
here remain refused rather than being inferred from a convenient ref label.

The long-lived [atomic file-creation discussion](https://stackoverflow.com/questions/2333872/how-to-make-file-creation-an-atomic-operation)
distinguishes replacement from an exclusive claim. The maintained
[Python OS API](https://docs.python.org/3.11/library/os.html#os.replace) documents
replacement behavior. The producer therefore uses the canonical writer's native
exclusive mode instead of an existence-check followed by an overwriting replace;
tests reproduce foreign and identical concurrent arrivals before claiming reuse.

## Validation

The focused controls use real isolated temporary Git repositories, genuine
cherry-picks and two-parent merges, native make/pytest execution, native JUnit and
the existing receipt signer/verifier. They include hostile literal values,
duplicate-key JSON, publication races, wrong source refs, direct same-source history,
unreviewed merge changes, dirty/stale content, failed/no-repeat focus, artifact tampering, expired
authentication, partial shards, pending gates and unsigned outer-gate refusals.
Tests, scoped lint/typing, target contract and the documented no-write behavior
are required before this deliverable is frozen for independent source review.

The native finalization repair began with two failing controls: actual producer
argv was rejected for its Darwin absolute Make path, and an updated inspecting
file invalidated otherwise authenticated historical adapter facts. The fixtures
now commit genuine adapter blobs and obtain argv from the real background
producer's no-write plan. Negative controls preserve same-basename executable,
wrong argv, substituted adapter and unsafe Git-mode refusals. Their two positive
regression nodes are promoted through the existing compatibility admission owner
with unchanged 90-second maximum and 60-second quiet bounds; the full-gate test
phase remains required. The existing producer coverage profile includes both
changed owners with the unchanged 85% aggregate and 75% per-file floors. These
controls and source approval are not exact-SHA release completion.

The combined replay exposed two genuine RED controls in the existing receipt
builder's merge/cherry fixtures: their Git helper omitted `cwd`, so native branch
creation was correctly rejected by the canonical temporary-repository confinement
owner. The helper now sets `cwd=repo.resolve()` for the real Git subprocess,
retaining its original inputs and assertions and all confinement enforcement.
The replay must include those existing controls alongside the producer suite.

Independent source review also produced two real failing-first controls. Recording
the same failed candidate into bundles A and B launched two real pytest children;
canonical item-progress admission now refuses the second launch and preserves the
first failed bytes, including when caller observer names change. A tracked recipe
that disabled plugin autoload only in the child exposed the producer-process
runtime substitution. The repaired binding equals a separately captured actual
child plugin manager/interpreter record, with exactly one execution. Both controls
retain genuine native make/pytest, source identity and authentication checks.

The subsequent Main CPython 3.14 replay retained 121 passing controls but exposed
two positive finalization failures. The gate fixture used the inspecting process's
`execution_policy()` through native shard pairing, so it claimed a 3.14 gate even
though the consumer correctly requires the canonical 3.11 release policy. A
narrow failing-first control reproduces the exact native rejection under locked
3.11 by explicitly supplying a 3.14 inspector contract. The fixture now models
the external gate with the native `release_execution_policy()` and canonical
policy digest, retaining native shard plans and all original positive assertions.
A self-consistent 3.14 policy with a recomputed valid digest remains rejected.
Production policy, runtime/source proof, signatures and all other refusals are
unchanged; these fixtures are not evidence of a real full gate or a 3.14 replay.

A later combined native focus retained 531 passing cases and two missing-input
CLI fixture failures. The native Make wrapper legitimately exported actual
`GLUDD_REVIEWED_HEAD_INPUTS_*` defaults, so those cases inherited sufficient intent,
output and repository inputs to perform a valid `record` instead of refusing.
The missing-input fixture now removes only its six CLI-default variables inside
its own `monkeypatch.context()`, preserving every original argument and assertion.
Two failing-first controls use genuine temporary Git review inputs and configured
defaults, with a publication tripwire preventing unintended writes. They require
the exact defaults to be restored afterward, then exercise a real native record
through those restored defaults. There is no global/autouse environment scrub or
change to production defaults, source/runtime identity, approval or gate policy.
The original failed focus observation and unintended external input bundle remain
retained; this scoped repair does not rerun or relabel that combined focus.

## Focus producer watchdog ownership

On 2026-10-18, candidate `76268ef66dcc51423ed4c4178b4cb8ca835ebe3a`
completed all 1,699 focused cases with two existing conditional skips. The native
observer recorded success after 297.108 seconds, but the outer producer exited
143 before publishing `focused-auth.json` or `focused.json`. Those passing child
results do not establish a completed focused binding.

The existing task watchdog log identifies the sender: at 08:35:25 UTC it sent
SIGTERM to the producer Python PID 79111 and its UV parent 79110. Its kill
record follows at 08:35:30 after the normal five-second grace period. It selected
the 300-second-old UV command while an unbound host-global deadline entry was already
1,096 seconds old. The native observer's 600-second quiet and 3,600-second total
limits had not fired. The failure is therefore producer ownership being lost
between two control planes, not a test timeout or an unexplained tool limit.

The repair must carry the existing legacy-watchdog compatibility protection
through the native focus producer and every long-lived Make/UV/pytest child,
including the interval after observer success while validation and authenticated
publication finish. It reuses the gate's established command-marker and native
pytest cache-directory pattern. It must preserve ordinary command cleanup,
literal Make inputs, the one-worker focused command, fatal warnings, both native
observer limits, process-group teardown, source/runtime checks and signatures.
No existing reserved run may be adopted, overwritten, relabelled or signed
retrospectively. The incomplete bundle and raw watchdog evidence remain retained
under `/tmp/gludd-v012-focus-143-76268ef` and the original native proof paths.

Long-lived user reports reinforce the lifecycle requirement. The 2023
[pytest-timeout subprocess report](https://github.com/pytest-dev/pytest-timeout/issues/159)
describes orphaned children after pytest exits; the 2025
[psutil process-group discussion](https://github.com/giampaolo/psutil/issues/2534)
explains why process-group ownership must be established before termination and
can outlive an intermediate parent. These reports motivate preserving the native
observer's cleanup authority. They do not diagnose this incident; the retained
local kill log supplies that evidence. No watchdog is stopped, new daemon added,
application restarted or release proof completed by this repair.


Focused repair acceptance retains eight failing-first cases, then nine native
cases pass. The broader routing/policy replay passes247cases after preserving
its intermediate two stale-roster failures. Native two-owner coverage passes
all193cases at95% combined (producer93%, background owner97%) and every unchanged
85% aggregate/75% per-file floor. One new cheap selector joins the existing third
admission child; all104 prior families, three children, workers2/1/1,21phases,
2850-second ceiling and90/60 bounds remain. This is source acceptance only;
independent source review, fresh final focus, full gate and exact-SHA hosted
proof remain required.

## Exact UV executable command input (2026-10-18)

The frozen368dd05 private3.12 diagnostic executed all three children:
main1974passes/67.65s, fresh-help1pass/1.09s and tail10passes/1failure/15.20s,
zero skips. The native observer failedrc2/88.424s, not timeout. Its immutable
raw history is /tmp/gludd-v012-312-execution-attribution-368dd05/. The tail
ownership test searched for the literal token uv even though the supported
UV input was the verified absolute pinned executable.

The test now asks native Make for the selected producer argv, preserving
inherited command-line/MAKEFLAGS precedence, checks that exact command shape
and resolves only the selected executable. An unavailable selection fails;
there is no fallback. It supplies one explicit shell-quoted absolute UV input
to both ownership dry-runs and expects that exact token before
run/python/-m/pytest. Existing watchdog-owned versus ordinary eligibility,
focus marker routing, literal-value injection tripwire, cache option and trace
plugins remain asserted. Three cheap real Make dry-run cases cover bare,
absolute and quoted literal executable paths without parameterizing the
costly native ownership fixture.

The [GNU Make command-line override contract](https://www.gnu.org/s/make/manual/html_node/Overriding.html)
and [recursive variable transport](https://www.gnu.org/software/make/manual/make.html#Options_002fRecursion)
explain why the fixture reads Make's effective input rather than assuming an
environment value wins. Existing
[stdlib shlex quoting](https://docs.python.org/3.11/library/shlex.html#shlex.quote)
keeps a POSIX-shell executable path as one token. In a related
[firsthand2021 Make path report](https://stackoverflow.com/questions/69612558/save-path-with-spaces-in-makefile-variable),
a user describes a path with spaces and the different behavior of quoting a
program-plus-argument string. That is context for distinguishing one executable
token, not an upstream UV defect or Windows portability proof.

The unchanged node reproduces RED1/3.22s with the verified absolute privateUV.
Final four-case acceptance passes4/3.07s on canonical311 and4/3.42s on private
3.12.15, zero skips and fatal warnings. Initial4/3.25s acceptance is retained
before tightening selected-input provenance. Normal strict/Ruff and250 target
contracts pass. Only the test owner and feature/history docs change; production
85/75 policy, all112 families/recipes/selectors/plugins, three2-1-1 children,
90/60 and21/2850 remain unchanged. No broad replay, timeout margin, gate,
promotion, daemon action or release completion is established.

## One fresh metadata object per runtime record (2026-10-18)

Source review of the native 368dd05 tail found duplicate public metadata
materialization at exactly three sites: installed-distribution and pytest-plugin
receipt identity, and command-admission environment identity. In
[pinned CPython 3.12.15](https://github.com/python/cpython/blob/v3.12.15/Lib/importlib/metadata/__init__.py),
Distribution.version reads self.metadata again. Reading Name from metadata
and then version can therefore parse two header snapshots for one record.

Each site now binds one fresh public metadata object per distribution record
and reads both Name and Version from it. The environment generator retains its
original evaluation order. There is no new helper/parser/library, memoization
or cache across calls, objects or events. Duplicate records still observe
individually, and the next check reads again. Existing Name conversion/errors,
sorting/duplicates, canonical hashes, plugin unknowns, all six separate named
package version lookups, private context, executable digest, candidate
blobs/authentication and start/finish checks remain.

Genuine public Distribution fixtures implement read_text with changing header
snapshots. They prove one coherent read, same-object next-call freshness,
exact stable identity, duplicate cardinality, named-version independence and
current failure without a previous-success fallback. Missing fields retain
the actual public API behavior: Python 3.12's DeprecationWarning remains fatal
under -W error. No warnings are filtered. Initial 14-failure/3-pass evidence
includes five incorrect new missing-field fixture expectations and is retained
separately. The corrected native RED has 13 failures/6 passes in 2.81s; finite
acceptance then passes 19 cases on canonical 3.11 in 2.02s and private 3.12.15
in 2.24s.

Normal strict checking exposed the existing bare/package test import conflict
and module re-export diagnostics. TYPE_CHECKING-only bare test imports,
identical stdlib monkeypatch targets and the rollover stub's actual path/dir_fd
annotation preserve runtime imports, OSError('delete'), the safety attribute
and every original assertion. All intermediate failures remain retained;
all four changed Python files pass strict/Ruff without new suppressions.

The first complete three-owner coverage run passed all 368 tests in 295.98s,
with zero skips. Its native observer finished rc2 in 297.622s because the full
two-file combined coverage was 84%, below 85% (receipts 81%, progress 93%).
That failed evidence remains unchanged. Reuse research found the existing
complete failure-receipt owner, which joins the final cohort. Twenty-five
additional native semantic-database and failure-node boundary controls fill
distinct integrity/error gaps, passing in 1.69s. The final source-only observer
uses 600s/60s based on the actual near-300s cohort; no admission or per-test
timeout changes.

The complete four-owner canonical run passes 434 cases in 265.68s, zero skips.
Native observer 20261018T181154Z-42233-e35b2254 passes rc0 in 267.388s. The
native audit reports 92.3% line and 87.5% branch aggregate coverage; receipts
has 91.6% line/87.1% branch, progress 94.8%/88.8%. Both full files satisfy
unchanged 85% aggregate and 75% per-file line and branch floors.
The private 3.12.15 four-owner run executes all 434 cases with zero skips:
433 pass and the original 0o4600 artifact-mode case fails in 296.89s.
Observer v012-runtime-identity-private312/whole-metadata-20261018 finishes
rc2 in 300.738s (not timeout; trace_path is null). All new metadata/integrity
cases pass. Validation returned valid=True after chmod, but the run did not
capture the effective native mode, so neither a policy defect nor a filesystem
cause is established. The complete failed XML/status/log remain immutable;
an exact native mode precondition is the next bounded diagnostic. No permission
mask or original refusal assertion is weakened, and no broad retry is run.

Authorized input-observation controls subsequently pass the exact mode node
(1/1.33s), the full receipt owner (140/2.31s), and both original mode cases
under the same ordered four-module collection (2/2.52s, 432 deselected).
Those controls do not explain or replace the failed whole-private run. Their
stronger fixture is retained for a separate test-only child; M21 restores the
original mode fixture. No production mode fix is claimed. Fresh frozen-candidate
runtime/combined proof remains required before any integration/release claim.


### Firsthand repeated-lookup findings and limits

In [pip issue 12079](https://github.com/pypa/pip/issues/12079), a 2023 operator
traced a pip/Plone resolver slowdown through candidate.version to repeated
distribution.version access; the report records the associated fix and closure.
The [2024 Python.org discussion](https://discuss.python.org/t/please-make-package-version-go-away/58501)
contains firsthand repeated-version measurements and distinguishes warmed
lookup from process/import startup. Those are different workloads, not Gludd
native 3.12 cost attribution or exact Name/Version coherence reproductions.
Their global/candidate-lifetime cache suggestions do not apply across
independent proof checks. Pinned source and local semantic RED establish this
change; latency savings and hosted 90-second margin remain unmeasured.

The full two-file config includes both runtime owners with branch coverage,
no exclusions or floor changes. All 112 manifest objects, Make recipes,
selectors/plugins, three 2/1/1 children, 90/60, 21/2850, 85 aggregate/75 per file,
fatal warnings, ZDD and cleanup/branch/promotion authority remain unchanged.
This is focused source acceptance, not admission/gate/hosted readiness. No
timing probe, broad replay, provisioning, daemon action or release closure.

## Native permission-input observation fixture (2026-10-18)

The separate metadata child is pinned at
dd806d3041c171d4a471b3939c1f783505fac4fc. Its complete private four-owner
run remains FAILED: 433 passes and one original 0o4600-mode failure, with
zero skips. Bounded passing controls do not explain or replace that run.

The existing receipt fixture now requires its requested native S_IMODE
immediately after chmod. A fixture-local Path.stat callback always delegates
to the original method unchanged and records only this manifest's native
follow_symlinks, mode, uid, device and inode values. Both original refusal
conditions and the exact unsafe-mode reason remain, with snapshots included
in failure diagnostics. No native values are fabricated; successful runs do
not imply that the earlier unrecorded mode is known.

The same private runtime passed the exact original mode node (1/1.33s), the
whole receipt owner (140/2.31s), and both mode nodes after the original ordered
four-module collection (2/2.52s, 432 deselected). Diagnostic retries stopped
there. This separate fixture child passes the complete 140-case receipt owner
on canonical 3.11 in 2.05s and private 3.12.15 in 2.40s, zero skips and fatal
warnings. Normal strict/Ruff pass. These are focused source/input controls.

No production permission mask, accepted state, ownership/cleanup authority,
Make/manifest/selector/plugin/worker/deadline or coverage config changes.
The previous full-file 92.3% line/87.5% branch audit remains separate source
evidence; no second coverage or expensive reviewed-head body replay was run.
The original runtime failure is unexplained, and fresh frozen-candidate
runtime/combined/source/gate/hosted proof remains required. No production
repair, latency saving or release readiness is claimed.

## Native setuid-input admission guard (2026-10-19)

The exact d6a634a frozen four-owner private 3.12.15 run remains FAILED:
433 passes, one failure and zero skips, 299.31s; its native observer reports
rc2/302.972s. The requested permission input04600 was absent at the immediate
post-chmod precondition: native permissions0600, uid501, with matching recorded
device/inode. Validation was not reached. This does not retrospectively
explain the older metadata-child failure, which recorded no effective mode.

A bounded observation kept the selected builtin posix.chmod and its integer
argument2432 unchanged. It returned None while direct builtin stat retained
raw33152 (0100600); the ordinary0644 control changed raw33152 to33188.
That default-context diagnostic reports22 passes/one failure in3.87s. Root
then ran the exact instrumented ordered-four-owner comparison outside the
sandbox: the requested mode was realized as raw35200 (0104600), both original
unsafe-mode refusals passed, and all434 cases passed with zero skips in
289.58s; native observer293.477s. The observed difference establishes execution
context dependence for these runs. It identifies neither a specific kernel
policy nor the cause of older unrecorded failures. Neither run is relabelled,
and this is not a production permission repair or performance measurement.
Raw comparison ownership remains with
`/tmp/gludd-v012-chmod-native-context-comparison-d6a634a/`.

Family113, native-setuid-input-realization, now appends the exact existing
`test_receipt_validation_refuses_unsafe_artifact_mode[2432]` node to the
promotion manifest and places it first in the MAIN admission child. All112
earlier family objects and every earlier selector's relative order remain.
No receipt fixture or validator changes. Real-Make routing controls require
exactly this parameter case once, rejecting omission, whole-module expansion,
the ordinary0644 case, duplication, later-child execution, later MAIN position
and a mismatched manifest node. Existing three sequential children, workers
2/1/1, original90/60,21phases/2850 ceiling, plugins, fatal warnings, ZDD,
authentication, cleanup and85/75 coverage authority remain intact.

Two new routing controls fail first in1.42s; all nine finite controls then pass
in1.97s. Intermediate whole-owner acceptance retains133 passes/four failures:
two stale exact-roster literals and two existing native fixture-collection
EPERM failures. Only the literal expectations are conformed to the additive
selector; no fixture boundary, assertion or setting bypass is introduced.
Final complete routing-owner acceptance is recorded in the source report.
This source guard does not prove the updated90-second target behavior;
independent source review and Root's actual unchanged-bound behavior remain
separate prerequisites before release claims.

The upstream decision at
`/tmp/gludd-v012-native-setuid-input-upstream-decision.md` found no matching
successful-chmod/S_ISUID-loss report. Pinned
[CPython3.12.15 os_chmod_impl](https://github.com/python/cpython/blob/v3.12.15/Modules/posixmodule.c#L3200-L3318)
passes the requested mode and checks the native return code without verifying
the resulting bits. Published
[Apple authorization source](https://github.com/apple-oss-distributions/xnu/blob/main/bsd/vfs/vfs_subr.c#L10364-L10430)
distinguishes S_ISUID ownership from S_ISGID membership; gid0 alone does not
explain this S_ISUID request, and that source does not identify the running
kernel's policy. A firsthand
[Homebrew Seatbelt report](https://github.com/orgs/Homebrew/discussions/6747)
describes explicit denied writes and kernel deny records, not a successful
04600-to0600 operation. These findings provide context, not an exact upstream
cause or a universal sandbox behavior claim.

## Fresh native commit metadata per preparation (2026-10-19)

Root's frozen f277be0 first-two session profile records repeated native Git
launches inside independent reviewed-head preparations. Its controller-local
self/cumulative intervals overlap; they are not summed and do not establish
child CPU cost, hosted latency or clearance of the original90-second blocker.
The retained profile is diagnostic-only; the trace-route child and its source
review remain separate from that historical observation.

The existing receipt Git owner now reads the application/source commit trees
and ordered parents in one native `git show --no-patch` query per preparation.
Requested immutable IDs are deduplicated only within that call. Response
validation requires exactly the unique requested keys, complete native object
IDs, a tree and ordered parents, with the existing1MiB bound; missing, duplicate,
unexpected, malformed, non-ASCII and failed responses refuse. The lower-level
Git helper preserves native SHA1/SHA256 widths; existing receipt schema policy
is unchanged. `--no-show-signature` preserves the old metadata reads' behavior
when repository signature display or a GPG command is configured.

Genuine Git count/parity controls create fixtures before recording and delegate
every native command. They prove metadata launches change from3 to1 for the
one-head cherry-pick and2 to1 for the two-parent merge, on each of two fresh
preparations. Complete plan, source tree, ordered parents and both original
cleanliness fences remain equal to independent native references. A genuine
Git replacement changes the next call's effective object view; no metadata is
reused across preparations, checks, roots, native children or mutations.
All original ref/ancestry, patch/remerge, diff, focused-path, clean HEAD,
worktree/index, authentication, capture, validation and finalization checks
remain.

[Git show's native formatting](https://git-scm.com/docs/git-show) supplies the
structured fields; the existing receipt subprocess owner retains launch/error
authority. No Git library, persistent process, raw-object parser or cache is
added. A firsthand [GitPython issue906 report](https://github.com/gitpython-developers/GitPython/issues/906)
describes a2019 Zuul user's test-runtime regression after a dependency version
change. It does not identify repeated native launches as its cause or provide
a transferable Gludd speedup. The local source/profile and real operation
counts justify this bounded change, not an upstream bug claim.

All113 admission families, original selectors/plugins, three sequential2/1/1
children,90/60 bounds,21phases/2850 ceiling, fatal warnings,85/75 line/branch
floors, ZDD, cleanup and branch/promotion authority remain. Full existing
receipt-builder and native-startup/input coverage owners remain in acceptance.
Raw RED/GREEN, coverage and mandatory collection receipts are retained under
`/tmp/gludd-v012-native-git-metadata-repair-evidence/`. Reduced launches are
not measured wall-time savings, updated admission behavior or release proof.

## Fresh paired native patch identities (2026-10-19)

Each independent cherry-pick comparison now uses the existing receipt Git owner
for one keyed multi-object `git show` and one `git patch-id --stable`. The legacy
single-object `_patch_id` remains unchanged as the native parity reference.
Complete immutable IDs are deduplicated only within the current pair; output
must contain exactly one complete digest for each requested key. Missing,
duplicate, foreign, malformed, non-ASCII, oversized, empty-patch and native-error
responses refuse. Only the small keyed result is bounded; patch payloads keep
their existing native behavior.

The normal duplicate-code guard retained a failed attempt for repeated exact-ID
admission. Both readers now share one pure ordered-request validator, preserving
each caller's original error text, one/two distinct common-width40/64 lowerhex
identities and zero Git work before validation. Query/parsing bodies and the
legacy parity reference remain unchanged; no clone waiver or cache is added.

Real Git controls prepare fixtures before recording and delegate every command.
They establish four launches become two for each local pair, on two independent
builder calls and two independent preparations. SHA1/SHA256, equal and unequal
changes, binary payloads, same-ID deduplication, result ordering, replacement
views, failure after success and next-call freshness retain native parity.
Invalid source parents still refuse before any patch probe. No result crosses
a preparation, capture, validation, repository mutation or authentication fence.

[Git show](https://git-scm.com/docs/git-show) and
[Git patch-id](https://git-scm.com/docs/git-patch-id) define the native formatting
and keyed stable identity contract. A firsthand
[2012-2017 duplicate-commit discussion](https://stackoverflow.com/questions/11605703/git-finding-duplicate-commits-by-patch-id)
distinguishes attributed show output from raw diff streams and describes merge
diff caveats in historical Git versions. It motivates exact keys and unchanged
parent/topology checks; it is neither a current Gludd defect nor a benchmark.
The original report's additional changes were not an identical patch.

The failed development source remains immutable at
`refs/heads/reviewed-v012-integration-dfb4bf8-20261019`: canonical admission
failed with rc124 after90.547s; MAIN1990 passed in72.84s and fresh help1 passed
in1.11s, while the tail was censored. Raw history stays at
`/tmp/gludd-v012-admission-timeout-dfb4bf8/README.md`. Both native cleanup repair
receipts in `/tmp/gludd-v012-cleanup-dfb4bf8-ci-repair-receipts.md` are repaired
awaiting hosted confirmation, not resolved or admission clearance.

The active model implementation owner is release_repair_proposal_review on
`agent-v012-paired-patch-20261019`; model work has no invented OS PID.
The private3.12 whole-owner attempt retained208 passes and one failed public
Make behavior: the selected environment's installed package did not provide
`general_ludd.git_release`. The existing reviewed-head Make recipe now prepends
its current repository/src to preserved caller PYTHONPATH, following the
existing gate-progress convention. Selected UV, actual Python argv, literal
inputs and the focus-only watchdog marker remain unchanged. Native empty and
poison-package controls fail first; the poison payload is syntax-checked and
must never execute once current source is bound. Original hostile-path and
owned-versus-ordinary process assertions remain unchanged.
A source-acceptance run retained199 passes and one failed symlink-output case:
the unchanged production guard first rejected the fixture's lifecycle/signing
timestamps. The sole unchanged node reproduction passed; that does not replace
the failed whole run or identify which historical epoch bound failed.
A new no-sleep earlier-focus regression fails against the actual native signed
epoch. The existing fixture now records genuine UTC start/completion around its
unchanged producer/sign work, preserving focus, shard and signer chronology.
Every original output/refusal/foreign-target survival assertion and production
timestamp allowance remains unchanged; this is fixture input conformance.

Canonical whole-owner coverage retains both existing full-file profiles and
independent85/75 line/branch floors. Private3.12 acceptance retains both complete
changed test owners and every original assertion. All113 families, selectors,
plugins, three sequential2/1/1 children,90/60,21phases2850, fatal warnings, ZDD,
cleanup and branch/promotion gates remain unchanged. Native source receipts
and commands are retained under `/tmp/gludd-v012-paired-patch-evidence/`.

After frozen-source acceptance, review and integration, Root owns the original
unchanged canonical admission, ordinary guarded development push and final
native release proofs. Reduced native operation counts do not establish
wall-time savings, hosted margin, passing admission or release readiness.
