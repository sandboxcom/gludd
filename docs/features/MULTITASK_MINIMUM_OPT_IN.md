# Multitask Minimum Opt-In Contract

## Problem

The multitask plugins retained conflicting values of three, six, and ten as
recommendations, floors, targets, and hard ceilings. That contradicted the
active cost-efficiency directive, which prefers
inline execution for simple work and requires delegation only when it is useful.
The text-completion path also compared directly with `MIN_DISPATCHES`, bypassing
the intended effective-minimum abstraction.

## Contract

- Three is the absolute dispatch ceiling and recommended configured width.
- `.opencode/lib/multitask_config.ts` is the single source of truth, and every
  plugin clamps environment and state-file overrides to that hard ceiling.
- No minimum is mandatory unless `GLUDD_MIN_DISPATCHES` or
  `GLUDD_MULTITASK_MIN_DISPATCHES` is explicitly present. The compatibility
  controls `CLAUDE_AGENT_FLOOR`, `GLUDD_DISPATCH_FLOOR`, and
  `GLUDD_SESSION_START_MIN_DISPATCHES` likewise default to zero.
- An explicit zero disables minimum enforcement.
- Registered OpenCode plugins, their implementation modules, generated Claude
  hooks, runtime settings, and active agent skills all follow the same contract;
  no secondary layer may restore a hidden floor.
- User-supplied floor directives and persisted legacy state are clamped to three;
  the obsolete always-active ten-agent directive is removed during state load.
- Worktree-backed coding agents have a stricter hard cap of two because they
  consume disk and can contend for the same checkout.
- Tool, zero-streak, thin-wave, and warning paths use
  `REQUIRED_DISPATCHES`, never the recommendation directly.
- A configured minimum of one accepts a one-dispatch wave. Message-shape
  enforcement cannot silently raise that explicit minimum to two.
- With the zero default, a voluntary dispatch and its result never create a
  refill warning or replacement obligation.
- Subagent bypass, disengage, pressure release, pending-work, and the hard
  maximum remain unchanged.

## Practitioner evidence

OpenCode issue 17169 reports a subagent retry loop producing excessive API use
and costs, including more than $100 of accumulated impact:
<https://github.com/anomalyco/opencode/issues/17169>.

OpenCode issue 17721 documents how unbounded recursive subagent spawning can
grow exponentially in sessions, tokens, and time:
<https://github.com/anomalyco/opencode/issues/17721>.

Codex issue 35383 reports 118 unmanaged run copies consuming 202 GB in three
days while the configured keep-count appeared ineffective. This supports a
small hard concurrency cap plus explicit ownership and cleanup rather than
optimistic reliance on worktree retention settings:
<https://github.com/openai/codex/issues/35383>.

Claude Code issue 59019 reports writes from nominally isolated parallel
worktrees leaking into the parent checkout. This reinforces Gludd's existing
single-writer rule and the decision not to create filler agents merely to occupy
capacity:
<https://github.com/anthropics/claude-code/issues/59019>.

These reports support an explicit operator choice for mandatory wave width
while retaining a hard safety ceiling.

CPython issue 139783 shows that source-boundary extraction can return only a
declaration when an otherwise harmless formatting boundary changes:
<https://github.com/python/cpython/issues/139783>. CPython issue 105799 records
the maintenance cost of tests that depend on interpreter-specific AST and
bytecode implementation details:
<https://github.com/python/cpython/issues/105799>. Gludd's structural tests
therefore inspect the helper that owns a policy and separately assert that thin
wrappers delegate to it. They do not require policy literals to be duplicated
inside every wrapper.

## ZDD, security, and resources

This is control-plane policy and causes no application data-plane downtime.
The hard three-agent ceiling, subagent isolation, pending-work checks, and
fail-closed configured-minimum behavior remain. Default simple work no longer
allocates needless processes or model spend.

OpenCode loads enforcement plugin source at startup. After this source change,
OpenCode must be restarted before the live session is considered updated. Test
and repository work continues against the existing runtime until that restart.

## Verification

Structural tests cover configured/unconfigured/zero semantics and every active
enforcement surface. Runtime plugin tests prove repeated unconfigured mutations
are allowed across the floor, cumulative-floor, multitask, delegate, directive,
and session-start layers; configured minima block; the ceiling remains three;
oversized overrides clamp safely; legacy ten-agent state is discarded; and
subagents still bypass parent enforcement. The runtime matrix also executes the
configured-one boundary and the zero-floor voluntary-dispatch/result path so
advisory text cannot restore a hidden minimum. Plugin
syntax/load/import, hot-module runtime, task/spec, and collection gates must all
pass.

The CI-wait discipline mirror must independently require the same ownership
semantics: zero subagents is valid when no independent deliverable exists,
filler work is forbidden, and three useful owners is the ceiling. It must also
reject the obsolete claim that a zero-agent CI wait is itself a policy
violation.

The active-policy regression scans `AGENTS.md`, both operational specification
files, all active OpenCode skills, and Claude agent prompts together. It rejects
mandatory session-start waves, automatic replacement/refill rules, polling-only
subagents, fixed post-result read quotas, inherited ten-agent language, and
claims that observable foreground gates are forbidden. This scan failed first
on stale behavioral specs and the background-test-runner skill, which proved
that changing runtime defaults alone was insufficient. The repaired policy now
keeps each operation with one explicit owner: foreground work is valid when
bounded and observable, background work is optional, and reassignment occurs
only for another concrete independent deliverable.

Policy text must not claim enforcement that the repository cannot execute.
The enforcement-claim verifier therefore resolves every referenced script and
Make target. During this repair it rejected 17 of 200 specs that named
nonexistent audit or recovery targets; those claims now reference only the
existing implementation owner, and the verifier passes all 200 specs.
