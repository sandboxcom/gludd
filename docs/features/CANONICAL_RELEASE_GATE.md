# Canonical release gate profile

The `release` test profile lets one full `make gate` produce both the signed gate
status and the canonical local dual-track attestation for the same clean commit.
It keeps all gate preflights, lint, type checking, collection, smoke, cleanup, and
terminal signing. Only the test phase delegates to the existing
`test-ci-dual-track-local` producer, avoiding a second full test replay solely to
replace native gate evidence with release-policy evidence.

## Operator contract

`GATE_TEST_PROFILE` accepts exactly `native` or `release`. The default remains
`native`: the existing current-interpreter, quiet pytest policy, dirty-worktree
development admission, and at most two bounded batch workers remain available.
The release profile requires a clean immutable commit, the locked CI dependency
profiles on CPython 3.11, exactly one test worker, and exactly `-W error` with no
extra pytest arguments. It executes every canonical shard, every isolated phase,
and the unchanged 85 percent aggregate and 75 percent per-file line and branch
coverage floors.

The bounded batch size remains `GATE_MAX_FILES_PER_BATCH=1..32`. For release
routing, `GATE_EXACT_SHA_RESUME=1` maps to the canonical producer's existing
`DUAL_TRACK_RESUME=1`; `0` maps to `0` for a fresh test execution. Native routing
retains its existing authenticated exact-SHA receipt admission flags. Resume
never changes the canonical policy, shard membership, isolation phases, or
coverage floors.

Safe policy validation performs no gate launch and writes no release evidence:

```text
make gate-admission-config GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1
```

After committing the intended candidate, the complete foreground command is:

```text
make gate GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1
```

The supported observed background launch forwards the same policy explicitly
and retains the existing exact process/session owner and timeout watcher:

```text
make gate-background-observed GATE_TEST_PROFILE=release GATE_EXACT_SHA_RESUME=0 GATE_MAX_FILES_PER_BATCH=16 GATE_BATCH_WORKERS=1 GATE_TIMEOUT=21600 GATE_POLL_INTERVAL=60 GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY=0
```

Its safe contract example uses `GATE_BACKGROUND_OBSERVED_VALIDATE_ONLY=1`; the
non-observed launcher likewise supports `GATE_BACKGROUND_VALIDATE_ONLY=1`.
Neither validation mode creates a daemon, test worker, or attestation. Both
background targets validate policy before launching and explicitly forward the
profile, resume, batch size, worker limit, and captured candidate SHA.

The gate performs read-only configuration and exact-candidate admission before
acquiring its owned lease. Lease acquisition still precedes every stateful gate
recipe; failed admission or failed acquisition starts no gate phase. The existing
`.NOTPARALLEL: gate gate-refresh` is retained. Seven real Make checks execute the
actual prerequisite header for native/release policy and inject failure at each
check, including under `-j2`. The old lock fixture's first-token assumption is
conformed to this documented admission boundary, while refresh ordering,
release calls, active-owner protection, and every other assertion remain intact.

This follows the long-lived
[Make prerequisite-order discussion](https://stackoverflow.com/questions/9159960/order-of-processing-components-in-makefile):
prerequisite position is not a general parallel-execution guarantee. Gludd keeps
its explicit serialization and tests observable phase execution, rather than
equating a read-only policy check with a protected stateful phase. No serving
process, deployment change, lease bypass, or validation-budget increase follows.

Release admission rejects unknown profiles, `PYTEST_CMD`, extra `PYTEST_ARGS` or
`PYTEST_ADDOPTS`, dirty or focused-reproduction exemptions, producer
validation-only mode, more than one worker, overridden preflight selection, and
a relaxed typecheck error limit. These checks precede heavy work. The producer
also pins `--batch-workers 1` and carries `--watchdog-owned-gate` when the gate
owns it. A failed locked dependency sync stops before any test runner executes.

## Evidence and failure ownership

Make captures `GATE_CANDIDATE_SHA` at admission and retains that expected commit
across recursive commands. The release driver checks a clean exact identity
before allocating test resources and again after the producer completes. The
full gate repeats the identity check after smoke and before signing its terminal
status, so a code or HEAD change during the run cannot produce a passed gate.

The producer publishes its usual namespaced
`ci-shards/attestation.json`. The driver reuses the existing dual-track validator
to require a terminal local pass, the complete canonical shard set, internally
consistent plans and policy fingerprints, CPython 3.11, and the same clean SHA.
It also requires this gate to refresh the receipt; an unchanged old receipt,
missing receipt, native-policy receipt, or different commit fails closed.
The signed gate's test row names the release profile, candidate SHA, and local
attestation path. Hosted exact-SHA evidence and release readiness remain
separate release admission requirements.

GNU Make turns recipe failures into its own exit code 2, so an owned producer
cancellation can lose its original 130 or 143 code at that boundary. The driver
recovers a cancellation only from a freshly written canonical failure receipt
with verified identity and policy. It retains ordinary nonzero child results,
never upgrades failure to pass, and uses the existing cancellation finalizer to
write a signed terminal failure and release its locks and temporary directory.
Direct child cancellation codes retain the same cleanup path.

## Reviewed fixture finding in the secrets baseline

The public `GATE_PREFLIGHT_TARGETS` rejection fixture triggered the pinned
detect-secrets 1.5.0 Base64-entropy heuristic during a real pre-push check.
Its literal argument selects a local Make fixture target; it is not a credential,
does not authorize any service, and is deliberately rejected by release
admission. The unchanged test continues to exercise that real guard.

An observed scan using the existing baseline manager and policy found exactly
one new identity and no removed identities. Review reconciled only that finding
into `.secrets.baseline`, labelled `is_secret=false`, while retaining every
existing finding, audit label, scanner setting, threshold, and exclusion.
No test rewrite, inline pragma, plugin disable, file exclusion, hook change, or
push bypass was introduced. Content-free classification and scanner-hash
evidence are retained outside the checkout; unknown findings remain unapproved.

The next normal push exposed the same heuristic on the public repository
argument in `tests/unit/test_ci_run_summary_target.py:77`. The canonical owner
again found exactly one added identity and none removed; review labels only that
exact filename/detector/hash as `is_secret=false`. A failing-first regression
derives the detected argument from its unchanged source and pins its exact
identity and review, while existing hook tests keep new identities and duplicate
occurrences blocked. The public repository name is not an authentication value.

Validate the canonical artifact without changing it:

```text
make secrets-baseline-check SECRETS_BASELINE_FILE=.secrets.baseline POLICY=config/detect_secrets_baseline_policy.json REPO_ROOT=. EXECUTABLE=detect-secrets
```

The existing read-only hook still scans source using a disposable baseline copy
and refuses every new path/detector/hash occurrence. Audit labels do not alter
that detection boundary. [Upstream's pinned audit guide][secrets-audit] explains
review labels; its [design reference][secrets-design] explains identity and
volatile line metadata. A [practitioner report from 2022-10-22][secrets-forum]
records a detector mistaking a source identifier for a secret. That report
involves a different detector, but reinforces reviewing the actual consumer and
identity rather than broadly suppressing a heuristic. This baseline-only
reconciliation allocates no environment and changes no deployment or release
admission policy.

### Unambiguous task-path registration

The normal development push exposes a separate KeywordDetector match in the
task metadata. The unchanged managed scanner finds exactly one new identity at
`TASKS.md` line 47 and no removals. A bounded replay of the pinned upstream
detector identifies 3,311 characters of public registered filenames and proof
text after the baseline filename. Its exact SHA-1 identity is
`3d87c12867a9e3a7e5aeeeaf9d820091df6cec8c`; there is no authentication value in
that span.

The canonical owner retains every previous identity and adds only that reviewed
finding. Its audit label records a false positive, not a detector exemption.
The new failing-first regression derives the span from the unchanged source and
requires exactly one filename/detector/hash finding with the review label.
No file registration, task status, source text, scanner, hook, exclusion or
threshold changes. The independently reviewed public fixture remains required.

[The pinned upstream keyword implementation][secrets-keyword] defines that
quoted-value heuristic. The practitioner report above concerns a different
source token, so it supplies context, not proof of this particular collision.
Actual full-source and whole-push admission must still verify the reviewed
candidate before hosted or release success is claimed.

## Verification and practitioner evidence

Behavioral regressions execute the real shell driver, the extracted real Make
test-phase recipe, and the real local attestation validator while replacing only
the Make producer child with a bounded fixture. They cover both resume modes,
ordinary failure, cancellation, cancellation translated into Make exit 2,
diagnostic rejection, dirty checkout rejection, stale/native/wrong-SHA evidence,
locked-sync failure, and explicit background policy forwarding. Existing native
gate concurrency and background lifecycle tests continue to pass. These focused
checks do not run a full gate or certify a release candidate.

Cross-interpreter fixture proof on 2026-10-12: all 36 cases passed on CPython
3.11.14 (`make test-specific-pyver`) and 3.14.0 (`make test-files`), each with
`-W error` and one worker. The receipt fixture explicitly derives its policy and
digest from `release_execution_policy()`, retaining the canonical shard plans;
the interpreter executing the harness and real validator does not redefine the
modeled locked 3.11 producer. An explicit metadata regression failed first on
3.14 before this correction. A fresh 3.14-policy receipt with a consistent digest
is still rejected on both runtimes, as are all existing negative cases. These
checks change neither production admission policy nor release-candidate proof.

Research reviewed on 2026-10-12 supports reusing the established interfaces:

- The [GNU Make exit-status documentation][make-exit] distinguishes Make's own
  return codes from individual recipe results. The canonical failure receipt
  preserves the producer's cancellation semantics across that boundary.
- The [GNU Make recursion reference][make-recursion] documents inherited
  command-line definitions and cautions against manually expanding `MAKEFLAGS`
  into shell commands. The launch recipes forward policy values explicitly;
  focused test fixtures isolate their parent test-runner flags.
- The long-lived [bug-make discussion from 2020-12-19][make-practitioner]
  explains that diagnostic database output still runs Make recipes unless a
  separate execution mode says otherwise. The profile's safe example therefore
  runs policy validation rather than a dry run of the full recursive gate.
- [pytest-timeout issue #60][pytest-timeout], opened 2020-03-31 and referencing a
  2016 pytest mailing-list discussion, reports that CI timeouts can prevent
  graceful cleanup and a final report. This profile retains bounded observation,
  signed cancellation, and the existing timeout policy.

ZDD is candidate rejection: this profile does not promote a branch, create a
release tag, or change a serving deployment. It adds no persistent process and
uses the existing gate lock, namespaced resource root, host-wide heavy-operation
semaphore, canonical serial runner, and background owner. One test worker runs
at a time; logs remain observable and temporary test roots are removed by their
owner. Rollback is the isolated profile, contract, regression, and documentation
commit; ordinary native development gates retain their prior test policy.

[make-exit]: https://www.gnu.org/software/make/manual/html_node/Running.html
[make-recursion]: https://www.gnu.org/s/make/manual/html_node/Quick-Reference.html
[make-practitioner]: https://lists.gnu.org/r/bug-make/2020-12/msg00055.html
[pytest-timeout]: https://github.com/pytest-dev/pytest-timeout/issues/60
[secrets-audit]: https://github.com/Yelp/detect-secrets/blob/v1.5.0/docs/audit.md
[secrets-design]: https://github.com/Yelp/detect-secrets/blob/v1.5.0/docs/design.md
[secrets-forum]: https://github.com/Yelp/detect-secrets/issues/629
[secrets-keyword]: https://github.com/Yelp/detect-secrets/blob/v1.5.0/detect_secrets/plugins/keyword.py
