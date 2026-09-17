# Universal Polymer Task Execution

## Problem and contract

S83.168 establishes a task-universal execution boundary. The executor owns
provider selection, scheduling, model invocation, and evidence accounting; a
domain adapter owns the meaning of a candidate and its acceptance criteria.
Self-improvement is therefore one possible consumer of the same runtime, not a
dependency of it. Neither the universal executor nor the polymer adapter imports
`self_improve`.

Polymer design is one constrained workload; Arduino firmware is a second,
independent workload implementing the same adapter contract. This is the
architectural proof that the executor is task-universal rather than a renamed
self-improvement loop. A successful polymer result requires
all of the following evidence:

- a strict, structured polymer candidate rather than model prose or a scaffold;
- an allow decision from the existing chemistry policy and safety classifier;
- validation evidence that supports execution at the requested scale;
- a complete and verifiable provenance chain for every claimed property; and
- a passing result from any explicitly required, allowlisted injected tool.

Missing or malformed evidence is a refusal, never a partial success. The model
gateway and tool runner are injected, so tests and deployments can bind local,
Azure, or chemistry-tool implementations without teaching the core about a
specific provider or SDK.

## Evidence-based provider routing

Each execution target declares capability, permitted data classifications,
estimated cost, health, accelerator identity, and non-empty evidence for those
claims. The executor intersects those declarations with hardware discovered and
approved by the existing accelerator planner. It then rejects targets that lack
the requested capability, cannot handle the request privacy class, are unhealthy,
exceed the remaining budget, or do not match discovered provider hardware.
Among the eligible targets it chooses the lowest estimated cost deterministically
and asks the existing scheduler to plan the work before calling the model.

This permits public work to use a healthy lower-cost Azure target while keeping
restricted work on a capable local target. Provider labels alone are never
sufficient routing evidence, and a disappearing or unhealthy accelerator cannot
be converted into success by model output.

## Mature chemistry tooling and practitioner evidence

[RDKit](https://www.rdkit.org/docs/RDKit_Book.html) is the mature open-source
choice for molecular parsing, substructure matching, descriptors, and related
chemical perception. Gludd does not recreate those algorithms. Instead, the
polymer adapter exposes a narrow injected tool seam so a deployment can run a
pinned RDKit-backed validator while keeping RDKit out of the provider-neutral
runtime and its base dependency set.

That boundary is intentionally stricter than "the parser accepted the string."
In the practitioner discussion
[MolFromSmiles gives different results in different versions #8329](https://github.com/rdkit/rdkit/discussions/8329),
users report version-dependent validity outcomes for the same input. The lesson
for this feature is to record the validator identity and version in provenance,
pin the deployment tool, and combine tool output with Gludd's policy, safety,
validation, and provenance gates. A bare parser result cannot authorize a design.

## Security, resources, and ZDD

The executor performs no dynamic imports, network access, file writes, or
background work. Request deadlines, model budgets, scheduler capacity, and the
allowlisted tool boundary remain externally enforceable resource controls.
Exceptions are reduced to their type at this boundary so provider or tool error
text cannot leak restricted prompts or credentials into task results.

The change is additive and requires no database, configuration, or wire-format
migration. Deploy the executor and adapter dark, run representative public and
restricted requests through injected non-mutating validators, then enable routing
per workload. Observe selected target, exclusion reasons, refusal reason,
estimated and actual cost, validation outcome, and provenance verification. A
rollback removes the new task registration or deploys the previous application;
there is no persistent state to reverse and in-flight work can drain on its
original worker.
