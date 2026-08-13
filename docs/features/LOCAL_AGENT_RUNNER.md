# Isolated local agent runner

Status: implemented with fail-closed request validation and focused behavioral
coverage. Full repository gate evidence is tracked in `TASKS.md` as S83.15.

## Purpose

`general_ludd.execution.local_agent_runner` is the JSON process boundary for a
controller-side `ToolCallLoop`. Keeping controller imports behind this entrypoint
prevents an Ansible module payload from partially importing the controller
application and then losing its ability to serialize an Ansible result.

The runner accepts one JSON object on standard input and emits one JSON object on
standard output. It has no persistent state and performs no deployment mutation.

## Behavioral contract

1. A valid request contains `prompt`, `system_prompt`, `max_iterations`, and
   an optional `model_profile`.
2. `max_iterations` accepts an integer or an integer string in the inclusive
   range 1 through 100. Booleans, zero, negative values, non-integer JSON
   numbers, non-finite numbers, and larger values fail before model execution.
3. Malformed JSON, missing fields, and invalid field types produce a JSON result
   with `failed: true` and `changed: false`; they do not escape as parser or
   conversion exceptions.
4. Import and runtime failures preserve the same fail-closed result shape.
5. A successful run returns the answer plus stable `tool_calls`, `usage`, and
   `iterations` fields. The boundary reports `changed: false` because model
   inference alone does not mutate managed infrastructure.

## Resource and security boundaries

The 100-iteration ceiling bounds model calls, tool-loop work, latency, and cost
from an untrusted request. Rejecting Python booleans matters because `bool` is
an `int` subclass and would otherwise silently become one iteration. Rejecting
non-finite and fractional JSON numbers also prevents truncation or an uncaught
`OverflowError`.

The process result never includes the input prompt unless downstream model logic
explicitly returns it as the answer. Callers must apply their normal secret
redaction to operational error messages and must not pass credentials in
`prompt`, `system_prompt`, or `model_profile`.

## Zero-downtime deployment

The runner is stateless, so old and new workers can overlap during a rolling
deployment. The successful response schema is unchanged. The only compatibility
change is deliberate fail-closed rejection of iteration counts outside 1 through
100. Rollback requires only restoring the prior executable; there is no database
or on-disk migration.

A failed child request is isolated to that invocation. The parent service remains
available and can retry through its existing transport policy without restarting
other workers.

## Practitioner evidence

An Ansible user reported that an Ansible 2.19 module failed inside the AnsiballZ
bootstrap before emitting any JSON, leaving the controller with only
`Module result deserialization failed: No start of json char found`. The same
host worked under 2.18. That open practitioner report demonstrates why module
bootstrap/import failures need a narrow, observable JSON boundary rather than a
large mixed import graph:

- [ansible/ansible issue #86072](https://github.com/ansible/ansible/issues/86072)

## Verification

`tests/unit/test_local_agent_runner.py` executes the successful wiring, import
failure, runtime failure, valid request forwarding, malformed inputs, and every
iteration-boundary class. Focused coverage must remain at least 85% for the
runner, with no individual-file result below the repository's 75% floor.
