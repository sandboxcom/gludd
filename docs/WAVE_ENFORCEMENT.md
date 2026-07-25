# Dispatch Wave Enforcement

`enforce-floor.ts` treats a dispatch wave as the task or agent calls made in
one assistant message. When open work exists, a wave is exactly 10 dispatches.
The plugin rejects an eleventh dispatch and blocks later inline work when the
previous message contained an undersized wave. `GLUDD_DISPATCH_WAVE_WIDTH` is
only for isolated plugin e2e tests; production defaults to 10.

## Pre-dispatch audit

Before the first dispatch, the plugin writes
`/tmp/gludd-dispatch-preflight.json`. This is an observable record of the
required width and the first ten pending work items. It is marked complete
after the tenth dispatch.

The orchestrator must perform this audit before composing a wave:

1. Select ten concrete deliverables from the task ledger or current failures.
2. Deduplicate file ownership and do not assign shared infrastructure to more
   than one worker.
3. Classify every deliverable as an enhancement or a fix; at least half of a
   multi-task wave must be enhancements.
4. Keep research serialized and limit concurrent coding or test work to two
   disjoint file sets, even when ten agents are dispatched.
5. Put all ten dispatches in one assistant response, then inspect results and
   start the next audited wave before inline mutation.

The preflight file is evidence, not a substitute for the audit. A stale file
does not authorize an undersized wave.
