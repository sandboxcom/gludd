# Collection and Role Interoperability for v0.1.0-beta4

## Release contract

Gludd `v0.1.0-beta4` treats its Ansible content as a versioned dependency
graph, not as a directory of files that happens to work from the source tree.
Every role and module call must resolve from the packaged collection root,
every cross-collection call must have a `galaxy.yml` dependency, and the
resulting graph must be acyclic before a candidate can be promoted.

The read-only verifier in `scripts/check_collection_interop.py` accepts all
three layouts used by the build and installer:

- a repository containing `collections/ansible_collections/`;
- an installation root containing `ansible_collections/`; or
- one extracted Galaxy artifact whose root contains `galaxy.yml`.

It parses production role tasks, handlers, collection playbooks, and Molecule
scenarios with the project YAML library. It does not use text matching to infer
Ansible structure and it does not alter the candidate or active tree.

## Mechanical checks

| Violation | Gate result |
| --- | --- |
| Literal `include_role` or `import_role` target is absent | `missing-role` |
| A Gludd module FQCN has no module, action plugin, or valid redirect | `missing-module` |
| A Gludd target collection is absent from the packaged catalog | `missing-collection` |
| A role name is short or computed with Jinja | `short-role-name` or `dynamic-role-name` |
| A call crosses collections without a Galaxy edge | `undeclared-dependency` |
| A declared Gludd dependency is absent from the bundle | `missing-dependency-collection` |
| Galaxy dependencies form a cycle | `dependency-cycle` |
| A task file cannot be parsed deterministically | `invalid-yaml` |

The diagnostics name the owning collection and task path so a failure can be
fixed without walking hundreds of roles manually. Results and graph edges are
sorted, which keeps CI evidence and model context stable between runs.

## Beta4 graph repairs

The first full-tree audit exposed four classes of source-tree-only behavior:

1. `general_ludd.agent.networking` called the dedicated networking collection
   while the networking role called an agent-owned Scapy module. Declaring both
   directions would create a Galaxy dependency cycle.
2. Security and operations roles called agent modules without declaring the
   agent collection.
3. Git-release roles called a `collect_repo_evidence` module that was never
   packaged.
4. Production orchestrators and Molecule scenarios used short role names; the
   Travel collection's root scenario referenced three roles that did not
   exist.

Beta4 resolves those defects with one owner for each behavior:

- `general_ludd.networking.gludd_scapy` is the canonical networking module.
  `general_ludd.agent.gludd_scapy` remains a compatibility name through
  `meta/runtime.yml` redirect metadata, and agent declares the networking edge.
- Security and operations declare their calls to `general_ludd.agent`.
- Git-release repository assessment is one private
  `general_ludd.git_release.collect_repo_evidence` role. It uses
  `ansible.builtin.uri` to call the authenticated core API, validates the JSON
  response, and only then publishes a namespaced fact. Three public roles reuse
  it instead of copying transport or importing Gludd core Python.
- Forensics and governance orchestrators, plus every covered Molecule scenario,
  use literal FQCNs. The Travel scenario calls its actual flight, hotel, and
  search modules instead of nonexistent wrapper roles.

The resulting direction is deterministic: domain collections call their own
content or declared services; compatibility redirects point to one canonical
implementation; and no hidden role lookup depends on the checkout's
`ansible.cfg`.

## Model-performance and DRY impact

A static, verified DAG is cheaper for a local model to use than runtime role
discovery. Gludd can provide the model with the bounded owning collection,
available role/module names, and declared downstream edges instead of the full
collection tree. Literal FQCNs eliminate ambiguous search-path candidates, and
one evidence role plus one Scapy implementation prevent duplicate task or
transport descriptions from consuming context.

The verifier itself is a reusable source of compact graph data: its report
contains sorted collection names, dependency edges, and only actionable
violations. A model never needs to infer whether a short name resolves or
invent a dependency not present in the release artifact.

## Zero-downtime promotion and rollback

Collection promotion is build-then-switch:

1. Build every collection beside the active installation under a release-SHA
   namespace.
2. Extract all candidates into one staging `ansible_collections` root.
3. Run the interoperability verifier and Ansible syntax/Molecule smoke checks
   against that root while active jobs continue on their original digest.
4. Switch only new jobs to the verified digest. Existing jobs drain on the
   prior digest.
5. If health or canary checks fail, point new jobs back to the retained prior
   digest; do not edit either installation in place.

The verifier is deliberately read-only. Its rollback test snapshots the active
tree, rejects an invalid candidate, and proves the active bytes are unchanged.
Missing targets, undeclared edges, cycles, invalid YAML, or malformed API
responses all fail before publication of new role facts or release pointers.

## Practitioner evidence

These rules address long-lived failure modes reported by Ansible users:

- [Ansible issue 73212](https://github.com/ansible/ansible/issues/73212), open
  since January 8, 2021, records parent/child role variable recursion and
  leakage. Gludd uses caller-prefixed inputs, `public: false`, and namespaced
  output facts at composition boundaries.
- In an [Ansible Project nested-role discussion from April 27,
  2022](https://groups.google.com/g/ansible-project/c/uACAyWBdvPM/m/3tX-7IX0IQAJ),
  practitioners recommend explicit import/include flow because hidden role
  dependencies make execution and conditional behavior difficult to reason
  about. Gludd therefore keeps orchestration visible in task files and verifies
  each literal target.
- [Ansible documentation issue 676](https://github.com/ansible/ansible-documentation/issues/676),
  open since October 18, 2023, documents the recurring assumption that a
  collection's Python dependency will automatically exist on the managed
  target. Gludd keeps role/module resolution in Galaxy metadata and crosses
  into core through a versioned HTTP contract rather than an ambient import.

The implementation follows maintained Ansible mechanisms: [Galaxy collection
metadata](https://docs.ansible.com/projects/ansible/latest/dev_guide/collections_galaxy_meta.html)
for dependency edges, [role reuse
semantics](https://docs.ansible.com/projects/ansible/latest/playbook_guide/playbooks_reuse_roles.html)
for explicit composition, and [runtime metadata
redirects](https://docs.ansible.com/projects/ansible/latest/dev_guide/collections_galaxy_meta.html#meta-runtime-yml)
for the compatibility alias.

## Focused verification

The beta4 change is pinned by unit and structural integration tests covering a
valid packaged graph, missing roles and modules, undeclared calls, missing
dependency artifacts, redirect targets, cycles, dynamic and short names,
repository-wide acceptance, and ZDD rollback. The existing Scapy behavior suite
loads the canonical networking module, while the Molecule inventory test keeps
agent-only coverage accounting honest after the move.

Promotion evidence must additionally include the full collection build,
Ansible syntax and Molecule gates, aggregate coverage of at least 85 percent,
at least 75 percent for every touched Python file, and the release artifact
checks described in the beta4 runbook.
