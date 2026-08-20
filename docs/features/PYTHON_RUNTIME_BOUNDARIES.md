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

The shipped checker, repaired dependency graph, packaging layouts, rollback
procedure, and focused evidence are documented in
[Collection and Role Interoperability](COLLECTION_ROLE_INTEROPERABILITY.md).

## Remaining non-agent collection migration map

### Audited inventory

The beta4 inventory is based on repository-wide searches of `collections/` for
core imports, `sys.path` mutation, and ambient interpreter fallbacks. The
`general_ludd.agent` collection is tracked by its own migration; this inventory
covers every remaining collection finding.

- Nineteen runtime files import `general_ludd` directly. They are
  `chat/plugins/modules/chat_export.py`,
  `azure/plugins/module_utils/azure.py`,
  `chemistry/plugins/module_utils/chemistry_dispatch.py`,
  `operations/roles/log_analyzer/files/analyze_logs.py`,
  `infrastructure/roles/auto_register_service/templates/connector.py.j2`, the
  two language module utilities, the nine language role scripts, and the three
  E2E-test-generation task/script files. Seven language test files repeat the
  core import, while `business/roles/entity_research/README.md` contains one
  documentation-only example.
- Thirty-three runtime files mutate `sys.path`: five radio role scripts, three
  binary-RE role scripts, the language module utility plus nine language role
  scripts, five forensics task files, eight physics task files, and two
  E2E-test-generation scripts. Twenty-one additional test files do so across
  radio, binary-RE, language, governance, E2E-test-generation, and OS-expert.
- The three audited ambient-interpreter patterns account for 82 runtime sites:
  13 bare `executable: python3` sites in forensics and physics; 35
  `default('python3')` sites in chemistry, language, and governance; and 34
  `default('/usr/bin/env python3')` sites in web, binary-RE, and XML.
- No collection currently declares `requires_ansible`, references
  `meta/execution-environment.yml`, or exposes an EE Python requirements file.
  Collection-to-collection edges in `galaxy.yml` therefore cover installation
  order but not the controller or target Python contract.

Counts are file counts, not import-line counts, except for the explicitly
identified 82 interpreter sites. A file with several imports or path insertions
is counted once. Generated strings inside tests are test findings, not runtime
files.

### Per-collection disposition

| Collection | Current coupling | Required landing point |
| --- | --- | --- |
| Chat | `chat_export` imports the core session exporter inside a transported module | Make export a real chat collection module backed by chat `module_utils`; keep format conversion and idempotence in that collection |
| Azure | collection `module_utils` only re-export `general_ludd.azure.core` | Move the authoritative Azure functions into Azure `module_utils`; core callers submit an Azure FQCN job instead of importing the collection |
| Chemistry | dynamic import and repository-path fallback load core chemistry; five roles run the bridge as a command | Create one FQCN chemistry module with action choices and collection-local `module_utils`; remove `importlib`, `GLUDD_REPO_ROOT`, and command dispatch |
| Language | two utilities, nine role scripts, and seven tests depend on core language packages | Put pure language data and algorithms in language `module_utils`, make role entry points modules, make cross-source scans controller action plugins, and call the model daemon through the shared client |
| Operations | a role script imports the core log analyzer | Make log analysis a module backed by operations `module_utils`; if the input is controller-local, pair it with an action plugin instead of copying controller paths to a target |
| Infrastructure | a generated connector imports an internal core helper | Publish the minimal record schema/helper as a versioned connector SDK; generated projects declare that SDK and its compatible core ABI explicitly |
| E2E test generation | role scripts add checkout `src` and import core analyzers/generators | Make code-path analysis and scenario generation controller action plugins with collection `plugin_utils`; declare tree-sitter and other controller dependencies in EE metadata |
| Radio and binary-RE | role scripts add collection directories to `sys.path` before importing helpers | Convert each script mode to a collection module and import its owning `module_utils` by collection path; consolidate repeated modes behind a typed argument spec |
| Forensics and physics | YAML embeds Python, edits `sys.path`, and runs ambient Python; coordinators use short role names | Replace each inline program with an FQCN module, import local `module_utils`, and call child roles by FQCN with private variables |
| Web, governance, and XML | roles retain ambient interpreter fallbacks | Prefer FQCN modules; any unavoidable external program receives a required, verified role-venv executable with no system default |
| Business | README demonstrates a core package import | Show the public FQCN module or collection utility contract so copied examples work from the Galaxy artifact |

The connector SDK is the sole intentional Python ABI shared with generated
applications. It contains only stable protocols, schemas, normalization, and
version negotiation; it does not import the Gludd daemon or an Ansible package.
All other cross-plane reuse occurs over the job or daemon wire contract.

### Supported Ansible mechanism by execution site

1. Code that must inspect the controller checkout, such as E2E source analysis,
   is an action plugin. Its Python dependencies belong in the EE, and its file
   access is constrained to Runner's declared project mount.
2. Code that must inspect or change a managed host is an Ansible module. Shared
   pure Python belongs in its collection's `plugins/module_utils` and is pulled
   into the module payload by Ansible's module assembler.
3. A role remains declarative composition. It validates arguments, invokes
   FQCN modules or roles, and publishes namespaced results; it does not embed a
   Python program in YAML or discover a checkout path.
4. Code that calls Gludd policy, model, persistence, or secret-bearing services
   uses the authenticated standard-library daemon client. It never imports the
   service implementation into the EE or managed host.
5. Test code installs the built collection under a temporary
   `ansible_collections/<namespace>/<collection>` root and the harness sets
   `ANSIBLE_COLLECTIONS_PATH` before Python starts. Tests import FQCNs without
   changing `sys.path`, so a missing artifact or dependency fails honestly.

### Migration order and mechanical exit gates

The work lands in dependency order so no temporary duplicate implementation
becomes authoritative:

1. Add `meta/runtime.yml` and `meta/execution-environment.yml` to affected
   collections, build the combined EE dependency inventory, and pin the
   collection graph. This is metadata only and does not switch live jobs.
2. Convert self-contained path hacks first: radio, binary-RE, forensics,
   physics, web, governance, and XML. Build and execute their Galaxy tarballs
   against explicit localhost and remote interpreters.
3. Move each domain implementation exactly once for chat, Azure, chemistry,
   language, and operations. Preserve a compatibility adapter only at the old
   public API, and have that adapter cross the job boundary rather than import
   the new collection implementation.
4. Convert E2E-test-generation controller work to action plugins and add its EE
   Python requirements. Prove it never transfers the source checkout or parser
   environment to the managed host.
5. Publish and consume the connector SDK, then update the business example and
   all collection tests. Remove compatibility adapters after the beta4 support
   window, not during the ownership move.

The non-agent migration is complete only when a mechanical scan finds zero
runtime `general_ludd` imports, zero runtime `sys.path` mutation, and zero
ambient interpreter defaults in collections. The gate separately allows only
the documented connector-SDK import and test fixture strings that are
explicitly testing rejection. Each collection tarball must pass syntax,
argument-spec, module, role-composition, clean-install, and EE execution tests
without `src/`, the repository root, a user Galaxy directory, or the Gludd core
virtual environment on `sys.path`.

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

## Implemented beta4 foundation

The controller boundary is now represented by tracked, independently
verifiable artifacts under `config/ansible/`:

- `execution-environment.yml` is an Ansible Builder v3 definition whose base
  image is digest-pinned and whose Galaxy, Python, and bindep inputs are
  separate files;
- `runtime-lock.json` content-addresses all four EE inputs and records the
  one-at-a-time build budget, adjacent rollout, drain, and rollback contract;
- `managed-host-python.lock.json` rejects ambient interpreters and establishes
  the content-addressed `/opt/gludd/role-envs/<lock>/bin/python` interface; and
- `collection-python-boundary-inventory.json` records every remaining
  migration finding by exact path, line, rule, and offending-line SHA-256.

The core dependency set no longer contains `ansible-core` or
`ansible-runner`. They live in the `ansible-controller` optional extra and the
development test group; `ansible-builder` is development tooling. The
PyInstaller specification excludes Ansible, Ansible Runner, collection source,
and playbooks so the frozen core cannot silently become a second controller
runtime. A clean subprocess regression blocks both Ansible imports and proves
the core CLI remains importable.

`ProcessIsolationConfig` now rejects enabled isolation unless
`container_image` uses the full `registry/name@sha256:<64 lowercase hex>` form.
In-process execution is named `test_only_in_process` and cannot be combined
with enabled isolation. This prevents a missing, mutable, or truncated image
reference from degrading into an ambient controller run.

Collection model execution now has one stdlib-only HTTP implementation:
`GluddClient.call_model`. It requires an explicit PSK, attaches both supported
authentication headers, and sends all game generation to the shared daemon
model service. The collection no longer imports `ModelGateway`, constructs a
per-fork model singleton, or mutates `sys.path` for `game_build`.

The executable checks are:

```text
make validate-ansible-runtime-boundary
make build-ansible-execution-environment ANSIBLE_EE_VALIDATE_ONLY=1 ANSIBLE_EE_RUNTIME=podman ANSIBLE_EE_IMAGE=gludd-ansible-ee:0.1.0-beta.4 ANSIBLE_EE_CONTEXT=/tmp/gludd-ansible-ee-contract
make verify-ansible-execution-environment ANSIBLE_EE_VALIDATE_ONLY=1 ANSIBLE_EE_RUNTIME=podman ANSIBLE_EE_IMAGE=registry.example/gludd-ee:beta4@sha256:<64-hex>
make check-collection-python-boundary COLLECTION_PYTHON_BOUNDARY_ROOT=collections/ansible_collections COLLECTION_PYTHON_BOUNDARY_INVENTORY=config/ansible/collection-python-boundary-inventory.json COLLECTION_PYTHON_BOUNDARY_STRICT_ZERO=0
```

Baseline mode currently tracks 213 exact release-path findings and fails on a
new, changed, duplicated, or stale entry. Strict-zero mode uses the same
scanner with `COLLECTION_PYTHON_BOUNDARY_STRICT_ZERO=1`; it fails until every
tracked migration is removed, then becomes the permanent release gate without
changing code or replacing the ledger with a path allowlist.

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
- In a [custom-module support thread from March 24,
  2017](https://groups.google.com/g/ansible-project/c/vKgky33rdKw), an Ansible
  maintainer explains that remote modules cannot rely on Ansible code outside
  `module_utils` because that is the code Ansible bundles into the payload.
  This directly rules out treating checkout `sys.path` edits as packaging.
- A [shared-code thread from January 11-13,
  2023](https://groups.google.com/g/ansible-project/c/0dCmwgjXdKI) confirms that
  controller plugins use collection `module_utils`, while collection search
  helpers should resolve data files. That supports separate action-plugin and
  managed-module dispositions instead of one executable-script convention.

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

Ansible's [module program-flow
documentation](https://docs.ansible.com/projects/ansible/latest/dev_guide/developing_program_flow_modules.html)
also fixes the execution-site distinction: action plugins always run on the
controller, while the normal action plugin assembles and executes modules on a
managed host. The [collection structure
contract](https://docs.ansible.com/projects/ansible-core/devel/dev_guide/developing_collections_structure.html)
defines `meta/runtime.yml` and `requires_ansible`; both become required beta4
metadata rather than implicit compatibility assumptions.

## Verification evidence required at promotion

The release record must attach the exact core and EE lock hashes, EE digest,
collection artifact hashes, model manifest hash, SBOM/checksum results, sandbox
and local-model E2E logs, collection-composition results, coverage report, and
rollback-canary result. Evidence is valid only for the release commit and the
artifacts it names; a passing source-tree test cannot certify a different
binary, image, collection tarball, or model file.
