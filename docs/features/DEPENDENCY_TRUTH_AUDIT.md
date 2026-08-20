# Dependency Truth Audit

## Contract

`make deps-audit` is the repository's fail-closed deptry boundary. A dependency
finding exits nonzero; the target does not mask deptry with `|| true` or convert
errors into informational output. The target is registered in the make target
contract and its behavioral example is `make deps-audit`.

The first authoritative replay reported 109 findings while the old target still
returned success. Configuring PEP 621 development groups and import-name
mappings reduced that to nine intentional dynamic/entrypoint cases. Those cases
are explicitly adjudicated in `[tool.deptry.per_rule_ignores]`, after which the
audit reports zero findings.

## Dependency model

The manifest distinguishes four dependency shapes:

1. Runtime imports are direct project dependencies. Imports previously obtained
   only through another package—`annotated-types`, `langchain-core`, and
   `openai`—are now declared directly.
2. Operator-selected adapters are named extras: MySQL, VMware, networking,
   Hindsight, model benchmarking, XML security, and platform sandboxing.
3. The optional `dev` group is classified as development-only, so pytest,
   linters, type stubs, and packaging tools are not misreported as unused
   runtime packages.
4. First-party `ansible_collections` and distribution-to-module mappings
   (`scikit-image` to `skimage`, Azure distributions to the `azure`
   namespace, and similar cases) are represented explicitly.

The unused `structlog` runtime dependency was removed. The Pillow entries in
the game extras are intentional and restored: the `game-e2e` and `e2e-all`
extras declare `pillow>=12.3.0` as a security floor so the game pipeline never
resolves an image decoder below the current security-fix release, even though
Pillow is only reached transitively (via scikit-image). Pillow is therefore
adjudicated in `[tool.deptry.per_rule_ignores]`. The observability extra now
directly declares `opentelemetry-proto`, whose modules are imported by the
receiver.

## Dynamic and entrypoint adjudications

The narrow DEP002 list is not a blanket rule suppression:

- Gunicorn, uvicorn-worker, vLLM, and llama-cpp are executed through process or
  module entrypoints.
- aiosqlite is selected through the SQLAlchemy URL scheme.
- boto3, msgpack, OpenTelemetry, Torch, and langchain-openai are loaded with
  guarded `importlib` calls.
- pqcrypto selects a versioned KEM module dynamically.

Each remains a declared dependency because the corresponding runtime path would
otherwise fail when selected. Adding a new ignored package requires updating
this document and the focused contract test.

Hindsight is intentionally **not** on that ignore list. On 2026-08-20 the
repository's literal `importlib.import_module("hindsight_client")` call still
produced DEP002 despite the distribution-to-module mapping. The adapter now uses
a guarded static import, so the extra remains optional at runtime while its
dependency truth is mechanically visible. This avoids turning a real adapter
dependency into a permanent audit exception.

## Practitioner evidence

- A long-running [Poetry request to prune unused packages
  (#4135)](https://github.com/python-poetry/poetry/issues/4135) accumulated years
  of practitioner interest and specifically points users toward deptry. It
  demonstrates that stale dependencies are a persistent maintenance problem,
  not a one-time cleanup.
- The deptry author's [original /r/Python announcement
  thread](https://www.reddit.com/r/Python/comments/x911kg) describes the tool's
  purpose—finding obsolete, transitive, missing, and misplaced dependencies—and
  invited real-project feedback.
- Deptry's [current configuration
  reference](https://deptry.com/usage/) documents development-group
  classification, known-first-party namespaces, package/module mappings, and
  limited dynamic-import extraction. Its [0.18.0 changelog
  entry](https://deptry.com/CHANGELOG/#0180-2024-07-31) records the original
  `importlib.import_module` support. Gludd uses those native capabilities and a
  statically auditable guarded import instead of adding a custom scanner or a
  Hindsight suppression.

## Security, resources, and ZDD

Optional adapters remain absent from the default environment, limiting attack
surface and disk use. Their versions and artifact hashes are still resolved in
`uv.lock`, so an enabled adapter is reproducible and auditable. The default
sync installs only the runtime and development sets; GPU/native extras are never
compiled by the audit.

This change is ZDD-safe because it changes candidate packaging metadata and
quality gates, not live workers, schemas, or network routes. A candidate artifact
must pass deptry, lock integrity, license, vulnerability, test, and coverage
checks before promotion. Existing workers keep their immutable environment until
the new artifact is healthy; rollback selects the previous artifact and lock.
