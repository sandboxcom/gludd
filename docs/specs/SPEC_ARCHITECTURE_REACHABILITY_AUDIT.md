# SPEC: Architecture Reachability Audit

**Spec ID:** SPEC_ARCHITECTURE_REACHABILITY_AUDIT
**Status:** implemented
**Created:** 2026-08-13
**Owner:** release engineering

## Problem

The architecture audit treated textual proximity as reachability. It required
`EventLoop.tick()` to contain direct `_phase_*` calls even though the current
implementation dispatches the explicit `PHASE_ORDER` through
`_run_phase_range()`, and it ignored phase methods inherited from
`EventLoopHandlers`. A regular expression also missed multiline router
registration calls and classified private router helpers as public routers.

Those false negatives made a healthy extracted architecture look disconnected
and encouraged restoring monolithic code solely to satisfy the audit.

## Behavioral contract

- `tick()` must call `_tick_once()`, which must reach
  `_run_phases()` or `_run_phase_range()`.
- `_run_phase_range()` must resolve `_phase_<name>` dynamically from the
  declared phase name.
- Every required phase must exist on either `EventLoop` or its
  `EventLoopHandlers` mixin.
- Router registration is identified from Python AST call arguments, including
  multiline calls; the expected and observed router inventories must match.
- Private helper modules such as `routers/_util.py` are outside the public
  `register(app, daemon_state)` contract.
- Public daemon-wiring entry points must either be called by daemon startup or
  be explicitly identified as supported library entry points.

The audit remains fail-closed: a missing phase, broken dispatcher edge,
unregistered router, unexpected router, or undocumented wiring entry point
fails the gate.

## Zero-downtime deployment

This feature changes static release validation only. It does not modify the
running daemon, event-loop schedule, HTTP routes, or deployment lifecycle.
It therefore preserves the existing rolling/ZDD path while making pre-release
validation accurately follow the runtime delegation boundary.

## Practitioner evidence

Vulture's maintainers document that Python's dynamic nature means implicitly
called code can be reported as unused, and their `getattr` example reproduces
the same static-reachability limitation:
https://github.com/jendrikseipp/vulture

A practitioner issue opened in 2018 likewise shows that AST analysis must
model execution context rather than treating every parsed reference alike:
https://github.com/jendrikseipp/vulture/issues/152

## Verification

`tests/unit/test_call_graph_deep.py` pins the dispatcher chain, mixin-resolved
phase inventory, AST router inventory, public wiring boundary, and router
signature contract.
