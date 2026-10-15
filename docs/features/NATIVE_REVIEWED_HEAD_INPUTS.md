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
