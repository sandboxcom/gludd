# Universal Task Runtime

## Contract

`UniversalTaskRuntime` is the capability-dispatch layer above
`UniversalTaskExecutor`. A caller submits one `UniversalTaskRequest`; it does not
select or import a chemistry, embedded, or self-improvement implementation. The
runtime resolves the request's exact capability from an immutable adapter
registry and refuses an unregistered capability. Duplicate or structurally
invalid adapters are rejected at startup.

After dispatch, the existing executor owns one provider-neutral path:

1. snapshot eligible model targets and approved accelerator evidence;
2. select a target from capability, privacy, health, and cost facts;
3. admit the task through the shared scheduler;
4. call the injected model gateway by exact profile identity; and
5. let the registered domain adapter parse and independently assess the result.

Self-improvement can implement the same adapter protocol. It receives no
privileged routing path from this runtime.

Dispatch is exact rather than hierarchical or best-effort. A polymer or Arduino
request presented to a registry or target set that supports only
`self_improve.proposal` is refused with explicit adapter or target evidence; it
is never rewritten into a self-improvement task.

## Event-loop routing boundary

The durable event loop consumes the same universal intent without owning domain
implementations. `general_ludd.event_loop.task_routing` contains the stable
work-type taxonomy, prompt resolution, playbook selection, scheduler work-item
mapping, and cost estimate. `event_loop.loop` composes those policies and keeps
compatibility exports for existing callers; it does not acquire a special
self-improvement routing branch.

This boundary also limits release risk. Routing policy can be tested at 97%
branch-aware coverage without importing or measuring the 5,000-line lifecycle
orchestrator, while the loop's existing compatibility tests prove that old
imports and patch points retain their behavior. Adding a chemistry, firmware,
or future domain adapter therefore does not require expanding the event-loop
module or changing a self-improvement package.

## Provider and profile-origin evidence

The invocation provider and the source of a model profile are different facts.
An admitted FreeLLMAPI record, for example, becomes a native Gludd profile for
its actual provider; FreeLLMAPI is discovery provenance, not a magic inference
backend. Every execution target requires a content-addressed
`ModelProfileOrigin` receipt. The receipt binds discovery evidence to the exact
route properties that were admitted:

| Field | Meaning |
| --- | --- |
| `source` | Profile discovery or configuration source |
| `protocol` | Versioned admission/binding contract |
| `evidence_sha256` | Digest of the exact admitted source evidence |
| `profile_id` | Exact gateway profile identity |
| `provider` | Native invocation provider, never a catalog alias |
| `accelerator_sku` | Approved accelerator identity |
| `capabilities` | Exact admitted capability set |
| `allowed_data_classifications` | Exact privacy scope |
| `offline` | Whether the route is locally isolated |
| `model_runner_id` | Attested model runner identity |
| `receipt_sha256` | Digest of all preceding canonical fields |

The selected origin is retained in `RouteDecision` and each target evaluation.
A source label without a lowercase SHA-256 evidence binding is invalid, as is a
receipt whose digest does not match its canonical fields. Before routing, the
executor asks an injected verifier to match the complete receipt against a
pinned admission set. A missing verifier, verifier failure, non-boolean verdict,
or unpinned receipt fails closed. The router revalidates both the receipt digest
and its target binding on every snapshot, so post-construction or deserialization
mutation cannot bypass admission. Replaying a valid FreeLLMAPI-origin receipt on
a local or Azure profile—or changing its accelerator, capabilities, privacy,
offline status, or runner—therefore cannot cross a capability boundary. The
router also refuses duplicate profile identities before selection, preventing an
approved route from being swapped for a later target with different provider or
capability metadata. It still independently requires the target's native
provider to match approved accelerator inventory, so discovery provenance cannot
create hardware or health evidence.

The acceptance matrix exercises all three supported route shapes through the
same gateway and scheduler contracts:

| Route shape | Invocation provider | Profile origin |
| --- | --- | --- |
| Local | `local` | operator-configured/native |
| Azure | `azure` | operator-configured/native |
| Catalog-admitted | native provider such as `groq` | `freellmapi` |

Provider-specific allowlists do not belong in domain adapters. The Arduino
adapter now enforces the actual privacy property: restricted work must remain on
an offline target. Public work may use any provider that already passed the
universal capability, privacy, health, cost, and accelerator gates.

## Cross-domain proof without invented answers

`tests/unit/test_universal_firmware_adapter.py` registers the real
`PolymerDesignAdapter` and `ArduinoFirmwareAdapter` in one runtime. Both tasks
select the same digest-bound FreeLLMAPI-origin profile through one executor. The
test gateway supplies fixed candidate fixtures; the runtime never writes a
polymer formula or firmware source itself.

Success still belongs to each domain's evidence gates. Polymer output must pass
schema, chemistry safety, validation, and provenance checks. Arduino output must
pass schema and source checks plus the injected compile, static-analysis, and
simulation pipeline. Model prose, a route decision, or plausible source text is
not completion evidence.

## Practitioner reports and design consequences

Long-lived provider aggregation issues show why catalog discovery must remain
separate from runtime readiness:

- In [FreeLLMAPI issue 880](https://github.com/tashfeenahmed/freellmapi/issues/880),
  an operator received HTTP 200 and a large `/v1/models` response, yet the client
  classified every model as unusable. Gludd therefore retains the admission
  protocol and evidence digest while requiring independent capability and health
  evidence for routing.
- In [FreeLLMAPI issue 1218](https://github.com/tashfeenahmed/freellmapi/issues/1218),
  a traced production session reported stale catalog entries and provider error
  classification consuming most of a failover budget. Gludd does not convert a
  catalog entry into a domain answer or bypass normal budget, health, and
  accelerator gates.

These reports also rule out a special FreeLLMAPI branch in chemistry or firmware.
Provider behavior remains behind the existing model gateway, while the task
runtime carries only stable profile identity and provenance.

## ZDD rollout and rollback

Origin receipts are in-memory configuration and require no persistent data
migration, but they are mandatory at the execution boundary. Before shifting
traffic, generate and pin receipts for every configured target, deploy the new
runtime dark, and replay representative local, Azure, and catalog-origin tasks.
Register adapters alongside existing direct executor calls, then shift new
requests by capability while in-flight work drains. Rollback shifts new traffic
to the previous application; the unused receipt configuration can remain in
place. No model candidate or domain result is synthesized or persisted by the
runtime itself.
