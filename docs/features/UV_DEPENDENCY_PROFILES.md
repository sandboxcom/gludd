# UV Dependency Profiles

## Purpose

Gludd keeps the deployable application core at the repository root and moves
optional runtime, provider, test, quality, build, and science dependencies into
small independently locked uv projects under `requirements/profiles/`. A named
install set composes those projects only when a job or deployment needs them.

This design has two separate boundaries:

- **Resolution boundary:** the root and every profile have their own
  `pyproject.toml` and `uv.lock`. They are not uv workspace members and are
  resolved independently.
- **Installation boundary:** `make sync` installs the root and the profiles in
  one explicit set into a staged virtual environment, validates the resulting
  union, and promotes it only after the complete candidate passes.

Splitting resolution keeps unrelated dependency graphs from inflating or
constraining one universal lock. Explicit installation sets preserve the
reproducible environments required by CI, containers, feature jobs, audits,
and local development.

## Why independent projects, not a uv workspace

uv's documented model is one lock per project or workspace. Its
[workspace guide](https://docs.astral.sh/uv/concepts/projects/workspaces/)
states that all workspace packages share one lockfile and that `uv lock`
operates on the entire workspace. The same guide recommends independent
projects when packages need separate environments or finer-grained dependency
resolution. It also warns that workspace members must remain mutually
compatible. The
[resolution guide](https://docs.astral.sh/uv/concepts/resolution/)
adds that project dependencies, extras, and dependency groups are normally
resolved together into a universal lock.

Those are upstream product guarantees. They are why Gludd does not attempt to
create several locks inside one uv workspace or rename a workspace lock by
configuration. Each Gludd profile is instead a real standalone project, which
is the supported uv unit for independent resolution.

The following upstream reports are supporting operational evidence, not uv API
guarantees:

- In [uv issue #9735](https://github.com/astral-sh/uv/issues/9735), a user
  reported a 35,000-line, 2.5 MB lock, lock times above 30 minutes, and
  occasional out-of-memory failures after repeated conflict-marker resolution.
  uv fixed that particular exponential marker-growth bug in
  [PR #11293](https://github.com/astral-sh/uv/pull/11293), released in uv
  0.6.2. The report therefore does **not** establish a current uv defect. It
  does show why Gludd treats lock growth as a measured, fail-closed property
  instead of assuming it can never recur for a different dependency graph.
- The long-lived practitioner request
  [uv issue #7251](https://github.com/astral-sh/uv/issues/7251), opened in
  September 2024 and still open when rechecked on 2026-10-06, asks for multiple
  named workspaces and lockfiles in one monorepo. The reporter notes that the
  workable approach is to put independent workspace definitions in separate
  directories. Gludd adopts the supported independent-project form without
  depending on that proposed feature.
- A production-use
  [/r/Python discussion](https://www.reddit.com/r/Python/comments/1ixryec/anyone_used_uv_package_manager_in_production/)
  includes practitioner concern about unexpectedly large uv locks and separate
  reports that teams export locked requirements in build stages. These are
  anecdotes, not compatibility promises; Gludd relies on committed locks and
  executable gates instead.

Sources and issue status in this section were revalidated on 2026-10-06.

## Repository layout and invariants

The root `pyproject.toml` contains only the deployable core dependency closure.
It has no `[project.optional-dependencies]` table and no `[dependency-groups]`
table. The root `uv.lock` is therefore the independently resolved core lock.

`config/dependency_profiles.toml` is the sole catalog for profiles and install
sets. Every entry under `profiles` points to one directory below
`requirements/profiles/`. Each profile directory contains:

- a PEP 621 `pyproject.toml` named `gludd-profile-<profile>`;
- `[tool.uv] package = false`, because the project exists to resolve
  dependencies rather than publish a package;
- no `tool.uv.workspace` declaration;
- no dependency on `general-ludd-agent`; and
- its own committed `uv.lock`.

The catalog currently defines these 25 independent profiles:

- runtime and controller: `agent-runtime`, `ansible-controller`;
- providers and services: `aws`, `azure`, `gcp`, `mysql`, `vmware`,
  `networking`, `observability`, `hindsight`, and `platform-sandbox`;
- optional product paths: `benchmark`, `cuda-inference`,
  `cuda-inference-mcp`, `decision-codification`, `game-e2e`,
  `local-inference`, `presentation-test`, and `xml-security`;
- development capabilities: `dev-ansible`, `dev-build`, `dev-quality`,
  `dev-science`, `dev-security`, and `dev-test`.

The profile manager fails closed if a project escapes the repository, a set
contains duplicate or unknown profiles, a profile path is reused, a profile is
packaged or becomes a workspace, the root regains extras or dependency groups,
or a profile depends on the root project.

Composition happens only at install time. Consequently, an independent profile
lock does not constrain the root or another profile while uv resolves it. The
ordered set and the final `uv pip check` are part of the compatibility contract.
Do not assume that two individually valid locks form a valid environment.

The CUDA set demonstrates an intentional partition. vLLM 0.30.0 plus its
Torch 2.13.0 graph fits below the limit only when vLLM's MCP dependency branch
is independently locked. The `cuda-inference` project uses uv's documented
[dependency exclusion](https://docs.astral.sh/uv/concepts/resolution/#dependency-exclusions)
for `mcp`; the `cuda-inference-mcp` project pins that branch, and the public
`cuda-inference` set always installs both. The final `uv pip check` proves the
composed environment still satisfies vLLM's published metadata. Removing
either half is unsupported.

## Hard lock-size guard

The catalog fixes `max-lock-lines = 2499`. Validation reads the root lock and
every profile lock and rejects a missing lock or any lock with 2,500 or more
lines. The limit is intentionally below 2,500, applies to all lock projects,
and is checked again after regeneration.

The guard measures committed output rather than trying to predict resolver
complexity. Do not raise it merely to land a dependency. First remove accidental
requirements, narrow unsupported platform or Python ranges where product policy
allows, or split a genuinely independent capability into another profile.
Never hand-edit a uv lock; uv documents the lock as a managed artifact in its
[project guide](https://docs.astral.sh/uv/guides/projects/).

## Explicit install sets

The root project is always installed first and is not repeated in the lists
below. The profile order shown is authoritative because later profile syncs use
`--inexact` to add to the staged root environment.

- `core`: `agent-runtime`.
- `audit-runtime`: `agent-runtime`, `ansible-controller`, `aws`, `gcp`,
  `azure`, `observability`, `game-e2e`, `mysql`, `vmware`, `networking`,
  `hindsight`, `benchmark`, `decision-codification`, `xml-security`,
  `platform-sandbox`, `local-inference`, `cuda-inference`, and
  `cuda-inference-mcp`.
- `build`: `agent-runtime`, `dev-build`.
- `build-azure`: `agent-runtime`, `azure`, `dev-build`.
- `ci`: the `development` profiles plus `presentation-test`.
- `ci-azure`: the `ci` profiles plus `azure`.
- `ci-game-e2e`: the `ci` profiles plus `game-e2e`.
- `ci-local-inference`: the `ci` profiles plus `local-inference`.
- `development`: `agent-runtime`, `ansible-controller`, `dev-test`,
  `dev-quality`, `dev-security`, `dev-build`, `dev-ansible`, `dev-science`.
- `ansible-controller`: `agent-runtime`, `ansible-controller`.
- `aws`: `agent-runtime`, `aws`.
- `azure`: `agent-runtime`, `azure`.
- `benchmark`: `agent-runtime`, `benchmark`.
- `cuda-inference`: `agent-runtime`, `cuda-inference`,
  `cuda-inference-mcp`.
- `decision-codification`: `agent-runtime`, `decision-codification`.
- `e2e-all`: `agent-runtime`, `game-e2e`, `azure`.
- `game-e2e`: `agent-runtime`, `game-e2e`.
- `gcp`: `agent-runtime`, `gcp`.
- `hindsight`: `agent-runtime`, `hindsight`.
- `local-inference`: `agent-runtime`, `local-inference`.
- `mysql`: `agent-runtime`, `mysql`.
- `networking`: `agent-runtime`, `networking`.
- `observability`: `agent-runtime`, `observability`.
- `platform-sandbox`: `agent-runtime`, `platform-sandbox`.
- `presentation-test`: `agent-runtime`, `presentation-test`.
- `sandbox`: `agent-runtime`.
- `sbom`: `agent-runtime`, `ansible-controller`, `dev-test`, `dev-quality`,
  `dev-security`, `dev-build`, `dev-ansible`, `dev-science`.
- `vmware`: `agent-runtime`, `vmware`.
- `xml-security`: `agent-runtime`, `xml-security`.

Adding a profile is incomplete until it is registered in the catalog, assigned
to every required explicit set, independently locked, below the line ceiling,
covered by the profile contract tests, and mapped to its CI or deployment
consumer. Ad hoc combinations and direct package installs are unsupported.

## Operator commands

Repository policy requires the Make interface. The uv commands described below
are emitted implementation details, not commands to invoke directly.

Verify the root and all profile locks without modifying them:

```console
make relock DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY=1
```

Regenerate the root and all profile locks after an intentional metadata change:

```console
make relock DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY=0
```

The regeneration target runs uv lock once for the root and once for every
independent project, then applies all structural and line-count guards. Review
the entire changed lock cohort; a later failure can leave earlier generated
locks changed in the worktree, but it never changes a running environment.

Validate a complete development install plan without promotion:

```console
make sync DEPENDENCY_PROFILE_SET=development \
  DEPENDENCY_PROFILE_ENVIRONMENT=.venv-development \
  DEPENDENCY_PROFILE_PYTHON=3.11 \
  DEPENDENCY_PROFILE_VALIDATE_ONLY=1
```

Build, verify, and promote that environment:

```console
make sync DEPENDENCY_PROFILE_SET=development \
  DEPENDENCY_PROFILE_ENVIRONMENT=.venv-development \
  DEPENDENCY_PROFILE_PYTHON=3.11 \
  DEPENDENCY_PROFILE_VALIDATE_ONLY=0
```

Set every variable explicitly. Relative environment paths resolve from the
repository root. CI and concurrent worktrees must use distinct, namespaced
paths rather than sharing a mutable virtual environment.

The validate-only sync still validates the catalog and every relevant lock,
then asks uv for dry-run syncs. It deliberately skips `uv pip check` and does
not promote, so it is a preflight rather than deployment evidence. A real sync
must end with `PROFILE_SYNC_PASS`; any `PROFILE_ERROR` is a hard failure.

Run the dependency-truth audit with:

```console
make deps-audit
```

The target checks the `audit-runtime` locks, atomically exports the deduplicated
direct-requirement union to a temporary file, and runs deptry against production
source with `config/deptry_profiles.toml`. It fails closed on either invalid
profile state or dependency-truth findings.

Audit every runtime lock for published vulnerabilities with:

```console
make pip-audit-gate
```

The profile manager invokes uv's preview
[`audit` command](https://docs.astral.sh/uv/reference/cli/#uv-audit) once for
the root and once for every selected independent project, always with
`--locked`. This
keeps findings attributable to one lock instead of reconstructing an unlocked
aggregate. Catalog adjudications are scoped to the lock that needs them; a
new advisory in any other lock still fails the gate.

The CUDA lock deliberately retains setuptools 80.10.2 because vLLM 0.30.0
requires setuptools below 81. The one adjudicated advisory,
[GHSA-h35f-9h28-mq5c](https://github.com/pypa/setuptools/security/advisories/GHSA-h35f-9h28-mq5c),
affects source-distribution manifest exclusion on macOS APFS/HFS+. The CUDA
profile is a Linux x86_64 runtime, does not publish source distributions, and
therefore cannot exercise that path. Do not copy this adjudication to another
profile. Remove it when vLLM accepts setuptools 83 or newer.

## Staged sync and zero-downtime promotion

The
[uv locking and syncing guide](https://docs.astral.sh/uv/concepts/projects/sync/)
documents `--locked` as the mode that rejects stale project metadata instead of
updating the lock, and exact sync as the default behavior that removes packages
outside the selected project. Gludd wraps those primitives as follows:

1. Validate the entire profile catalog, lock presence, and lock line limits.
2. Run a non-mutating lock check for the root and every profile in the selected
   set.
3. Create a uniquely named staging directory beside the target environment.
4. Exact-sync the root into that staging environment with `--locked`.
5. Sync each selected independent profile, in catalog order, with `--locked`
   and `--inexact` into the same staging environment. `--inexact` is required
   here so one profile does not remove packages installed by the preceding
   projects.
6. Run `uv pip check` against the staged environment's Python interpreter.
7. Rename an existing target to a PID-scoped backup, rename the complete staged
   environment to the target, and delete the backup only after promotion
   succeeds.
8. Remove the staging parent in all success and failure paths.

Every subprocess is announced as `PROFILE_COMMAND ...`, so a long resolution
or install has observable phase progress. The manager starts no daemon and
does not mutate unrelated environments.

If resolution, download, install, or `pip check` fails, the target environment
has not been touched. If the candidate rename fails after the old target was
moved aside, the manager renames the backup back to the target before raising
the error. A pre-existing PID-scoped backup causes a fail-closed stop rather
than overwrite of possible recovery data.

This is the environment half of ZDD, not a traffic switch. Existing workers
continue using their already loaded predecessor code and dependencies. The
service deployment controller must start and health-check candidate workers,
route new traffic only after they are ready, drain predecessor workers, and
retain the previous immutable release until the observation window passes.

## CI, container, SBOM, and audit contract

Every consumer names one set; no consumer relies on whichever packages happen
to exist in a reused `.venv`.

| Consumer | Required set | Contract |
| --- | --- | --- |
| Runtime and production container | `core` | Root plus `agent-runtime`; no test, quality, provider, science, or GPU profile unless a distinct artifact declares it. |
| Normal CI and repository gate | `ci` | The development graph plus the presentation-test profile used by the complete CI suite. |
| Azure artifact build | `build-azure` | Root plus agent runtime, Azure SDKs, and build tooling. |
| Repository SBOM job | `sbom` | `make sbom` atomically syncs this set immediately before CycloneDX inventories the resulting environment. |
| Dependency truth and runtime audit | `audit-runtime` | `make deps-audit` validates direct ownership; `make pip-audit-gate` audits each committed lock independently with only lock-scoped adjudications. |
| Feature-specific CI | Matching named set | Use `ci-azure`, `ci-game-e2e`, `ci-local-inference`, or another exact feature set; do not fall back to a broader reused environment to hide missing ownership. |

All CI lanes first run the non-mutating relock check. A lock mismatch, missing
lock, oversized lock, unknown set, failed staged sync, or failed `pip check`
prevents the job and release from advancing.

A container build copies the root metadata and lock, the catalog, the profile
manager, and all profile metadata and locks into its dependency layer so catalog
validation remains complete. It performs a locked dependency-only `core` sync
in the build stage, builds the application wheel, installs that wheel without
re-resolving dependencies, and copies only the completed environment into the
runtime artifact. Copying development environments or running an unlocked
resolve in the runtime stage violates the contract.

`make sbom` first atomically replaces `.venv-sbom` from the locked `sbom` set,
leaving the developer `.venv` untouched, and then generates `dist/sbom.json`
from that exact environment. A sync or
`uv pip check` failure prevents the old environment from being presented as a
new SBOM input. Container SBOMs are generated from the separately built `core`
runtime artifact, not inferred from the broader repository SBOM.

Dependency truth, vulnerability scanning, license policy, SBOM generation, and
container construction all consume committed lock identities. Their scopes are
different by design, but none may independently resolve an uncommitted package
set. Promotion requires the applicable audit, tests, at least 85% aggregate
coverage with no production file below 75%, and the normal release gates to be
green before traffic moves.

## Troubleshooting

### `unknown profile set`

Check the exact set name in `config/dependency_profiles.toml`. Do not work around
the error with a direct package install or an unregistered combination. Add or
change a set only as a reviewed catalog change with tests and consumer mapping.

### Lock is missing or stale

Run the non-mutating relock check first. If metadata changed intentionally, run
the real relock target, review root and profile lock changes together, and rerun
the check. If no metadata was intended to change, restore the known-good lock
cohort rather than accepting a new resolution.

### Lock reaches 2,500 lines

Treat this as dependency-boundary drift. Identify the profile that pulled in
the unrelated graph, inspect broad version or platform constraints, and split
independent capabilities when appropriate. Confirm that the pinned uv version
contains the fix associated with issue #9735 if conflict markers are present.
Do not increase the ceiling or edit resolution markers by hand.

### Staged sync fails before `PROFILE_SYNC_PASS`

Use the last `PROFILE_COMMAND` line to identify the root or profile that failed.
The old target remains authoritative. Resolve the dependency or artifact error,
relock only through the Make target when metadata really changed, then repeat
validate-only and real sync. Never point workers at the staging directory.

### Final `uv pip check` fails

The independent locks are individually valid but their installed union is not.
Align compatible constraints, remove the incompatible profile from that set, or
use separate environments. Reordering profiles merely to make one version win
is not a compatibility fix.

### A profile package disappears during composition

Only the first root sync is exact. Every profile sync must remain `--inexact`.
Use `make sync`; a direct exact sync of an individual profile into the composed
environment will remove packages belonging to the root and earlier profiles.

### SBOM or audit contains the wrong scope

Discard the environment and recreate it from its required explicit set in an
isolated path. Do not repair the result by filtering SBOM entries or ignoring
audit findings. Record the set name alongside the artifact so reviewers can
reproduce its dependency closure.

### A stale promotion backup exists

Stop and preserve it as recovery evidence. Determine whether the target or the
backup is the last healthy environment before any cleanup. The manager refuses
to overwrite that directory specifically to avoid destroying the only usable
predecessor after an interrupted promotion.

## Exact rollback

There are two rollback moments.

**Before `PROFILE_SYNC_PASS`:** no operator action is normally required. The
target was never changed, or the rename exception path restored it. Keep traffic
on the predecessor, retain the emitted `PROFILE_ERROR`, and fix the candidate.

**After `PROFILE_SYNC_PASS`:** the transient promotion backup has already been
deleted. The rollback source is the previous immutable release, including its
root `pyproject.toml`, root lock, profile catalog, selected profile metadata and
locks, and its own installer contract. Do not combine metadata from one release
with locks from another.

For a profile-enabled previous release, use a clean checkout or unpacked
artifact of that exact release and the original deployment tuple. For example,
to restore the prior development environment:

```console
make relock DEPENDENCY_PROFILE_RELOCK_VALIDATE_ONLY=1
make sync DEPENDENCY_PROFILE_SET=development \
  DEPENDENCY_PROFILE_ENVIRONMENT=.venv-development \
  DEPENDENCY_PROFILE_PYTHON=3.11 \
  DEPENDENCY_PROFILE_VALIDATE_ONLY=1
make sync DEPENDENCY_PROFILE_SET=development \
  DEPENDENCY_PROFILE_ENVIRONMENT=.venv-development \
  DEPENDENCY_PROFILE_PYTHON=3.11 \
  DEPENDENCY_PROFILE_VALIDATE_ONLY=0
```

For a production container, substitute `core` and the production environment
path; for a feature worker, substitute its exact named set. If the previous
release predates dependency profiles, use the installer shipped in that release
rather than trying to translate its monolithic lock into the new catalog.

Health-check the restored candidate, redirect new traffic to it, drain the bad
release, and retain both release records through the observation window. If the
rollback sync fails, its own staged-promotion rules preserve the currently
active target. Source rollback must revert the whole dependency-profile cohort;
partial rollback of only `pyproject.toml`, only one lock, or only the catalog is
unsupported.
