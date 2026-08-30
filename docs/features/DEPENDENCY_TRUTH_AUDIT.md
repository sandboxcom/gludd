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

## Exact core ownership inventory (2026-08-29)

The beta4 ownership replay now complements deptry with
`config/core-python-dependency-ownership.json`. The focused unit contract
mechanically enumerates every PEP 621 core dependency, parses production AST
imports under `src/general_ludd` and `collections/ansible_collections`, and
compares collection execution-environment requirement manifests. Collection
test imports are deliberately excluded because they prove development usage,
not controller or managed-host runtime ownership. Any added, removed, moved, or
stale consumer therefore makes the normal unit suite fail with an exact path
diff.

The replay found no additional package that can safely leave the core lock.
Every direct dependency has at least one static core production consumer except
four exact runtime-selected dependencies: `aiosqlite` is named by the default
SQLAlchemy URL, `langchain-openai` by the default provider registry, `msgpack`
by the safe-cache importlib seam, and `uvicorn-worker` by Gunicorn's worker-class
string. Those four references are pinned by path and token rather than hidden
behind a broad unused-dependency allowlist. Because all packages remain proven
core requirements, `pyproject.toml` and `uv.lock` are intentionally unchanged.

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

The sources were revalidated on 2026-08-29. The upstream deptry reference still
states that it derives dependency truth by comparing declared packages with
Python imports and supports explicit distribution-to-module mappings. Poetry
issue #4135, opened in June 2021, records the long-lived practitioner need,
Poetry maintainers' conclusion that source parsing belongs in a separate tool,
and the later community recommendation of deptry. That division of
responsibility is why Gludd retains deptry for general package truth and adds
only a repository-specific exact ownership inventory for runtime-selected and
collection-boundary evidence.

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
The ownership replay is read-only, performs bounded single-process AST scans,
and starts no daemons. Rolling it back removes the inventory, checker, and
focused test together; it never mutates an installed environment or a managed
host. Keeping the lock unchanged also avoids candidate churn and preserves the
existing zero-downtime promotion and rollback artifact pair.
