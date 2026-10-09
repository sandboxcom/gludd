# Native OpenAPI service registration

## Outcome

S45 replaces `auto_register_service` source generation and registry mutation
with one collection-native, read-only controller action. An operator admits an
exact local OpenAPI 3.1 document by SHA-256, names one `operationId`, pins one
HTTPS host, supplies bounded scalar parameters, and maps secret-bearing headers
to controller environment-variable names. The action validates the document,
request, and response before returning a bounded record list.

The implementation deletes the connector template, pricing sidecar writer,
shell `grep`, provider-source `blockinfile`, and ignored healthcheck failure.
It never claims that an unexecuted query is healthy.

## Why a native validator

The implementation uses the upstream libraries rather than writing an OpenAPI
parser or client generator:

- [`openapi-core==0.23.1`](https://pypi.org/project/openapi-core/0.23.1/)
  builds OpenAPI 3.1 request/response protocol validators from a local-only
  `SchemaPath`.
- [`jsonpointer==3.2.0`](https://pypi.org/project/jsonpointer/3.2.0/)
  resolves internal references and the configured response-record pointer.
- `safehttpx.AsyncSecureTransport`, already used by Gludd's bounded-fetch
  design, connects to the vetted IP while retaining the original TLS SNI and
  Host header. The collection wrapper rejects all non-public DNS answers and
  does not fall back to an ambient proxy.

Both Python dependencies are exact pins in the controller profile, controller
requirements, lock, runtime manifest, and dependency ownership configuration.
The infrastructure collection itself is now staged into the digest-addressed
execution environment.

## Practitioner and upstream evidence

The design accounts for long-lived reports instead of assuming happy-path
OpenAPI data:

- [openapi-core issue #154](https://github.com/python-openapi/openapi-core/issues/154)
  (2019) shows why validating an isolated schema is not equivalent to
  validating a concrete request/response operation. S45 supplies the native
  request and response protocols and lets `openapi-core` select the operation.
- [openapi-core issue #297](https://github.com/python-openapi/openapi-core/issues/297)
  (2021) and [issue #893](https://github.com/python-openapi/openapi-core/issues/893)
  (2024) document recurring ambiguity and failures around external/relative
  reference graphs. S45 permits internal JSON Pointers only and resolves every
  reference before validator construction, so validation cannot fetch another
  file or URL.
- [openapi-core discussion #768](https://github.com/python-openapi/openapi-core/discussions/768)
  (2024) and the related
  [Stack Overflow report](https://stackoverflow.com/questions/77402092/openapi-core-validate-request-really-poor-error-description)
  describe unwieldy validation errors. The Ansible boundary emits stable,
  bounded phase messages and never returns raw request headers, response bodies,
  filesystem paths, or validator exception graphs.
- A long-running practitioner thread on
  [runtime response validation](https://www.reddit.com/r/webdev/comments/1fxqkl6/how_do_you_enforce_that_your_api_actually_fulfills/)
  identifies drift between documentation and actual responses. S45 validates
  the live response against the exact digest-admitted contract before exposing
  records.
- Another practitioner discussion notes the
  [runtime cost of large schemas](https://www.reddit.com/r/PHP/comments/s5ko42/do_you_use_open_api_specs/).
  S45 caps the contract at 2 MiB, 50,000 nodes, depth 64, 512 operations, and
  uses validation-only canaries before live traffic.
- [GHSA-v6ph-xcq9-qxxj](https://github.com/advisories/ghsa-v6ph-xcq9-qxxj)
  documents SSRF through OpenAPI `$ref` dereferencing, while
  [Semantic Kernel issue #14312](https://github.com/microsoft/semantic-kernel/issues/14312)
  documents the DNS check/use gap. S45 rejects external references and pins the
  vetted DNS address into the HTTP transport.

## Trust and execution boundary

Admission happens in this order:

1. Resolve a non-symlink absolute root and one regular, single-link contract
   below it. Reject `..`, symlink components, devices, empty/oversized files,
   and identity changes during hashing.
2. Match the supplied lowercase SHA-256 before parsing.
3. Load bounded JSON/YAML; require OpenAPI 3.1 plus `info` and `paths`
   structure, reject every external `$ref`, resolve internal pointers, and
   construct a `SchemaPath` with no URL/file handlers. The runtime disables
   `openapi-core`'s second generic spec-loader pass because its default handler
   registry can initialize URL access; selected operation and response
   contracts are admitted explicitly before protocol validation.
4. Require one unique `operationId` whose method is GET or HEAD. Reject cookie
   parameters, duplicate or controlled header names, missing required
   parameters, complex/non-finite values, parameter payloads above the
   count/size ceilings, and operations without a bounded JSON response schema.
5. Require an explicit HTTPS base URL whose host exactly matches the explicit
   host allowlist. Wildcards, URL credentials, fragments, and query strings in
   the base are rejected.
6. Resolve named secret environment references. The action never accepts or
   returns a secret value as configuration or result data.
7. Validate the request with `openapi-core`. In validation-only or Ansible check
   mode, stop here without DNS or transport allocation.
8. Resolve DNS under a deadline, reject if any returned address is non-public,
   and connect to the selected IP through `AsyncSecureTransport`. Use one
   request, no redirects, no ambient proxy, and no persistent client.
9. Bound the response while streaming, validate status, content type, headers,
   and body against the selected OpenAPI response, then resolve the configured
   record pointer and enforce the record cap.

## Hard ceilings

| Resource | Ceiling |
|---|---:|
| Contract bytes | 2 MiB |
| Parsed nodes / nesting | 50,000 / 64 |
| Operations | 512 |
| Parameters / encoded parameter bytes | 32 / 4,096 |
| Secret headers / secret bytes each | 16 / 4,096 |
| Total request deadline | 30 seconds |
| Response bytes | 4 MiB |
| Returned records | 1,000 |
| Redirects | 0 |
| Network methods | GET, HEAD |

The public defaults are smaller: 15 seconds, 1 MiB, and 100 records. Callers
may lower these values but cannot raise the hard ceilings.

## ZDD delivery

1. Build the exact controller image and run every configured contract with
   `validate_only: true`. Record contract digest, operation identity, and image
   digest; do not resolve DNS.
2. Canary a small task cohort with live execution. Compare validated status and
   bounded record counts with the existing read path; never mirror secret
   headers or response bodies into logs.
3. Shift new tasks to the candidate image. Existing tasks finish on the old
   image within their 30-second maximum. There are no listeners, background
   workers, generated files, caches, database rows, or schemas to hand off.
4. Drain the old image after its bounded requests finish. Keep the prior image
   and contract digest addressable.
5. Roll back by routing tasks to the prior image. A failed digest, schema,
   request, DNS, transport, or response check is a closed canary and requires
   repair-forward before another shift; rollback has no source mutation to
   undo.

## Verification

`tests/unit/test_infrastructure_openapi_registration.py` covers a real
schema-valid query through an injected transport plus digest mismatch, path
escape, no-follow descriptor reads, symlink/hard-link input, external
references, unsafe methods and hosts, missing secrets, request/response
mismatch, redirects, DNS rebinding answers, redacted transport failures,
byte/record/parameter ceilings, action failure translation, remote-module
bypass, check mode, header-smuggling ambiguity, non-finite parameters, role
wiring, execution-environment ownership, and removal of every source-mutation
primitive. The dedicated branch-coverage selector enforces at least 85%
aggregate and 75% for every production file.
