# Administrative Make Request Contract

## Scope

The administrative make endpoint is a typed boundary around the repository's
tracked MakeRunner. It accepts one target plus optional arguments, working
directory, timeout, environment additions, and streaming selection. It returns
the complete MakeResult envelope without interpreting command output.

## Request and compatibility invariants

- Required and optional fields are validated by a Pydantic request model before
  any process is constructed.
- Known fields use strict JSON types. Strings such as "false" or "30" are not
  coerced into booleans or integers, and malformed containers return HTTP 422.
- Unknown keys remain ignored for wire compatibility.
- A timeout of zero is distinct from an omitted timeout; the endpoint forwards
  every explicit integer unchanged to MakeRunner.
- Both established dependency seams remain supported: integrations may patch
  the command module before route registration or inject the router's public
  MakeRunner seam.
- Test-started patches are stopped after every example so one request cannot
  alter the next test's dependency graph.

## Practitioner evidence

Pydantic issue
[#4664](https://github.com/pydantic/pydantic/issues/4664) tracks the multi-year
design work required to make strict validation consistent across supported
types. Pydantic's documented rationale notes that default validation may coerce
common incorrect types, while strict validation rejects them. FastAPI discussion
[#5951](https://github.com/fastapi/fastapi/discussions/5951) records a
practitioner debugging a server error at an input boundary and the community
expectation that declared validation produces a 422 response. These reports
support an explicit strict model instead of indexing an untyped dictionary and
letting KeyError or downstream type failures escape as 500 responses.

## Zero-downtime behavior

The schema change is additive at route registration and does not restart or
mutate a running MakeRunner. During rollout, old clients using the documented
field types and extra keys continue unchanged; malformed clients fail before
process creation. Rollback restores the prior router without leaving background
work because this change does not alter MakeRunner process ownership,
namespacing, termination, or streaming lifecycle.

## Verification

Focused coverage exercises route registration, all result fields, both
dependency seams, missing and malformed inputs, every optional argument,
streaming callbacks, timeout edge values, and unknown-key compatibility. The
source must retain at least 75% per-file and the project aggregate remains
subject to the 85% release gate.
