# Node Package-Manager Pinning

Status: beta4 release contract

## Contract

The OpenCode tool package declares an exact `packageManager` value of
`npm@12.0.2`. The pin turns npm's recurring major-update notice into reviewed,
repository-owned configuration instead of letting developer machines and CI select
different package-manager majors. Application and plugin dependencies remain locked
by `.opencode/package-lock.json`; the package-manager pin does not loosen any
package range or replace the lockfile.

The regression test reads the tracked manifest directly and fails closed if the pin
is absent, ranged, or changed independently. A future npm update is therefore a
small reviewed manifest/test change with the existing Node dependency validation and
audit gates as rollout evidence.

## Compatibility and security

npm 12 requires a supported Node runtime, so release CI must continue exercising the
repository's Node 26 compatibility gate. Package installation stays bound to the
public registry override, isolated user config, namespaced cache, exact dependency
versions, and the locked integrity metadata. The pin is configuration only: it does
not install globally, mutate a developer's home directory, or grant lifecycle
scripts new authority.

## Zero-downtime rollout

The change affects build tooling, not a running Gludd service. Existing processes
continue serving while a candidate checkout validates the new toolchain. Promotion
is development to master only after the full gate and exact-SHA CI are green; rollback
is the prior manifest commit and lockfile, with no application-data migration.

## Observability and resources

The focused contract reports the expected package-manager string. Existing
`node-deps-sync` and `node-deps-audit` targets surface install and vulnerability
failures and use the project-scoped npm cache. No daemon, background process, or
persistent global installation is introduced.

## Practitioner evidence

The long-running Node.js request
[corepack #560](https://github.com/nodejs/corepack/issues/560) shows that automatic
`packageManager` insertion can silently fail or be affected by an unrelated parent
manifest, so this repository records the value explicitly. The older
[Node.js #50963](https://github.com/nodejs/node/issues/50963) documents teams trying
to keep contributors on one package-manager version and finding that an unenabled
Corepack makes the field ineffective. Those reports support keeping the exact pin
under a repository test instead of relying on workstation state. Corepack's own
[documented contract](https://github.com/nodejs/corepack) requires
`packageManager@x.y.z` and recommends an immutable checksum when Corepack owns the
bootstrap; Gludd retains its locked dependency integrity and treats any future
bootstrap change as a separate reviewed release change.
