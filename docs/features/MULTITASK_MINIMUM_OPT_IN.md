# Multitask Minimum Opt-In Contract

## Problem

The multitask plugin retained ten as both recommendation and unconditional
minimum. That contradicted the active cost-efficiency directive, which prefers
inline execution for simple work and requires delegation only when it is useful.
The text-completion path also compared directly with `MIN_DISPATCHES`, bypassing
the intended effective-minimum abstraction.

## Contract

- Ten remains the absolute dispatch ceiling and recommended configured width.
- No minimum is mandatory unless `GLUDD_MIN_DISPATCHES` or
  `GLUDD_MULTITASK_MIN_DISPATCHES` is explicitly present.
- An explicit zero disables minimum enforcement.
- Tool, zero-streak, thin-wave, and warning paths use
  `REQUIRED_DISPATCHES`, never the recommendation directly.
- Subagent bypass, disengage, pressure release, pending-work, and the hard
  maximum remain unchanged.

## Practitioner evidence

OpenCode issue 17169 reports a subagent retry loop producing excessive API use
and costs, including more than $100 of accumulated impact:
<https://github.com/anomalyco/opencode/issues/17169>.

OpenCode issue 17721 documents how unbounded recursive subagent spawning can
grow exponentially in sessions, tokens, and time:
<https://github.com/anomalyco/opencode/issues/17721>.

These reports support an explicit operator choice for mandatory wave width
while retaining a hard safety ceiling.

## ZDD, security, and resources

This is control-plane policy and causes no application data-plane downtime.
The hard ten-agent ceiling, subagent isolation, pending-work checks, and
fail-closed configured-minimum behavior remain. Default simple work no longer
allocates needless processes or model spend.

OpenCode loads enforcement plugin source at startup. After this source change,
OpenCode must be restarted before the live session is considered updated. Test
and repository work continues against the existing runtime until that restart.

## Verification

Structural tests cover configured/unconfigured/zero semantics. Runtime plugin
tests must prove unconfigured mutations are allowed, configured minima block,
the ceiling remains ten, and subagents still bypass parent enforcement. Plugin
syntax/load/import, hot-module runtime, task/spec, and collection gates must all
pass.
