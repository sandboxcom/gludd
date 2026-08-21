# OpenCode Delegation Depth Enforcement

## Contract

Gludd permits delegated agents to use ordinary tools at every supported depth,
but denies `task`, `agent`, and `workflow` dispatches when
`OPENCODE_DEPTH >= GLUDD_MAX_DEPTH`. The beta4 default maximum is four. Depths
zero through three may delegate; a depth-four agent must complete its assigned
work directly.

Most enforcement plugins must bypass delegated contexts so the orchestrator
remains the single policy owner. `enforce-depth.ts` is the scoped exception:
recursion cannot be bounded if the depth guard bypasses the very agents whose
dispatches it limits. The exception is valid only while the plugin:

- reads `OPENCODE_DEPTH`;
- applies only to dispatch tools;
- contains no generic `OPENCODE_SUBAGENT` or `isSubagent()` bypass; and
- leaves non-dispatch tools available at the boundary.

The manifest verifier, depth checker, and E2E plugin-load contract all encode
that exception independently. A recurrence of the generic bypass fails closed.

## Practitioner and Upstream Evidence

Evidence was reviewed on 2026-08-20.

- OpenCode issue
  [#18100](https://github.com/anomalyco/opencode/issues/18100) reports 47
  sessions and a 20-level recursive delegation chain, with useful work deferred
  to the deepest agent. The reporter requested a hard maximum depth; the issue
  was closed as not planned.
- OpenCode issue
  [#25681](https://github.com/anomalyco/opencode/issues/25681) reports recursive
  self-spawning enabled by a subagent's task permission, without a depth cap or
  cycle detector; the run stopped only when API credit was exhausted. That
  issue was also closed as not planned.
- OpenCode issue
  [#17721](https://github.com/anomalyco/opencode/issues/17721) explains that a
  global explicit task permission can propagate to subagents and defeat the
  framework's ordinary nesting guard. Per-session step limits do not bound a
  tree because every child receives a fresh budget.
- OpenCode issue
  [#5894](https://github.com/anomalyco/opencode/issues/5894) documents an older
  period when plugin tool hooks did not fire for subagent tool calls. Follow-up
  pull request
  [#36238](https://github.com/anomalyco/opencode/pull/36238) added regression
  coverage after current OpenCode behavior began invoking those hooks. Gludd
  therefore tests the delegated hook boundary instead of assuming a particular
  historical host behavior.

These reports make prompt-only anti-recursion instructions insufficient. The
Gludd plugin is a local defense-in-depth boundary until the host provides a
stable, hard recursion limit.

## ZDD and Failure Semantics

- Denial affects only the attempted child dispatch; the current agent retains
  its context and direct tools, so work can continue without a session restart.
- Malformed or negative depth values normalize to zero. Explicitly disabling
  the guard remains an operator action through `GLUDD_DEPTH_ENFORCE=0`.
- Hot-module load failure falls back to the compiled implementation. Tests use
  private hot-module prefixes so stale live-session state cannot produce a
  false pass.
- Plugin source changes do not become active in an already running OpenCode
  process. Commit the change, restart OpenCode, rebuild the hot modules, and
  rerun hook-runtime plus depth checks before claiming live enforcement.

## Verification

The beta4 verification boundary includes:

- depths zero through three allowed and depth four denied;
- dispatch-only enforcement and non-dispatch pass-through;
- the exact depth-plugin exception in the manifest and E2E plugin inventory;
- isolated hot-module subprocesses;
- `make check-depth-limit`, `make test-hook-runtime`, Node compatibility, and
  focused branch coverage at or above the repository 85/75 thresholds.
