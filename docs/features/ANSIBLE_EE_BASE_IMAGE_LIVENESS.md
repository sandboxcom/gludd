# Ansible execution-environment base-image liveness

Gludd pins the CentOS Stream 9 execution-environment base by manifest-index
digest. A digest is reproducible, but it is not a promise that a public
registry will retain that manifest forever. The release pipeline therefore
treats immutability and availability as two separate properties.

## Enforced workflow

`make refresh-ansible-base-image
ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY=0` performs the only supported
refresh:

1. send a bounded `HEAD` request to the official Quay Registry v2 manifest
   endpoint for `quay.io/centos/centos:stream9`;
2. require a syntactically valid `Docker-Content-Digest` manifest-index
   identity;
3. preserve the execution-environment YAML byte-for-byte except for that
   identity;
4. replace the YAML and runtime lock through same-directory atomic files; and
5. recompute every deterministic runtime-lock input hash.

A real execution-environment build checks the exact pinned digest before it
builds collection artifacts. A missing manifest therefore fails early with
the precise refresh command instead of consuming most of the job and failing
inside the container build. Validate-only builds stay offline.

The hosted gate also checks the pinned manifest immediately after locked
dependency installation, before validation and the expensive downstream job
fan-out. A vanished base therefore rejects the candidate early instead of
leaving the unrelated test and artifact jobs running for an unreleasable input.
The check remains blocking and uses the existing bounded registry request.

As of 2026-10-12, the resolved multi-architecture index is
`sha256:33346f5fe280763eb78e85f968ad52bc2b2013369de85403f67e802db98ed243`.
The value is evidence, not a permanent constant; the resolver is the durable
part of the fix.

## Zero-downtime deployment

Refreshing the source pin does not replace a running execution environment.
Gludd builds and verifies the candidate beside the active image, switches only
new jobs after the full gate passes, drains in-flight work, and retains the
previous verified digest for rollback. A resolver or build failure leaves the
active runtime untouched.

## Practitioner findings

- [Quay discussion #1807](https://github.com/quay/quay/discussions/1807)
  documents resolving a tag's manifest digest without downloading the image.
  Gludd implements that registry operation directly and validates the response
  header instead of parsing human-oriented CLI output.
- [QIIME 2 forum report #27926](https://forum.qiime2.org/t/qiime2-installation-via-docker-container-through-singularity-not-working/27926)
  shows a long-lived real-world `manifest unknown` failure for a Quay-hosted
  image. This is why digest syntax alone is not accepted as availability
  evidence.
- [Quay's distribution garbage-collection roadmap](https://github.com/quay/quayctl/blob/master/vendor/github.com/docker/distribution/ROADMAP.md)
  explains how concurrent manifest and blob collection can leave an apparently
  valid manifest unservable. Gludd checks the exact digest at build time rather
  than assuming an earlier resolution remains live.

## Verification

`tests/unit/test_ansible_runtime_artifacts.py` covers registry request shape,
invalid or missing digest headers, exact-identity mismatch, atomic definition
and lock synchronization, early build failure, and Make target registration.
`tests/unit/test_beta4_python_runtime_boundary.py` pins the reviewed release
identity. The hosted execution-environment job supplies the final independent
pull-and-build proof.

## Builder-stage source dependencies — 2026-10-12

Exact hosted run `38266403588` for `2564d61449d2782583f311ecbde9198c3662298b`
reaches the refreshed manifest but fails while pip acquires the committed
SearXNG Git revision: the builder image has no Git executable. The repair
uses Ansible Builder's existing `append_base` hook to install `git-core`
before the Galaxy and builder stages inherit that base. Putting Git only
in the final image or assuming the CI host's Git is available would not
repair this isolated build.

The same log reports distro pip 22.3.1 as outdated. The base hook upgrades
to explicit pip 26.2.1, the current non-yanked release verified against
[PyPI metadata](https://pypi.org/project/pip/26.2.1/) on 2026-10-12;
its Python >=3.10 requirement includes the unchanged controller Python 3.11.
No update-notice suppression, floating installer pin, mutable image,
core dependency, or managed-host lock change is introduced. The existing
runtime-lock writer refreshes the definition hash; all other lock inputs
remain unchanged.

Two test-first checks reproduce the missing base-stage prerequisites.
The repaired 39-case artifact cohort and runtime-boundary validator pass
with warnings fatal. Hosted image assembly, warm verification, and
exact-candidate full coverage remain required before promotion; an input
contract test is not a successful image build.

[Ansible Builder's definition reference](https://docs.ansible.com/projects/builder/en/latest/definition/#additional-build-steps)
explicitly supports installing Git after the base stage for later dependency
installation. The long-lived
[Ansible forum report from January 2024](https://forum.ansible.com/t/ansible-builder-error-when-scm-git-sourced-content-is-in-requirements-yml/3387)
explains the same host-versus-image boundary and why later bindep installation
cannot repair an earlier Git consumer. This change builds candidates beside
the active digest and retains the existing warm, switch, drain, and rollback
policy; it does not replace a running controller.

## Isolated SearXNG source metadata — 2026-10-12

Hosted Build `38278381965` gets beyond the served base manifest and the Git
and pip prerequisites, then fails while obtaining the pinned SearXNG wheel
requirements. The metadata import runs `searx/__init__.py`, which loads
`searx.settings_loader` and imports `yaml`. Installing PyYAML in the builder
interpreter cannot satisfy an import in pip's separate PEP 517 environment.
This is a source-package build dependency, not a missing runtime requirement.

`make inspect-ansible-searxng-source
ANSIBLE_SEARXNG_SOURCE_INSPECT_VALIDATE_ONLY=0` reads nine fixed files at the
committed Git revision without writing files or installing packages. Each
request has a 10-second connect limit and a 20-second total limit; mode `1`
prints the offline plan. Inspection confirms that the metadata import chain
needs only PyYAML: the frozen `settings_defaults`, `unixthreadname`, and
`exceptions` imports use standard-library or local modules, and `sxng_locales`
contains generated data. Runtime `msgspec` is not a metadata prerequisite.

The controller definition uses pinned uv `0.12.19` through Ansible Builder's
existing `PIPCMD` setting. The
[Builder assembly script](https://github.com/ansible/ansible-builder/blob/3.1.1/src/ansible_builder/_target_scripts/assemble)
and its
[final installation script](https://github.com/ansible/ansible-builder/blob/3.1.1/src/ansible_builder/_target_scripts/install-from-bindep)
both honor this setting. uv's documented
[extra build dependencies](https://docs.astral.sh/uv/reference/settings/#pip_extra-build-dependencies)
add `pyyaml==6.0.2`, matching the frozen source, only to SearXNG's isolated
metadata and wheel environment.
The definition embeds that configuration, so the existing definition hash
accounts for the entire repair. The Git revision, committed requirements,
collection artifacts, controller Python 3.11, core profile, and managed-host
lock retain their identities.

The base and final stages select `/usr/bin/python3.11`. The builder selects
the temporary `/tmp/venv/bin/python` created by the original assembly script.
This preserves the existing temporary-install and final-install boundaries.
Build isolation and runtime dependency resolution remain enabled; the recipe
does not replace upstream packaging with a custom builder.

The long-lived October 2023
[Python packaging forum report](https://discuss.python.org/t/no-network-interrogate-whether-dependencies-are-satisfied/36507/9)
explains why already-installed packages cannot satisfy an isolated build's
undeclared dependencies. The October 2024
[SearXNG practitioner report #3896](https://github.com/searxng/searxng/issues/3896)
also shows metadata imports failing before normal requirements installation
and warns about unconstrained prerequisite upgrades. Gludd therefore records
the package-specific build prerequisite and keeps the runtime source frozen.

The regression cohort parses the embedded TOML and generates a real Ansible
Builder Dockerfile without starting containers or installing dependencies.
It checks the installer inheritance, temporary interpreter selection,
unchanged staged VCS requirement, and absence of isolation or dependency
bypasses. These are focused acceptance checks. An actual source metadata
replay, hosted image assembly, offline import verification, and the unchanged
full gate and 85% aggregate/75% per-file coverage floors remain required
before promotion. Candidate
builds still run beside the active digest, with warm, switch, drain, and
rollback behavior unchanged.

## Early build-input admission

### Native pinned initialization acceptance

The freezer fixture now uses all seven pinned first-party modules, including
the actual package initializer and settings loader. It no longer substitutes
an empty initializer. Invalid settings must fail before frozen metadata is
rewritten; adjacent unregistered fixtures cannot shadow the canonical owner.
Ambient local SearXNG configuration is isolated only in the non-serving fixture.
The production module-freezer invocation remains unchanged.

The complete upstream default configuration is stored as effective YAML using
installed [PyYAML safe loading and emission](https://pyyaml.org/wiki/PyYAMLDocumentation).
The typed-tree digest, all 246 engines, top-level keys, frozen revision and raw
source/serialized digests are checked. This removes credential-bearing comments
without omitting configuration or encoding values to evade inspection. Original
first-party AGPL notices and raw acquisition evidence remain retained.

The initial canonical scanner correctly refuses six raw-fixture findings,
including an upstream comment labelled as a working API key. That comment is
not admitted. After native parser serialization, only six public revision and
checksum findings remain. Existing native baseline maintenance adds exactly
those six; all 1,120 prior identities, detector thresholds and filter semantics
are preserved. They are explicitly reviewed as noncredentials, following
[detect-secrets baseline auditing](https://github.com/Yelp/detect-secrets#auditing-a-baseline).
No inline suppression or fixture exclusion is added, and normal admission
continues to scan through its read-only disposable-copy owner.

The [2025 packaging discussion](https://github.com/searxng/searxng/issues/4372)
shows why metadata freezing and installed-webapp initialization need separate
acceptance. It does not establish a defective pinned freezer. Real native
regressions expose missing defaults, bypassed initialization and adjacent-file
shadowing before repair. These focused proofs do not establish a CentOS EE,
the complete installed dependency graph, exact-SHA hosted success or deployment
readiness; the original full nine-import/offline image proof remains required.

The pinned-source inspection owner now reads eleven exact inputs, adding the
upstream Dockerfile and complete default settings to the existing nine source
files. Its plan-only path makes no network request; actual acquisition retains
the fixed revision, HTTPS-only transport, 10-second connection bound,
20-second per-input bound and first-failure refusal. Two failing-first plan
and complete-input cases precede four passing inspector cases. The native
S98.4 source-defaults observation acquires all eleven inputs at the frozen
revision. Inspection is diagnostic, not installed-image or deployment proof.

The existing `_pytest-runtime-compatibility-admission` owner now executes the
complete runtime-artifact, Python-boundary, and frozen-source inspection files
before hosted shard and image fan-out. The promoted
`ee-isolated-metadata-prerequisite` and `ee-builder-installer-boundary` families
bind the exact prerequisite assertion and real Builder generation case to this
owner. Four routing, roster, and executable-wiring checks fail first when that
early ownership is absent.

The locked CI install set already includes `ansible-controller` and its Builder
dependency. Admission reuses that interpreter and the existing pytest runner;
it does not allocate another environment, install a package, download source,
or build an image. Complete acceptance files run with warnings fatal and one
worker, within the existing 90-second runtime and 60-second quiet bound. All
21 phases and the 2850-second aggregate ceiling remain unchanged.

Actual warning-fatal acceptance passes all 23 promotion cases and all 345 cases
in the real early owner, taking 47.02 seconds (47.8 observed). The manifest
validates all 28 failure families against executable owners and authoritative
full-gate phases; the 215-target contract, strict typing, Ruff, Make lint, and
Markdown remain green.

This early control-plane check does not replace the hosted pull/build result,
the exact canonical full gate, or either coverage floor. A failed source-input
contract blocks fan-out and leaves every serving controller untouched, retaining
the same zero-downtime warm, switch, drain, and rollback policy.

## Single-owner Azure SDK pins — 2026-10-12

Build `38296150696` at `f38ce53d15a8e51514b0e517fbda22a965777f87`
passes both Python quality jobs and their 349-case admission cohorts, then
fails EE assembly on incompatible direct requirements: collection
`azure.azcollection==3.21.0` declares `azure-identity==1.25.2`, while the owned
controller and EE inputs intentionally require `1.26.0`. The collection's
[exact release requirements](https://raw.githubusercontent.com/ansible-collections/azure/v3.21.0/requirements.txt)
confirm that source. This is a separate dependency-ownership failure, not
another missing isolated SearXNG metadata import.

Ansible Builder 3.1.1 supplies the
[supported collection-only exclusion](https://docs.ansible.com/projects/builder/en/latest/definition/#dependencies):
`dependencies.exclude.python: [azure-identity]`. The explicit user requirement
remains `azure-identity==1.26.0`. Exclusion affects only the inherited top-level
declaration; the resolver still checks user requirements and all transitive
dependencies. Every other collection requirement, controller pin, VCS revision,
interpreter, base digest, uv prerequisite and isolation setting stays unchanged.
The runtime-boundary validator requires that exact single-name policy, and the
existing lock writer refreshes only the changed definition's content hash.

Offline regressions reuse Builder's actual `process_collection`, `process` and
`filter_requirements` functions, with the exact upstream requirement snapshot in
a disposable collection fixture. `packaging.Requirement` checks the resulting
owned pin and constraints. Real Dockerfile generation verifies the exclusion
file and introspection flags before assembly; no dependency install, source
download, image build, custom resolver or additional environment is involved.
Existing owned-version assertions remain unchanged. The promoted
`ee-owned-azure-sdk-pin-parity` family runs in the same complete early artifact
file, with warning-fatal one-worker execution and unchanged 90/60-second bounds.

The original exact runtime-boundary mapping retains every prior entry and adds
only this explicit exclusion. Three new artifact cases fail first while 47
cases pass; the early routing and roster checks separately fail twice while 22
cases pass. After repair all 136 related acceptance cases pass, including Azure's
unchanged owned-pin assertions. The 50-case artifact cohort measures 92.77%
combined source coverage (93.73% line, 90.18% branch), above unchanged 85/75
floors. The real complete early owner executes 358 cases in 29.42 seconds
(30.2 observed), preserving 21 phases, the 2850-second ceiling and mandatory
full-gate proof; the manifest now owns 29 failure families. The existing lock
writer changes only the definition hash to
`sha256:5d33d909ace591a961dba2a659639f0f0b9ed77975d41d8b7a30040306e35827`.

The September 2023
[EE content forum discussion](https://forum.ansible.com/t/community-execution-environment-image-content-and-location/262)
describes conflicts from combining collection-declared dependencies. The
November 2023–January 2024
[collection requirements debate](https://forum.ansible.com/t/vote-ended-on-2024-01-17-collection-inclusion-requirements-add-requirements-txt-and-bindep-txt/2611)
records fixed-pin conflicts and misleading requirement-file ownership. Builder
3.1's current documented exclusion facility supplies the narrow ownership rule.

This changes candidate build policy only. Serving controllers retain the active
digest; warm verification, switching new jobs, draining and rollback remain
unchanged. Focused merge/generation acceptance is not transitive resolution or a
successful image. Replacement exact-SHA assembly/import proof, canonical full
gate and 85/75 coverage remain mandatory before promotion.

## Complete Azure transitive requirements — 2026-10-12

Hosted Build `38307021746` reaches uv assembly after the direct identity
ownership repair, then rejects the remaining Linux dependency graph.
`azure-identity==1.26.0` requires `msal>=1.39.0`, while the collection's
`azure-cli-core==2.75.0` requires `msal==1.33.0b1` on Linux. Excluding the
collection's identity declaration cannot reconcile those transitive constraints.
The controller identity remains intentionally pinned to `1.26.0`.

The compatible release metadata is explicit:
[Azure Identity 1.26.0](https://pypi.org/pypi/azure-identity/1.26.0/json)
requires Azure Core `>=1.31.0` and MSAL `>=1.39.0`;
[Azure CLI Core 2.91.0](https://github.com/Azure/azure-cli/blob/azure-cli-2.91.0/src/azure-cli-core/setup.py)
requires Linux MSAL `1.39.0`, MSAL Extensions `1.3.1`, and Azure Core
`~=1.39.0`. CLI releases `2.88.0`, `2.89.1`, and `2.90.0` still select Linux
MSAL `1.36.0`. Upgrading only CLI Core would retain a second conflict with the
collection's `azure-core==1.38.0`. Both CLI Core `2.91.0` and
[Azure Core 1.39.0](https://pypi.org/project/azure-core/1.39.0/) support the
unchanged controller Python 3.11.

The long-lived
[Azure CLI issue #15212](https://github.com/Azure/azure-cli/issues/15212)
reports an earlier CLI/Identity MSAL mismatch, and
[Azure collection issue #1779](https://github.com/ansible-collections/azure/issues/1779)
records recurring conflicts when collection and CLI dependencies are combined.
These reports support validating the assembled graph, rather than treating
successful direct-pin ownership as transitive compatibility proof.

The EE now owns CLI Core `2.91.0` and Azure Core `1.39.0` explicitly alongside
the unchanged identity pin. Builder excludes exactly those three inherited
collection declarations, leaving every other requirement in the graph.
The existing artifact owner's `resolve-requirements` mode accepts an explicit
Builder-staged collection root and output path. Before invoking uv, it checks
all staged manifests, their required versions and recursive collection
dependencies through Ansible Galaxy's version comparator. A missing declared
requirements file or transitive collection fails closed.

The compiler consumes Builder's actual merged requirements plus the definition's
Core and Runner pins, with the same embedded SearXNG configuration. It targets
Python 3.11 and `x86_64-manylinux_2_28`; that conservative platform selection is
not proof of the pinned base image's runtime ABI. The existing observed-command
owner streams output, emits a 15-second heartbeat, and enforces the unchanged
60-second quiet and 180-second standard-operation bounds. Failed resolution
retains its observed evidence and leaves earlier output untouched; successful
output is published atomically. No additional project environment, resolver,
daemon or proof cache is introduced.

Real resolution also validates the tools already present before introspection
or compilation. The required uv version comes from the EE's own install
declaration (`0.12.19`); the required Builder version comes from the controller's
frozen `uv.lock` (`3.1.1`), rather than its broader project dependency range.
A five-second `uv --version` probe and installed distribution metadata must
match those exact identities. Unavailable metadata, a failed or malformed probe,
or a mismatched version rejects resolution without installing or updating a
tool and without publishing output. Matching identities are reported visibly.
Offline plan mode does not inspect collections, probe executables, or read
installed package metadata, and it creates no resolution output.

Metadata overlap is focused evidence only. The complete graph must include
every exact Builder-staged native, external, and transitive collection plus
the owned user requirements, Ansible Core and Runner. The existing Builder
introspection and uv `0.12.19` compiler supply that resolution; a fixture graph,
an offline plan, or a successful requirements merge does not replace an actual
complete solve. Preparing the frozen SearXNG source metadata may create a
temporary isolated build environment with its existing package-specific PyYAML
prerequisite. Runtime installation and build-isolation bypasses are unnecessary.

Resolution and candidate builds leave the active image untouched. Hosted image
assembly, offline imports, the exact-SHA canonical gate, both coverage floors,
and warm, switch, drain and rollback evidence remain required before promotion.

The public, offline-safe contract example is:

```sh
make resolve-ansible-runtime-requirements ANSIBLE_EE_COLLECTION_ROOT=/tmp/gludd-contract-ee-collections ANSIBLE_EE_RESOLUTION_OUTPUT=/tmp/gludd-contract-ee-resolution.txt ANSIBLE_EE_RESOLUTION_VALIDATE_ONLY=1
```

Mode `0` requires the complete exact staged tree and matching existing tools.
Mode `1` validates owned configuration and prints a plan without reading that
tree, probing tools, installing packages or publishing output. The Make wrapper
uses literal environment carriers, preserves quoted paths and existing output,
and rejects invalid modes or missing inputs before invoking the owner.

Actual failing-first evidence is 19 failures/51 passes for the complete-input
contract, then 11 failures/69 passes for exact tool identity. Four public Make
wrapper cases fail before wiring. After integration all 134 artifact, boundary,
source-inspection and promotion cases pass with warnings fatal; the 103-case
coverage replay measures 91.63% combined (93.35% line, 87.50% branch), above
unchanged 85/75 floors. The two additional early families bind Linux transitive
parity and complete-assembly contract acceptance, not a mocked solver success
claim. The 217-target contract and actual safe example pass. This evidence is
not a complete real collection solve or replacement image proof.

## Complete offline acceptance of the built image — 2026-10-12

Build success now requires the same complete OpenTofu/controller warm used by
digest-addressed verification. Both phases run the captured build ID, never a
mutable tag, with no network or pull, automatic container removal, and names
derived from the project root, PID and phase. Python comes from the validator's
`/usr/bin/python3.11` boundary; all nine existing imports remain mandatory.
Any build, identity or smoke failure rejects acceptance before later work.

The [locked Builder CLI][builder-iid-cli] forwards its existing extra-build
arguments through [shlex splitting][builder-iid-forwarding]. A unique private
temporary directory therefore captures the runtime's image ID without an
extra build or post-build tag lookup. Reads require one bounded, owned regular
single-link SHA-256 file, reject links and nonregular inputs, and close the raw
descriptor even if wrapper construction fails. The existing resource inventory
admits the context-managed owner; no image, environment or serving ref is removed.

The first-hand [2023 Docker operator report][iidfile-operator] explains why an
image-ID filename must not be substituted with stdout or a pipe. Gludd uses a
private real file rather than inferring identity from streamed build output.
This historical report supports the design, not a claim of a reproduced runtime
bug in today's toolchain.

Acceptance fails 22 of 30 cases first, then the descriptor-failure case fails
separately. All 134 related cases pass with warnings fatal, and all 115 coverage
cases pass at 92% combined coverage, above unchanged 85/75 floors. The original
successful-build fixture now produces the required image-ID file while retaining
every original assertion. Four routing/roster/wiring cases fail first, then all
47 promotion/plan cases pass with 46 families and unchanged phase budgets.
The public plan behaviors, strict typing, Ruff and docstrings pass. Actual
replacement hosted full warm, canonical full gate and exhaustive coverage remain
required; the earlier hosted partial-import smoke is not this proof.

[builder-iid-cli]: https://raw.githubusercontent.com/ansible/ansible-builder/3.1.1/src/ansible_builder/cli.py
[builder-iid-forwarding]: https://raw.githubusercontent.com/ansible/ansible-builder/3.1.1/src/ansible_builder/main.py
[iidfile-operator]: https://github.com/moby/moby/issues/46571

## Native EE initialization diagnostics — 2026-10-13

Build run `38379268292`, job `115241183631`, for `d6769ef` printed
`ANSIBLE_EE_IMPORT_OK` and exited zero while its installed-image Python phase
logged a missing limiter configuration, disabled favicon caching because stdlib
SQLite was 3.34.1, and SoundCloud/Wikidata initialization errors under
`--network=none`. These retained failures do not establish healthy initialization.

The repair keeps the CentOS Stream 9 base and installs checksum-pinned
CPython 3.11.17 from the immutable `python-build-standalone` build `20261001`
into `/opt/gludd/ansible-ee-python`. Native `sqlite3` must satisfy SQLite 3.35
or newer with serialized threading (`threadsafety == 3`). Distro Python remains
the image bootstrap; the public controller interpreter path changes from
`/usr/bin/python3.11` to `/opt/gludd/ansible-ee-python/bin/python3.11`.
Controller, dependency installation, version freezing, and hosted smoke use
that path. Consumers invoking the old controller path must adopt the new path;
the independent managed-host Python boundary remains inventory controlled.

Builder's generated base stage installs the isolated interpreter in
`prepend_base`, before `pip_install` and Ansible's package installation consume
`PYCMD`. Its maintained standalone runtime leaves the distro SQLite library
intact. Both architecture-specific archive names and SHA256 values are frozen
in the hashed EE definition; source metadata alone is not installed-image proof.

The non-serving child selects a packaged native settings override using
`use_default_settings.engines.keep_only: [wikipedia]`, with a valid sibling
`limiter.toml` inheriting every frozen default. Pinned Wikipedia has no `init`
or `setup` callback; its top-level update changes only a local language map.
Production retains its engine defaults. The complete ordered nine imports and
installed Git/frozen-version assertions remain required. A stdlib logging
handler collects all records at WARNING or higher while normal handlers retain
the diagnostic output, then refuses `IMPORT_OK` if any were collected.

Practitioner reports corroborate the failure modes. [SearXNG issue #3754]
(https://github.com/searxng/searxng/issues/3754), opened 2024-08-22, reports the
same missing limiter file after the official installation process. [Docker
issue #325](https://github.com/searxng/searxng-docker/issues/325), opened
2025-01-16, reports SoundCloud initialization errors despite ready WSGI workers
and a successful ping. Neither startup completion nor network ping is adequate
engine initialization evidence.

Artifact acceptance executes the pinned settings loader and limiter parser,
native logging records and stdlib SQLite checks, and Builder's real generation.
Root's initial 22 cases failed before implementation; three additional ordering
and public-path regressions failed before their repair. The integrated focused
cohort passes all 181 cases with warnings fatal. Native branch-aware coverage of
the existing artifact owner is 92%, above unchanged 85% aggregate/75% per-file
floors; this is not exhaustive whole-project coverage. The maintained
[standalone build's published release assets](https://github.com/astral-sh/python-build-standalone/releases/expanded_assets/20261001)
independently supply both frozen architecture checksums. Native baseline
maintenance adds twelve explicitly reviewed public checksum findings, retains
all 1,126 previous findings and scanner policy, and passes canonical admission.
These checks do not provide the rebuilt Linux image, canonical CPython 3.11 gate,
complete-project coverage, or exact-SHA hosted evidence. S98.4 remains open until those proofs
are collected for the immutable candidate. ZDD still requires warming the new
digest beside the active one, switching new jobs, draining old jobs and retaining
the prior digest for rollback.

## Preserve the pinned base's executable provider (2026-10-13)

Exact source `39b52845b46cc35e58babf4c1a3bcd8a701f1bea`, Build run
`38430314549`, job `115417409716`, fails in base step 4 before Python or
collection assembly. DNF reports that the pinned CentOS Stream 9 image already
contains `coreutils-single-8.32-43.el9`, which conflicts with the requested full
`coreutils` package. This is a package-selection failure, not disk pressure or
successful installed-image acceptance.

Keep the existing `coreutils-single` provider in the explicit bootstrap request.
Python 3.11, curl-minimal, tar, gzip, native cache cleanup, the pinned base and
both standalone-interpreter archives remain unchanged. Do not use allowerasing,
skip-broken or nobest to conceal or bypass package resolution. The canonical
runtime-lock producer updates only the EE definition's content hash.

The [2017 upstream maintainer explanation](https://bugzilla.redhat.com/show_bug.cgi?id=1446494)
states that the providers intentionally conflict because they supply the same
executables. The [2018 Fedora container user report](https://github.com/fedora-cloud/docker-brew-fedora/issues/58)
records the same conflict during Ansible's container integration tests. Retaining
the already installed provider is the narrow application of that evidence; those
reports do not prove our rebuilt image.

The existing regression executes Builder's real generation and now checks the
provider, installation order and absence of dependency-bypass flags, preserving
every installer/interpreter assertion. It fails first on the actual conflicting
request; all 196 related source tests then pass with warnings fatal on Python
3.14 and existing Python 3.11. The native watched 3.11 replay completes in 32.3
observed seconds with its original quiet/runtime bounds and owned cleanup.
Source acceptance does not replace a successful DNF transaction, the full
installed offline smoke, exact-SHA hosted proof or the canonical release gate.
Warm the replacement beside the active digest; switching, draining and rollback
remain pending until deployment acceptance. S98.4 stays unchecked.

## Preserve native controller console-script lookup (2026-10-13)

Exact source `4721aa273fae51bf5b8f7afe6814ef5c87e86209`, Build `38451761526`,
job `115490425617`, installs core 2.19.12 and runner 2.4.3 successfully, but native
`check_ansible` and `check_galaxy` then exit after their console scripts cannot be
found. Both execute bare Ansible commands; selecting an absolute `PYCMD` does not
put its scripts directory on `PATH`.

The EE definition prepends the existing isolated controller's `bin` directory
to the inherited base-stage `PATH` before native package installation. Galaxy,
builder and final inherit that base environment. Native check scripts remain
unchanged and enabled; no system interpreter, package pin, artifact hash, SQLite
requirement, SDK exclusion or installer boundary is weakened. The existing lock
producer updates only the definition input hash.

The real native-generation regression fails first on missing PATH wiring, then
checks ordering, both unchanged native helper commands and their retained Galaxy
and final-stage execution. Fresh child helper checks reproduce missing lookup
without the selected bin path and require success with it. These host-generator
checks are not a rebuilt Linux image or installed-image acceptance.

The [2021 upstream user report](https://github.com/ansible/ansible/issues/74900)
records successful package installation followed by command-not-found. The
[2023 Ansible Builder forum](https://forum.ansible.com/t/ansible-builder-and-galaxy-ng/1669)
shows using schema-3 additional build steps for image environment declarations;
its old Galaxy server workaround is not adopted. The
[Docker environment reference](https://docs.docker.com/reference/dockerfile/#env)
defines inherited stage environments. These were reviewed on 2026-10-13 and
support fixing the generated image definition instead of altering native checks.

Root-user pip warnings in the retained failed build remain separate unsuccessful
diagnostics; this PATH repair neither filters them nor claims their remediation.
The replacement digest must still build, warm offline with fatal diagnostics,
pass exact-SHA hosted/full-gate acceptance and receive deployment acceptance
before new jobs switch. Existing jobs retain the serving digest and drain only
after promotion; rollback retains the prior verified image. S98.4 stays open.

Combined source acceptance passes 248 cases on Python 3.11 and 3.14, including
actual native helper failure without the selected bin path and success with it.
The complete early owner passes 1211 main cases plus fresh help in 41.2 observed
seconds under unchanged 90/60 bounds. Strict typing/Ruff, native seven-input
runtime validation, Make parity/231 contracts and unchanged 256-resource
ownership pass. This is bounded source/native-generation acceptance, not rebuilt
image or item deployment proof.

## Native controller virtual environment (2026-10-13)

The retained Build `38451761526` also emits root-user pip warnings. PATH repairs
console-script lookup, but does not change pip's installation environment. The
definition now extracts the same checksum-pinned standalone runtime under
`/opt/gludd/ansible-ee-runtime` and uses its maintained `python3.11 -m venv` to
create `/opt/gludd/ansible-ee-python`. The public controller interpreter and PATH
remain unchanged. Executable prefix assertions require the real controller venv
and its private base runtime, alongside the original Python and SQLite checks.

[pip 26.2.1's native warning owner](https://raw.githubusercontent.com/pypa/pip/26.2.1/src/pip/_internal/utils/misc.py)
returns for an actual virtual environment before inspecting UID. This removes
the warning's cause; no root-warning opt-out, logging filter, upstream source
patch or installer replacement is used. Native Builder's ensurepip, Ansible
installation, Galaxy helpers, assembly and final-install scripts remain enabled.
All package/archive pins, SDK exclusions and isolated SearXNG metadata inputs
retain their identities. Both the base runtime and controller venv remain in the
candidate image.

Creating Builder's temporary venv from the controller venv uses the original base
Python, not the controller's installed packages. The existing explicit uv
`PIPCMD` therefore still loads from the controller while `UV_PYTHON` selects
`/tmp/venv/bin/python` during assembly. Controller system mode is now false,
consistent with its real environment. An explicit absolute interpreter can
bypass uv's system-discovery filter, so the old true value is not claimed to have
caused a resolver failure. [CPython venv](https://docs.python.org/3.11/library/venv.html),
[uv environment selection](https://docs.astral.sh/uv/pip/environments/).

The [2021 pip user report](https://github.com/pypa/pip/issues/10556) records recurring
root warnings; its suppression request is not adopted. The
[December 2025 Ansible Builder forum report](https://forum.ansible.com/t/ansible-builder-failing-on-new-host/44937)
shows successful installation, warnings and a subsequent mismatched-interpreter
failure. These are distinct conditions, not reasons to ignore native checks.

The new real-generation assertions fail before the definition changes. An
artifact-only host probe also verifies native venv/ensurepip, nested environment
selection and SQLite with automatic cleanup, but runs as UID 501 with Python
3.11.14 and uv 0.11.17. It is not root/Linux or pinned-image execution proof.
Direct upstream CLI generation retains three native handle warnings and is not
accepted merely because it exits zero; the reviewed exclusive generation worker
owns those handles. Required image build, warning-free full offline warm,
coverage, exact-SHA hosted/full gate and deployment acceptance remain pending.
The serving digest, new-job switch, in-flight drain and rollback remain unchanged;
S98.4 stays open.

## Vanished pinned manifest and bounded method fallback — 2026-10-14

Exact hosted run `38502390021` accepts the previous `33346f5f…` index in its
Python 3.11 gate at 02:42 UTC, but Quay returns 404 for the same immutable
reference in the Python 3.12 gate at 02:51 UTC. A native local probe reproduces
404 for both HEAD and GET. This is evidence that the old manifest is unavailable
at those checks, not proof of garbage collection, a transient outage or a
Python-version defect. The existing refresh target resolves and atomically
records the new supported index
`sha256:4fdfb367402535024cd7703a67e4549ca3b0a0ee05081e06d89e24dce3a3827a`;
an independent native exact-digest check succeeds. The release identity test
retains its exact assertion and adopts that independently resolved pin.

The registry checker also handles method-specific HEAD failures without changing
the requested reference. A HEAD 404 or 405 permits exactly one GET with the
same supported URL, Accept header and 30-second request bound. The GET must
return a valid digest header, at most 1 MiB of nonempty content, and bytes whose
SHA-256 equals that header. Pinned checks still require the original requested
digest. Other HTTP failures and failed GETs remain terminal; no retry loop,
mutable-tag substitution, cache, image download or automatic refresh is added.
The fallback prints its reference and HEAD status before the request.

This uses the standard-library client already owned by the checker and follows
the [OCI manifest retrieval and existence-check contract](https://github.com/opencontainers/distribution-spec/blob/main/spec.md#pulling-manifests).
A [January 2023 Docker user forum report](https://forums.docker.com/t/registry-responded-to-head-request-with-404-not-found/134177)
describes a HEAD 404 with a successful subsequent pull. It motivates testing
method-specific behavior, but is not evidence that Gludd's current 404 is that
same defect. Here the fallback correctly rejects the vanished old digest.

The 23 added controls first reproduce 13 failures with ten existing-path
controls passing. All 486 new and existing EE/release-input cases then pass on
Python 3.11 and 3.14 with fatal warnings. The unchanged runtime-artifact coverage
profile passes at 92.94% lines and 85.87% branches, with its two existing
exclusions and unchanged 85/75 floors (`.gate-logs/coverage-20261014T025853Z.json`).
Ruff, production/test strict typing, docstrings, all 258 resource owners and
task registration/integrity pass. Native refresh and availability are source-input evidence only:
the new image still needs independent build, full offline warm, deployment,
exact-successor CI and full gate proof. No release-ready task is checked off.

### Error-response ownership follow-up

Five additional controls retain real standard-library `HTTPError` objects and
their file-like bodies instead of allowing garbage collection to hide resource
lifetime. All five fail first: two show GET starting while the failed HEAD
response remains open, and three show terminal HEAD/GET errors retaining their
responses. The checker now explicitly closes failed HEAD responses before GET
and terminal HTTP error responses before returning the wrapped failure. It
preserves the original HTTP status, exact reference and no-retry policy.
All 112 focused cases pass on Python 3.11 and 3.14 with fatal warnings. All 491
broader cases pass in the original runtime-artifact coverage profile at 92.95%
lines and 85.93% branches, retaining its two existing exclusions and unchanged
85/75 floors (`.gate-logs/coverage-20261014T031247Z.json`). Ruff, strict typing,
docstrings, all 258 resource owners and task registration/integrity pass.
Publication, exact-SHA hosted/full gate and deployment acceptance remain
required for this follow-up.

[Python's HTTPError reference](https://docs.python.org/3.11/library/urllib.error.html)
documents that the exception is also the file-like response returned by the
client. This is the resource owner to close, not a reason to suppress warnings
or replace the standard-library client. The practitioner report above remains
motivation for method-specific coverage, not evidence of a leaked production
connection or successful deployment.

## Recheck a frozen candidate's served manifest — 2026-10-20

Build `39511340747` on exact development
`3b2fdc2274bf95aa5b7fd3a2d929286920d9daf1` rejects the pinned
`4fdfb367402535024cd7703a67e4549ca3b0a0ee05081e06d89e24dce3a3827a`
index in both Python gate jobs. The original native checker reproduces HEAD404
and GET404 in0.8 observed seconds; retain
`.gate-logs/v012-manifest-red-3b2fdc2-20261020.log`. Neither a source review nor
an earlier successful availability probe establishes current registry retention.
The immutable run summary retains all eight shard rows as unknown/censored:
the failed barrier admits no shard timing or throughput claim.

The existing actual refresh owner resolves
`sha256:086f25bff2b5e44eaa6953c8165cf6c4dcb3af8018b37053b510c7d5d796613c`
in0.5s. A separate actual exact-digest check passes in0.4s. The native writer
changes only the base identity in the definition/lock and the definition hash;
all other frozen inputs remain. The reviewed identity fixture adopts this
independently observed digest while preserving both exact YAML/lock assertions.
The old identity test first fails1/1; no assertion is removed or weakened. The complete original artifact, fallback and runtime-boundary owners then pass127/5.39s with warnings fatal; native seven-input runtime validation, Ruff, strict typing and Markdown pass. This pin-only repair changes no producer implementation or coverage policy.

Before another guarded development repair push, run the existing owner with
`ANSIBLE_EE_BASE_IMAGE_CHECK_VALIDATE_ONLY=0`; its mode1 output is an offline
plan. Refresh only through `make refresh-ansible-base-image
ANSIBLE_EE_BASE_IMAGE_REFRESH_VALIDATE_ONLY=0`, then separately check the exact
new pin. Align `VIRTUAL_ENV` with the selected `UV_PROJECT_ENVIRONMENT` in both
outer/child Make contexts; do not silence UV's mismatch warning or use --active.
Do not rerun the failed immutable hosted SHA or substitute a mutable tag.

The earlier Docker forum HEAD404 report remains motivation for bounded method
fallback, not evidence of the cause here. Both methods fail for this old pin;
there is no basis to label the failure garbage collection or a transient event.
The existing pre-publication runtime owner now invokes the same exact-digest
checker in actual mode before its original90/60 observed replay and transport.
It selects canonical Python311 without downloads/sync and aligns VIRTUAL_ENV,
including when a caller supplies conflicting UV/runtime or validate-only inputs.
Failure is terminal before observer/replay/Git; success executes exactly one
native check and retains the original runtime/transport failure guards. Mode1
remains offline with no environment acquisition. No second registry client,
observer, proof cache, automatic refresh or timeout increase is introduced.

All seven failing-first publication controls reproduce the previous missing
check; the complete native publication/integration/manifest/artifact/fixture
cohort passes223/38.45s with fatal warnings. Original legacy/forced publication,
caller policy, transport failure and plan assertions remain, extended for the
new prerequisite. The final complete publication/integration/guide/objective
cohort also passes91/34.88s on canonical Python3.12.15 with fatal warnings.
Native268target contracts,1174targets/0duplicates, strict
fixture typing and Ruff pass; the documented offline behavior remains mutation
free. These controls validate routing, not hosted success or a full gate.

A live new manifest is input evidence only. Full image build/offline warm,
canonical release gate, exact-successor hosted CI, deployment acceptance and
receipt-bound promotion remain required. All seven S98 tasks remain open.

### Coverage diagnostics require the served input

The retained3b2fdc2 gate job118932860510 continues into the hermetic Azure
coverage owner after the exact manifest fails. Its explicit !cancelled()
condition overrides GitHub's default success requirement. Give the existing
manifest step the stable ansible_base_manifest ID and require that step's
successful outcome before coverage. Keep the existing !cancelled()/Python311
condition, fatal result and complete coverage owner. In particular, lint failure
after a verified manifest still permits independent coverage diagnostics; a
broad success() condition would drop that original assertion intent.

The new parsed-workflow regression first fails1/5PASS. All133 original/new
build-failure, runtime-boundary, artifact and fallback cases then pass4.43s with
fatal warnings. Ruff and strict typing pass. The original lint/coverage-order
assertion is retained verbatim. Hosted evaluation of the new dependency remains
pending; a parsed YAML assertion is not an Actions execution or full gate.

[GitHub's status-check reference](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#status-check-functions)
states that a status function overrides the implicit success requirement and
that !cancelled() permits work regardless of earlier success/failure. The
[2022-2024 practitioner discussion](https://github.com/orgs/community/discussions/26303)
records continuing confusion around always(), cancellation and expression/YAML
boundaries. This motivates an explicit prerequisite outcome in the original
native workflow, without a replacement expression parser or diagnostic runner.

## Served manifest refresh after ac4016f (2026-10-24 UTC)

Build `40080210078`, source
`ac4016fd7fc36566b5ce5354c7b051fd5f5a8f70`, fails the exact base-manifest
check in Python3.12 job `120832040110` with HTTP404 before the eight-shard
fan-out. The pinned `086f25bf...` identity is unavailable at that check;
this establishes no garbage-collection, transient-outage or Python-version
cause. Preserve the hosted failure and its unknown/censored downstream work.

The existing offline refresh plan passes without network or source mutation.
The unchanged native refresh then resolves the supported Stream9 source to
`sha256:6f8045fc4f552544d8a401d61848bafc8d06ca7c11897c5b54a2c37fc419a20a`.
A separate native exact-digest check passes for that new configured identity.
Both runs retain90total/60quiet; their producer-owned receipts are:

| Owner | Receipt below `.gate-logs/observed/` | Native seconds / status |
| --- | --- | --- |
| refresh-ansible-base-image | `v012-ee-base-refresh/20261024T090810Z-89298-4c842dcb.json` | 0.514 / 0 |
| check-ansible-base-image | `v012-ee-base-exact-check/20261024T090816Z-89324-56008b64.json` | 0.629 / 0 |

The refresh changes only the definition's exact base pin, the lock's matching
base pin and its definition hash. All other lock inputs retain their values.
The existing CENTOS_STREAM9_INDEX fixture adopts the independently
checked identity while retaining every exact definition/lock assertion. No
producer, workflow, method fallback, timeout, threshold or release setting
changes; no mutable image is used for execution.

This is fresh primary registry-input evidence. The OCI, Quay, QIIME and Docker
forum references above retain their recorded scope; no new forum page was
retrieved and none identifies the cause of this incident. No image pull/build,
full offline warm, hosted-success or deployment result follows from a served
manifest. Refreshing the source pin changes no active runtime: build beside
active, new-job switch, in-flight drain and rollback remain required.

Source context is the uncommitted four-path repair on the exact source above.
Complete original artifact/fallback/runtime-boundary owners on both installed
runtimes remain pending Root's four-path content guard and source review.
Root owns the clean checkpoint and first exact-SHA ci-failure-repair receipts;
availability alone closes no native failure family. Full canonical gate,
coverage, new-SHA hosted/deployment and promotion proofs remain pending.
