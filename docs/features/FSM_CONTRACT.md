# Finite-State Machine Contract

**Status:** IMPLEMENTED  
**Release target:** v0.1.0-beta4

## Behavioral contract

- A machine must register exactly one state with `initial=True` before
  `FSM.start()`. Zero or multiple initial states are invalid definitions.
- Guards receive the machine's mutable context dictionary. Event payloads remain
  on `Event.payload` and are not silently merged into context.
- When several transitions match the same event, higher numeric priority is
  evaluated first and the first enabled guard wins.
- A transition into `HistoryState` records the source state. A later transition
  out of that history state restores the recorded source, or the configured
  default when no snapshot exists.
- `FSM.start()` clears history from a previous run. A new transition into
  history after restart records a new snapshot normally.

These rules make malformed state graphs fail closed while keeping state
restoration deterministic.

## Practitioner evidence

A long-lived [Stack Overflow discussion of UML shallow
history](https://stackoverflow.com/questions/14681430/uml-state-machine-shallowhistory)
describes history as restoring the most recently active state and recommends a
default transition for the never-visited case. A separate [XState issue about
persisted history restoration](https://github.com/statelyai/xstate/issues/5178)
shows that losing or reconstructing the recorded history identity can send a
machine back to its initial state unexpectedly. Gludd therefore pins history
recording, default fallback, and restart clearing independently.

## Zero-downtime deployment and rollback

This correction changes test fixtures and the documented in-memory contract; it
does not alter schemas, serialized state, network protocols, or daemon startup.
Mixed beta4 processes continue to use process-local FSM instances. Rollback is a
source-only revert with no data migration or service drain.
