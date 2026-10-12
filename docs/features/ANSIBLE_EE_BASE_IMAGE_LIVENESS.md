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
