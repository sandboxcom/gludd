# Pause Recursion-Guard Continuity

## Purpose

An `AgentTask.depth` value is control state for the dispatcher nesting guard,
not optional display metadata. The existing hibernation snapshot and dispatcher
resume path preserve that value, but the public task and agent pause endpoints
did not call the entity snapshot helper, and their resume endpoints did not call
the rehydration helper. A client could therefore pause nested work through the
API and resume it without the state needed to enforce its prior depth.

The routes now use the existing controller boundary whenever both the dispatcher
and hibernation controller are wired. Pause captures the active task before the
durable pause record is written. Resume hydrates saved handles through the
canonical dispatcher path. Snapshots retain their original project identity as
well as depth, messages, parent, invoker, description, and prompt.

## Compatibility and semantic supersession

When either optional collaborator is absent, task and agent pause/resume retain
their prior no-snapshot behavior and response status remains explicit. Existing
response keys are unchanged; quiesce status/errors and rehydration count/errors
are additive observability fields. Existing pause records already default these
fields, so no persisted-state migration is needed.

Historical head `f78f2b298` bundled this recursion concern with README status
rewrites, daemon shutdown, capability/model construction, Ansible serialization,
and execution-engine sandbox changes. Those unrelated edits are not replayed:
their current implementations and tests are authoritative. This reconciliation
ports only the still-unreachable entity pause/resume continuity contract and
does not touch Make, configuration, enforcement plugins, or shared tooling.

## Practitioner evidence

The repository already records two relevant practitioner reports:

- [AutoGen discussion 2301](https://github.com/microsoft/autogen/discussions/2301)
  documents the difficulty of reconstructing messages and speaker transitions
  when resuming group-chat work. The durable lesson is that resume must restore
  complete control state rather than recreate a superficially similar task.
- [LangGraph issue 6792](https://github.com/langchain-ai/langgraph/issues/6792)
  reports resumed subgraph work rerunning because checkpoint scopes differed.
  It reinforces testing the public resume boundary and its identity/state
  propagation, rather than only testing a storage helper in isolation.

## Security, resources, and observability

Resetting depth would turn pause/resume into a recursion-limit bypass. The route
therefore delegates to the existing HMAC-protected, Pydantic-validated JSON
snapshot store; a missing, corrupt, or unauthentic handle is reported as a
degraded rehydration and is not dispatched. No pickle or model-provided code is
evaluated.

Work is bounded by the active tasks matching one agent or task and by the saved
handles already attached to its pause record. The change adds no network call,
subprocess, retry loop, worker, or background service. Existing asynchronous
snapshot I/O stays off the event loop. Responses expose quiesce status/errors
and rehydrated counts/errors without logging snapshot contents or prompts.

## Zero-downtime deployment and rollback

The change adds no schema, dependency, listener, or process-lifecycle change.
Old and new workers can coexist during a rolling deployment: valid legacy
records remain readable, while newly paused entities on updated workers carry
the continuity metadata. Operators should route a pause and its resume to a
worker sharing the configured durable stores, as before. Rollback is a normal
Git revert of the two focused source edits, regression, task evidence, and this
document; no data rewrite, destructive cleanup, or outage is required.

## Verification

The failing-first regression drove both public entity routes against the real
pause and hibernation controllers: both failed before production wiring, then
the final adjacent selection passed 55 tests with warnings as errors. The
regressions prove that depth `4` and original project identity survive the round
trip, a legacy snapshot safely falls back to the entity identifier, unknown
handle shapes do not reach the dispatcher, and failed capture is observable as
degraded rather than clean.

Branch-enabled coverage is 87% aggregate, 85% for `pause_controller.py`, and
89% for `pause.py`; both files exceed the 75% line-and-branch floor. Ruff,
strict mypy for the production files and typed regression, production
docstrings, locked Markdown, the 220-spec lint, task ledger, and task integrity
pass. Serialized collection counted 105,546 of 105,547 tests with one
intentional deselection and zero collection errors. The guarded local commit
remains a release requirement.
