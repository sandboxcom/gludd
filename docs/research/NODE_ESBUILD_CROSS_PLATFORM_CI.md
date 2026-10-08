# Cross-platform esbuild supply chain

## Incident

Development CI run `30712424749` failed before tests on both Python matrix legs.
The repository contained a tracked `node_modules/@esbuild/darwin-arm64`
executable, while GitHub Actions needed `@esbuild/linux-x64`. Every hot-module
compile therefore failed. A native dependency tree is a host artifact and must
never be copied between operating systems or committed.

## Enforced design

- `.opencode/package.json` pins esbuild exactly; `.opencode/package-lock.json`
  records package integrity and public-registry provenance.
- `node_modules/` is ignored and a unit test fails if any nested copy is tracked.
- `make node-deps-sync` runs `npm ci` with an isolated user config, a
  Gludd-namespaced cache, and an explicit configurable registry. CI invokes it
  before `make hot-reload-plugins`, so npm selects the runner's native package.
- `make node-deps-relock` creates the lock in a fresh canonical temporary
  directory. This prevents an installed tree or a machine-private registry from
  contaminating the committed lock.
- `make node-deps-audit` gates moderate-or-higher advisories by default and is a
  phase of `make security-audit`. The threshold and registry remain explicit
  operator settings.

The install scripts cannot be disabled blindly: esbuild uses an optional native
package and verifies/selects it during installation. Gludd instead minimizes the
script surface with an exact dependency, integrity lock, public provenance,
isolated configuration, and an audit gate.

## Upstream and operator evidence

- [esbuild 0.28.1 release](https://github.com/evanw/esbuild/releases/tag/v0.28.1)
  adds integrity verification to the Deno install path and fixes a Windows
  development-server traversal issue. Gludd pins this release rather than a
  floating pre-1.0 range.
- [esbuild issue #3478](https://github.com/evanw/esbuild/issues/3478) documents
  the long-lived operator failure mode where install options omit the native
  optional package. Maintainers confirm that the platform package must be
  installed rather than suppressed.
- [esbuild issue #3173](https://github.com/evanw/esbuild/issues/3173) records
  years of operator trouble with cached native modules, nondeterministic rebuilds,
  and CI/runtime mismatches. The Gludd lock-and-install boundary avoids carrying
  those binaries between hosts.

## Acceptance evidence

The regression suite verifies that no `node_modules` path is tracked, every lock
URL uses the configured public registry, the exact esbuild version matches the
lock, and CI installs before compiling. A clean platform install builds all 27
expected hot modules; the Node audit reports zero known vulnerabilities.
