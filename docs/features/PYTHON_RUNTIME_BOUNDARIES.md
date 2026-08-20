# Python Runtime Boundaries for v0.1.0-beta4

## Status and release decision

This is the release-blocking runtime contract for the `v0.1.0-beta4` tag
(`0.1.0-beta.4` in Python package metadata). Gludd core, Ansible controller
code, managed-host role code, and local-model inference are separate execution
planes. They may exchange versioned JSON data, Ansible facts, and artifacts;
they must not rely on one another's import paths or ambient Python installation.

Beta4 is not releasable merely because these environments happen to coexist in
a developer virtual environment. The built artifacts must prove the boundary
from a clean, offline-capable install.

## Existing seam and migration findings

The repository already contains useful parts of the target architecture:

- `general_ludd.ansible` owns runner, isolation, collection-path, timeout, and
  event handling in core.
- `general_ludd.agent.plugins.module_utils.gludd.GluddClient` is a standard
  library HTTP client suitable for the collection-to-daemon boundary.
- `general_ludd.agent.scrum_leader` composes roles with FQCNs, snapshots caller
  variables before delegation, and keeps decision logic in child roles.
- `galaxy.yml` already declares `general_ludd.agent` dependencies for the
  formal, language, E2E-generation, and OS-expert collections.

The beta4 scan also found release-path coupling that the gate must reject:

- collection plugins and role scripts import `general_ludd` modules directly;
- collection code modifies `sys.path` to find the source checkout;
- several roles invoke ambient `python3` or `/usr/bin/python3`;
- `agent` module utilities construct a core `ModelGateway` singleton inside the
  Ansible process, while other collections import that wrapper;
- some role composers use short role names even though the repository contains
  many collections and multiple collection search tiers.

Those patterns can pass in the monorepo while failing in a Galaxy tarball, an
Execution Environment (EE), a frozen executable, or a remote module payload.

## Four-plane boundary

```text
Gludd core Python
  | versioned job envelope + event stream
  v
Ansible controller EE Python ---- HTTP/JSON ----> Gludd model gateway
  | action/filter/lookup plugins                    | one loaded model
  | AnsiballZ module payload                        | bounded queue/cache
  v                                                 v
Managed-host Python / role venv                  local inference process
```

### 1. Gludd core Python

The `gludd` entry point owns API, scheduling, authorization, job persistence,
policy, event ingestion, and artifact promotion. Its lock contains only core
runtime dependencies. It may launch Ansible Runner as a bounded child and read
Runner's JSON events, but production collection execution must not use
`CoreAnsibleRunner` to import collection Python into the Gludd process.

Core must not add `collections/` or a checkout `src/` directory to `sys.path`.
No collection artifact may depend on the editable Gludd checkout being present.

### 2. Ansible controller Python

The controller runs from one immutable, digest-addressed EE built with the
mature `ansible-builder` mechanism. Its definition owns:

- an exactly resolved `ansible-core` and `ansible-runner` pair;
- signed or checksummed collection tarballs from the beta4 build;
- controller-side Python requirements discovered from each collection's
  `meta/execution-environment.yml`;
- controller system packages discovered from collection bindep files; and
- a collection lock whose dependency graph is resolved before image creation.

The controller image is not the Gludd core image. The Runner invocation names
the EE by digest, uses a project-namespaced private-data directory, has a finite
timeout, and mounts only declared read-only inputs plus its job artifact output.

### 3. Managed-host Python

Modules execute under the target host's explicitly selected
`ansible_python_interpreter`. Interpreter discovery is not a dependency
installer. A role that needs non-stdlib Python must either use an existing
Ansible module or provision a content-addressed role environment such as
`/opt/gludd/role-envs/<lock-sha>/bin/python` in a prior play.

The environment is built beside the active one, verified, and then selected in
inventory for a new play. Roles never invoke bare `python`, `python3`, or
`/usr/bin/python3`, and they never change `sys.path`. Pure code shared by remote
modules belongs in a public collection `plugins/module_utils` package and uses
the supported `ansible_collections.<namespace>.<collection>` import form.

Third-party Python packages are not assumed to be bundled into an AnsiballZ
payload. A missing target dependency fails early with the package name,
interpreter path, expected lock digest, and remediation; it does not fall back
to the Gludd or system environment.

### 4. Local-model Python

Model backends, weights, tokenizers, and GPU libraries stay in the existing
local-inference extra or its dedicated service artifact. Collection code calls
the authenticated Gludd model endpoint through one shared, stdlib-only client.
It must not import `ModelGateway`, provider registries, LangChain, llama-cpp,
Torch, or embedding implementations.

One namespaced model service owns each loaded model, KV cache, response cache,
and concurrency budget. Role calls carry a stable task kind, capability hints,
maximum input/output tokens, deadline, idempotency key, schema version, and
deterministic E2E settings. This avoids loading a model per Ansible fork,
maximizes cache reuse, and gives the core router enough information to choose
the smallest capable model without duplicating prompt logic in roles.

## Dependency and artifact ownership

| Plane | Dependency source | Release artifact | Forbidden dependency |
| --- | --- | --- | --- |
| Core | core lock generated from base project requirements | wheel and platform executable | collection source and domain/model extras |
| Controller | EE lock plus collection metadata | digest-pinned EE image or offline image archive | editable Gludd checkout and user Galaxy path |
| Collection | `galaxy.yml`, `meta/runtime.yml`, and `meta/execution-environment.yml` | one versioned Galaxy tarball per collection | undeclared cross-collection import |
| Managed host | content-addressed role requirements | lock/hash manifest and optional wheelhouse | ambient system `pip` or controller packages |
| Model | local-inference lock and model hash inventory | service artifact plus model manifest | Ansible forks loading model weights |

The beta4 release bundle contains the core distributions, all referenced
collection tarballs, the EE digest and offline acquisition metadata, Python and
collection lock manifests, SBOMs, checksums, signatures where configured, model
hash inventory, and smoke-test results. The release verifier installs from
these artifacts, not from the source tree or a user cache.

Collection Python requirements describe controller dependencies only. Managed
host requirements are separate role data and must not be placed in an EE file
as if that made them available remotely.

## DRY collection and role composition

The collection graph is a directed acyclic graph:

```text
general_ludd.runtime (stdlib contracts, module_utils, action plugins)
  -> general_ludd.agent (daemon/model/task clients)
     -> formal, language, os_expert, e2e_test_gen
  -> independent domain collections

orchestrator role
  -> explicit FQCN child role
     -> FQCN module/action plugin
        -> shared module_utils or daemon API
```

`general_ludd.runtime` is the single home for stable wire schemas, result
helpers, bounded HTTP transport, retry classification, and redaction. Domain
algorithms stay in their owning collection. Core business logic stays in core
and is exposed through an authenticated API; it is never copied into
`module_utils` merely to make an import work.

Every cross-collection edge is declared in `galaxy.yml` with a beta4-compatible
range and locked to an exact artifact for the build. Every role call uses an
FQCN, for example `general_ludd.agent.sprint_plan`. Dynamic composition uses
`ansible.builtin.include_role` with `public: false`, argument-spec validation,
explicit `tasks_from`, and a caller-prefixed snapshot of values. Static
`meta/main.yml` role dependencies are reserved for unconditional prerequisites;
orchestration remains visible in task files. Cycles and undeclared edges are
gate failures.

Role outputs are namespaced dictionaries or versioned JSON artifacts. Child
roles do not publish generic facts into shared scope, and a composer does not
read a child's private variables. This prevents variable precedence from
becoming an implicit call interface.

## Beta4 implementation slice

The following is the minimum code-gated slice before beta4 can be promoted:

1. Split dependency resolution into core, controller EE, managed-role, and
   local-inference manifests; build every manifest from the same release SHA.
2. Make the digest-pinned EE the production default and fail closed when it is
   absent. Keep in-process execution only for explicitly marked core tests.
3. Replace release-path `general_ludd` imports and `sys.path` mutation in
   collections with collection `module_utils`, FQCN calls, or the shared daemon
   API. Reject new occurrences mechanically.
4. Replace ambient interpreter invocations with an inventory-selected EE or
   role interpreter. Test explicit and implicit localhost plus a clean remote
   target.
5. Route every collection model task through the shared HTTP contract and prove
   that parallel Ansible forks reuse one model service rather than loading
   weights themselves.
6. Build all collection tarballs, the EE, core packages, hashes, SBOMs, and
   offline metadata; smoke-install and run them without checkout imports.
7. Gate sandbox, local-model, collection-composition, rollback, coverage, and
   release-completeness tests before creating or moving the beta4 tag.

Follow-up work may consolidate additional domain utilities, optimize EE layers,
or split large collections further. It may not defer any release-path import,
interpreter, artifact, authentication, or rollback boundary above.

## Compatibility, ZDD, rollback, resources, and tests

| Concern | Beta4 acceptance | Failure and rollback behavior |
| --- | --- | --- |
| Python | Core supports the project's declared Python range; EE and every collection publish their tested controller/target range | incompatible jobs are rejected before execution; prior core and EE digest remain selectable |
| Ansible | `meta/runtime.yml` pins the supported `ansible-core` floor; collection lock resolves every Galaxy edge | unresolved or prerelease dependency fails the build; no live artifact changes |
| Role calls | FQCN DAG, argument specs, private vars, namespaced outputs | validation fails before mutation; composer records the failing child |
| Model calls | authenticated schema, bounded tokens/deadline/retries, deterministic E2E profile | fail closed or use an explicitly declared offline fallback; never instantiate a hidden model |
| ZDD rollout | build and warm a new EE/model service beside the active digest, run sandbox canaries, atomically switch new jobs, drain old jobs | switch the job pointer back; retained jobs finish on their original digest |
| Managed venv | build beside active path, verify imports, activate only for a new play, retain previous lock | inventory points back to the previous interpreter; no in-place package downgrade |
| Resources | namespaced Runner/model processes, finite forks and queue, CPU/memory/disk budgets, visible heartbeat, deterministic cleanup | admission control rejects overload; timeout kills only the owned process tree and preserves artifacts |
| Security | read-only inputs, minimal mounts, PSK redaction, checksum/signature verification, no user collection fallback | any identity/hash/auth mismatch fails closed before role execution |
| Coverage | at least 85 percent aggregate and 75 percent for every touched Python file | release gate fails; tests are fixed only when their contract is wrong |

Required E2E coverage includes clean EE startup, collection installation and
FQCN resolution, cross-collection success and missing-dependency failure,
localhost and remote interpreter identity, absence of core imports in remote
payloads, sandbox mount/network/process limits, concurrent role isolation,
local-model health/chat/stream/schema/timeout/fallback, single model-load under
parallel forks, ZDD promotion with in-flight work, rollback to the prior digest,
offline artifact installation, and executable/wheel parity.

## Practitioner evidence and design rationale

- In an [Ansible Project thread from January 23,
  2018](https://groups.google.com/g/ansible-project/c/QFku2jXEGug), a user
  expected `ansible_python_interpreter` to make `pip` use the matching virtual
  environment but observed a system `pip`. The durable lesson is to identify
  the executable and dependency installation explicitly rather than infer one
  from the other.
- In an [Ansible Project answer from April 13,
  2021](https://groups.google.com/g/ansible-project/c/02j9r3hR6yk/m/MDq2ZTsiCAAJ),
  a maintainer states that the Python installing Ansible and the Python running
  modules are different runtimes even for localhost. That distinction is the
  basis of Gludd's controller/managed-host split.
- The still-open [Ansible documentation issue 676, opened October 18,
  2023](https://github.com/ansible/ansible-documentation/issues/676), records a
  collection author's surprise that a third-party Python dependency was not
  packed for the target. Gludd therefore treats controller and target
  requirements as different artifacts.
- [Ansible issue 73212, opened January 8,
  2021](https://github.com/ansible/ansible/issues/73212) documents parent-role
  values leaking across child calls and recursive templating when the same
  variable name is passed through. Gludd snapshots caller values under a
  private prefix and uses namespaced outputs.
- A [nested-role practitioner discussion from April 27,
  2022](https://groups.google.com/g/ansible-project/c/uACAyWBdvPM/m/3tX-7IX0IQAJ)
  recommends explicit import/include task flow over hidden role dependencies
  because execution, inheritance, and conditional behavior are visible. Gludd
  follows that pattern for composers.

The supported mechanisms reinforce these reports: Ansible's [interpreter
discovery documentation](https://docs.ansible.com/projects/ansible/latest/reference_appendices/interpreter_discovery.html)
defines target selection; [Ansible Builder collection
metadata](https://docs.ansible.com/projects/builder/en/latest/collection_metadata/)
defines controller Python and system dependency introspection; [module utility
guidance](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_module_utilities.html)
defines the remote shared-code mechanism; and the [collection Galaxy metadata
contract](https://docs.ansible.com/projects/ansible/latest/dev_guide/collections_galaxy_meta.html)
defines install-time collection edges. Beta4 uses these maintained mechanisms
instead of a custom package loader.

## Verification evidence required at promotion

The release record must attach the exact core and EE lock hashes, EE digest,
collection artifact hashes, model manifest hash, SBOM/checksum results, sandbox
and local-model E2E logs, collection-composition results, coverage report, and
rollback-canary result. Evidence is valid only for the release commit and the
artifacts it names; a passing source-tree test cannot certify a different
binary, image, collection tarball, or model file.
