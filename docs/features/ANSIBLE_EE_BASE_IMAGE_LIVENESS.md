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

As of 2026-09-29, the resolved multi-architecture index is
`sha256:0996d37c69b3a8c33042932d415ad7bf5a8122270264e835e5c0153615aef0e4`.
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
