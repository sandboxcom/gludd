# Monotonic Debounce and Throttle State

## Problem and Root Cause

The deep debounce suite carried private prototype classes instead of importing
the product implementation. Those copies used numeric zero as both a valid
clock reading and a "never fired" sentinel, so leading calls, resets, trailing
delivery, max-wait, and instance isolation failed when a deterministic clock
started at zero. The async implementation also stored the leading callback and
the trailing timer in the same task slot. Enabling both edges therefore
cancelled the callback it had just scheduled.

The adjacent timing replay exposed the same state-identity error in
`StallWatchdog.start`: an API documented as idempotent overwrote the original
start time and deadline. A repeated registration must leave the live operation
unchanged until `finish` closes that generation.

## Contract

- `Debouncer`, `Throttle`, and `AsyncDebouncer` are production APIs. Tests and
  callers do not maintain substitute state machines.
- A synchronous caller may inject a monotonic-compatible clock. `None`, not a
  timestamp such as `0.0`, represents missing state.
- Each trailing debounce call restarts the quiet-period deadline from that
  call. When configured, max-wait caps that moving deadline for a sustained
  burst.
- A throttle admits the configured leading edge at most once per window and
  retains only the latest suppressed call for its trailing edge. A late
  deterministic tick records the logical timer deadline, not wall-clock delay,
  so test scheduling jitter cannot move the next window.
- A debounce max-wait may be shorter than its quiet-period wait. It still must
  be positive and finite. Every wait must be finite and nonnegative, preventing
  `NaN` or infinity from creating immortal pending work.
- Async scheduling and timestamp comparison share the running event loop's
  monotonic clock. The leading callback and replaceable trailing timer have
  separate ownership, so scheduling one cannot cancel the other.
- V2 clock observations must be finite. Backwards observations are clamped to
  the instance's previous observation; they cannot move a quiet deadline earlier.
  A simulated drive deadline must also be finite and cannot rewind its clock.
- A fresh leading burst establishes its own max-wait epoch before invoking user
  code, preserving trailing work queued by a reentrant synchronous callback.
- An async trailing generation may publish only while it is the current task.
  Equal callback arguments cannot authorize a cancelled predecessor to fire.
- V2 `aclose` closes intake and cancels/drains owned leading/trailing
  generations other than its calling callback. A self-closing callback remains
  registered until completion, so an external close can still cancel and drain
  it. An owned callback closing an already-closed instance returns without
  joining the close in progress; external callers still cancel and drain live
  tasks. Reset does not reopen a closed instance. Completed payload/task references
  are released; background callback exceptions are retrieved and logged by their
  owning done callback.
- `cancel` removes delayed trailing work; `reset` additionally restores the
  leading-edge admission state. Cancellation remains cooperative at the next
  event-loop checkpoint.
- Re-registering a live watchdog operation is a no-op. Callers must `finish`
  before reusing an operation identifier with a new start or deadline.

## Mature Primitives and Practitioner Evidence

The implementation uses Python's maintained primitives rather than a custom
wall-clock or scheduler: [`time.monotonic`](https://docs.python.org/3/library/time.html#time.monotonic),
the event loop's [`loop.time`](https://docs.python.org/3/library/asyncio-eventloop.html#asyncio.loop.time),
`asyncio.create_task`, and `asyncio.sleep`. The
[`asyncio` task documentation](https://docs.python.org/3/library/asyncio-task.html#creating-tasks)
also recommends retaining a strong reference to background tasks until their
completion, which the live-leading-task set does with automatic discard.

The edge and max-wait vocabulary follows the mature
[`lodash.debounce` contract](https://lodash.com/docs/#debounce), including the
rule that a both-edge single call does not produce a duplicate trailing call.
Lodash is not added as a Python dependency; only its long-lived behavioral
model informs this process-local implementation.

Long-lived reports show why these details are durable operational concerns:

- A [2015 leading-edge debounce question](https://stackoverflow.com/questions/34552038/debounce-function-that-fires-first-then-debounces-subsequent-actions)
  records the recurring requirement to fire once immediately and suppress the
  rest of a burst.
- A [2016 asyncio scheduling and cancellation report](https://stackoverflow.com/questions/40016501/how-to-schedule-and-cancel-tasks-with-asyncio)
  demonstrates that delayed work needs an owned task handle.
- A [2020 pending-task destruction report](https://stackoverflow.com/questions/63585835/cancelled-asyncio-tasks-result-in-task-was-destroyed-but-it-is-pending)
  documents the shutdown warnings caused by abandoning cancellation without a
  loop turn.
- A [2021 cancellation report](https://stackoverflow.com/questions/68759284/how-to-make-asyncio-cancel-to-actually-cancel-the-task)
  explains that `Task.cancel()` requests cancellation and delivery occurs when
  the event loop next runs the task.
- Python's long-running [clock-resolution issue](https://bugs.python.org/issue31539)
  shows that event-loop timers inherit platform monotonic-clock resolution;
  deterministic tests must assert logical deadlines rather than sub-resolution
  elapsed-time accidents.

## Security, Resources, and Observability

Constructor validation fails closed before mutating state or creating tasks.
Negative, non-finite, and edge-less configurations cannot silently retain work.
Arguments remain process-local and callbacks execute with the caller's existing
authority; the primitives add no serialization, network, filesystem, or command
boundary.

Each instance has one current replaceable trailing timer and one latest
argument record. Superseded timers remain owned until cancellation finishes;
they cannot publish even when their payload equals the current generation's
payload. Leading and trailing task sets retain only live work and discard each
task on completion. Async close drains a stable snapshot through the existing
`cancel_and_drain_tasks` helper, including superseded timers that are still
cooperating with cancellation. It excludes the caller from that snapshot and
leaves a self-closing callback registered until its native done callback runs.
External close still sees that live task and preserves caller cancellation.
Callback failures are retrieved and logged, while
synchronous errors propagate immediately. Cooperative callbacks must still yield
and finish; the primitive introduces no forced task termination or retry.
No retry loop, thread, or hidden polling process is introduced.

## Zero-Downtime Delivery and Rollback

The state is in-memory and has no schema or persisted representation. Deploy
new workers beside old workers, shift traffic only after the strict focused
suite and coverage gates pass, and drain old workers so their already-admitted
timers reach completion or normal shutdown cancellation. Mixed versions do not
share debounce state.

Rollback sends new traffic to the previous immutable worker version and drains
the repaired workers. No data reversal or migration is required. Rollback must
not restore the test-local prototypes; the production API remains the single
source of timing semantics.

## Verification

The focused suite pins leading, trailing, both-edge, max-wait, pending, reset,
zero-epoch, cancellation, non-finite input, and multi-instance isolation. The
adjacent timing suite pins idempotent watchdog generations and immutable
verdicts. Tests run with warnings promoted to errors, and touched production
files must meet the repository's line-and-branch coverage floors.

## v0.1.2 committed-source recovery (2026-10-26 UTC)

Completed S83.116 retains its original implementation and quiet-period evidence
at 13b933128/dc5082aac. The later committed merge
15cadf57648f396189bfdf4ac4c31243dc0ec785 also contains accepted debounce source
blob 2e25d50dc58e55a0a94b31baa79fec58c5ccccdf and adversarial test blob
cc2515e7338203197e9d61636aca08adb3ed543c. Those bytes were absent from the current
release source despite the older completed-task entry. Merge parents are
03c0d4c9d97f944197f72d7e573f6c78b543debe and
dcab7071d8887eaaad21c567616e15970c580b93.

Receiving uses feature/v012-debounce-recovered-20261026 at
da86949df467666b42971812cb70eeb7a4cd9e7b. Its old debounce source exactly equals
the merge's first-parent source. Native git-show-file-to exports only the
committed debounce source and tests; no locking source or whole merge is
received. The existing cooperative task-drain helper remains the lifecycle
owner. The ten original adversarial controls retain every assertion.

Fresh old-source replay reproduces seven failures and three passes in0.78s,
watcher native2/2.225s, at
.gate-logs/observed/debounce-recovery-red/debounce-recovery-red311-da86949.json.
They expose stale max-wait epochs, backwards deadlines, erased reentrant work,
equal-payload stale-generation execution, unobserved callback exceptions,
post-close reopening and retained finished task/payload state.

The original combined close test fails at its post-close RuntimeError assertion;
its one-loop-turn drain assertion happened to pass old code. That result does
not independently prove superseded-task drain ownership. An additive native
boundary control records the tasks actually passed to the real existing drain
helper and preserves actual cooperative cancellation. On old source it fails
because the live superseded timer is absent from that transfer, while the
current timer is present. Receipt
.gate-logs/observed/debounce-recovery-drain-red/debounce-recovery-drain-red311-da86949.json
retains that separate one-case RED (0.54s, watcher native2/1.778s). Both controls
release only their own resistant coroutine in finally and leave no live work.

Scoped Ruff, strict typing of source plus adversarial owner, producer
docstrings, environment-write and line-limit checks pass through the serial
native owner at .gate-logs/observed/debounce-recovery-static/debounce-recovery-static311-da86949-typing2.json
(native0/3.802s). The initial strict check correctly rejected access through an
implicit imported helper attribute; the new test now imports the real canonical
helper explicitly, with every assertion unchanged. Its earlier failure remains
at debounce-recovery-static311-da86949.json. Locked native Markdown also passes.
The repaired complete adversarial owner passes all eleven cases (0.64s,
watcher native0/2.005s), at
.gate-logs/observed/debounce-recovery-green/debounce-recovery-green311-da86949.json.

The unchanged full-scope resource metadata owner, WRITE=0, recognizes both
current task acquisitions as owned but refuses their stale inventory identities.
At debounce_v2.py:222:27 it reports
c7c3312001d76fe396c89327c24479df6eb286a6b672d7397978c351e720f780;
at 246:15 it reports
613dbdf9f3fe59ffeb2eb06b00438a9d9d828b497e62c57940c9bd6f636ef798.
The old189:27 and219:21 records are stale. Native status2/5.7s is retained at
.gate-logs/observed/debounce-recovery-resource-read/debounce-recovery-resource-read311-da86949.json.
Root owns native inventory reconciliation and exact-path staging; no entry,
threshold or cleanup policy is waived.

Root reconciled the complete native ownership inventory with WRITE=1 and then
ran independent WRITE=0 admission: both passed, retaining 265 resources. The
configuration diff admits the two current debounce acquisition hashes/teardowns
and refreshes four pre-existing line-only stream-command/task-watchdog
coordinates. No ownership field, threshold or cleanup policy is waived. Root
staged the three owned paths plus config/resource_ownership_inventory.json.
Its staged-content guard passed in 6.618s with zero new clones and unchanged
source/test bytes. Those native inventory/guard results remain in Root's actual
tool transcript; no synthetic observation is created here.

The complete current coverage/receiving union contains these nine whole owners:

~~~text
tests/unit/test_debounce_v2.py
tests/unit/test_debounce_v2_deep.py
tests/unit/test_debounce_v2_adversarial.py
tests/unit/test_debounce_deep.py
tests/unit/test_timing_deep.py
tests/unit/test_async_lifecycle.py
tests/unit/test_s83_116_debounce_deck_sync.py
tests/unit/test_markdown_docs_deep.py
tests/unit/test_resource_ownership_gate.py
~~~

The native coverage-files owner uses
/tmp/gludd-v012-debounce-coverage-da86949.ini with the complete production file,
branch measurement, parallel data and mandatory physical source binding:

~~~ini
[run]
branch = True
parallel = True
include =
    ${GLUDD_COVERAGE_SOURCE_ROOT?}/src/general_ludd/util/debounce_v2.py

[report]
show_missing = True
~~~

Its existing recipe captures physical `pwd -P` and exports that required variable;
the caller does not choose the source identity. Both outer installed runtime
tuples select the registered 3.11/3.12 toolchains, with no synchronization/download.
Serial execution retains native 600-second total/180-second quiet focused bounds, warnings as
errors, literal ID= and all original tests/assertions. These are receiving
bounds; canonical runtime/promotion 90/60 and release 21600 are unchanged.

Before independent self-close review, both complete runs pass 215 cases with
zero skips/errors. These receipts apply to that earlier source only. Native
audits preserve 85aggregate/75each-file LINE and BRANCH, with no exclusions or
suppressed warnings:

| Runtime | Cases / pytest time | Whole-file raw line | Whole-file raw branch | Native elapsed |
| --- | --- | --- | --- | --- |
| 3.11.14 | 215 /30.13s | 198/206 (96.12%) | 62/70 (88.57%) | 32.055s |
| 3.12.15 | 215 /29.60s | 198/206 (96.12%) | 63/70 (90.00%) | 32.340s |

Exact native terminal status 0 receipts are
.gate-logs/observed/coverage-files/20261026T144604Z-70071-4ce2ecb2.json
and 20261026T144822Z-77478-822305a9.json. Reports are
.gate-logs/debounce-recovery-profile{311,312}-da86949.json, with native audit
reports coverage-20261026T144635Z.json and coverage-20261026T144854Z.json.
The complete resource-owner consumer validates the admitted inventory in each
run. Earlier READ2/static REDs remain historical failures; no old receipt is
recertified. No performance improvement is inferred from these measurements.

That first evidence prose was a documentation-only delta after staged
admission, with 32 complete document/deck cases passing on each runtime and
locked Markdown passing. Independent review then found the additional self-close
failure below; neither the 215-case receipts nor the earlier staged guard
certifies the subsequent source. Root retains fresh exact staging/content
checks, collection, guarded commits and integration. Historical evidence and
focused results supply no canonical gate, exact-SHA CI, ZDD deployment or release
proof.

### Independent review: callback self-close

The first recovered `aclose` snapshot included its calling callback. A trailing
callback was also cancelled before the native drain, and the helper could gather
the caller itself. Two parameterized leading/trailing controls retain all eleven
earlier cases and assert real close/ownership behavior plus external close during
caller cancellation. A safety wrapper checks the actual native drain snapshot
before delegating unchanged cooperative cleanup; RED therefore proves invalid
self-transfer without allowing recursive self-gather to corrupt pytest's loop.

A firsthand [June 2025 Python Help report](https://discuss.python.org/t/asyncio-tasks-a-cancels-and-awaits-b-b-cancels-and-awaits-a-recursion-error/96079)
reproduces recursive cancellation when tasks cancel and await one another.
The [2019 CPython issue](https://github.com/python/cpython/issues/80637)
records the older deadlocked-task cancellation case. Those reports explain a
durable cyclic-wait risk; they do not prove this Gludd self-close defect.
The native RED below supplies the actual caller-transfer evidence.
Python's [Task.cancel documentation](https://docs.python.org/3.12/library/asyncio-task.html#asyncio.Task.cancel)
also explains that cancellation reaches an awaited Future; gather cancellation
propagates even with return_exceptions enabled. The repair breaks self-ownership
without suppressing caller cancellation or weakening the established drain.

Fresh 3.11 RED records four failures in 0.60s, watcher native2/1.783s:
.gate-logs/observed/debounce-self-close-red/debounce-self-close-red311-da86949.json.
All four fail at actual caller inclusion; the trailing tasks are already
cancelling. The small `aclose` repair excludes the caller, detaches its trailing
handle without cancelling it, and relies on the existing done callbacks to remove
terminal tasks. Live self-closers remain registered for an external close.
The real drain helper and exception propagation are unchanged.

Complete current adversarial acceptance is 15 passed in 0.51s, watcher
native0/1.669s, at
.gate-logs/observed/debounce-self-close-green/debounce-self-close-green311-da86949.json.
It covers normal completion after self-close, rejection of new intake, retained
live ownership, external cancellation/drain through resistant cleanup, and
propagated caller CancelledError on both edges.

Ruff, strict two-file typing, producer docstrings, environment-write checks and
line limits pass in the serial static owner. Its final full-scope WRITE=0
resource check correctly refuses the changed trailing acquisition's teardown
identity: at 246:15, 3d21c868366ac9e5f7b58dc7a59c73215b66911c83ccae70d6db29f2a3443f92
replaces 613dbdf9f3fe59ffeb2eb06b00438a9d9d828b497e62c57940c9bd6f636ef798.
The leading identity is unchanged. Native2/9.195s remains at
.gate-logs/observed/debounce-self-close-static/debounce-self-close-static311-da86949.json.
Root owns inventory reconciliation and fresh exact staging/content admission.
That intermediate 219-case union was not executed: independent review found the
second cycle below before broader receiving.

### Idempotent callback close during an active close

Two live leading callbacks can still form an indirect cycle after the direct
self-transfer repair. Callback A closes and drains B; cancellation enters B's
finally block, where B closes and attempts to drain A. Excluding only the current
task does not break A-to-B-to-A ownership.

An additive native-transfer guard retains all fifteen existing controls and
checks the actual caller-to-snapshot graph before allowing recursive asyncio
cancellation. The two-callback regression records one failure in 0.63s,
watcher native2/1.917s:
.gate-logs/observed/debounce-close-cycle-red/debounce-close-cycle-red311-da86949.json.
The failed edge is B's return transfer to A, not a synthetic timing inference.

The additional repair returns immediately when intake is already closed and the
caller is one of the still-owned callbacks. It does not join another callback's
close. First closes and external closes keep their real cancellation/drain path,
and all live callbacks remain in the original registries until done. No helper,
exception propagation, ownership threshold or original assertion changes.

The complete sixteen-case adversarial owner passes in 0.57s, watcher
native0/1.808s:
.gate-logs/observed/debounce-close-cycle-green/debounce-close-cycle-green311-da86949.json.
The new case requires B's finally close and A's initiating close to return,
preserves B's original CancelledError and rejects late intake.

Current Ruff, strict two-file typing, producer docstrings, environment-write and
line-limit checks pass. The final native full-scope WRITE=0 resource check also
passes all 265 resources without further inventory mutation. The serial native0
receipt (9.591s) is
.gate-logs/observed/debounce-close-cycle-static/debounce-close-cycle-static311-da86949.json.
Locked Markdown passes for the updated research/contract. Root's refreshed
four-path staged-content guard also passes, native0/6.427s with zero new clones,
at .gate-logs/observed/debounce-recovery-content/debounce-recovery-cycle-content-20261026.json.
That receipt precedes the final evidence prose below.

The same nine complete owners, whole-file profile, physical source binding,
600/180 receiving bounds, installed runtime tuples and 85aggregate/75each-file
LINE and BRANCH floors receive the final source serially. Every original
assertion remains. Both native audits pass with zero excluded lines:

| Runtime | Cases / pytest time | Whole-file raw line | Whole-file raw branch | Native elapsed |
| --- | --- | --- | --- | --- |
| 3.11.14 | 220 /30.43s | 199/207 (96.14%) | 66/74 (89.19%) | 32.306s |
| 3.12.15 | 220 /28.41s | 199/207 (96.14%) | 67/74 (90.54%) | 31.082s |

Exact native terminal status0 receipts are
.gate-logs/observed/coverage-files/20261026T152825Z-705-75d3314c.json and
20261026T152928Z-4688-637b300e.json. Raw reports are
.gate-logs/debounce-close-cycle-profile{311,312}-da86949.json; native audits are
coverage-20261026T152857Z.json and coverage-20261026T152959Z.json. The complete
resource-owner consumer passes against all 265 admitted resources on each
runtime. Source, tests and inventory stay unchanged between runtimes and after
acceptance; the final delta records only these actual results.

Final documentation checks use the complete Markdown/deck owners on both
runtimes and locked Markdown. Root retains exact restaging/content checks for
this evidence delta, normal fresh collection, independent source review and
guarded commits/integration. The earlier 215-case and 15-case receipts retain
their actual prior-source scope; the intermediate 219-case union was never run.
No result here supplies a canonical gate, exact-SHA CI, ZDD deployment or
release/promotion proof, and no speed improvement is inferred.
