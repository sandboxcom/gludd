# Binary Bundling — Fully Self-Contained Redistributable (Design)

Status: **design-complete, not yet implemented.** Line-anchored against current
`master`; re-confirm with a Read before editing (lines drift). Goal: every
external binary gludd shells out to is either (a) bundled inside the
redistributable and resolved bundle-first at runtime with an empty `PATH`, or
(b) explicitly documented as a host prerequisite that cannot be bundled
(container runtimes) or is provided by the Python runtime itself.

---

## 0. The central finding

gludd has **two disconnected binary systems** that were never wired together:

1. **`BinaryBootstrapper`** (`src/general_ludd/filestore/bootstrap.py:67`) —
   downloads, sha256-verifies (fail-closed), extracts, chmods, and locates
   binaries. Bundle-first (`get_bundled_binary_path`, bootstrap.py:180-190),
   then filestore-download fallback. This is the *right* design.
2. **`BinaryPathResolver`** (`src/general_ludd/config/binary_paths.py:24`) —
   consulted by the actual deploy/secrets call sites. It is **PATH-only**:
   `resolve()`/`is_available()` (binary_paths.py:28-44) call `shutil.which()`
   and nothing else — no bundle, no filestore, no `sys._MEIPASS`. Confirmed
   zero references to `_MEIPASS` anywhere in `src/`.

`get_infra_binary()` (binary_paths.py:46-49) returns the bare string `"tofu"`
or `"terraform"`; `DeploymentManager._run_terraform` (`infra/deployment.py:183`)
hands that bare name straight to `asyncio.create_subprocess_exec` (line 184).
`SecretsManager.start_local_container` (`secrets/manager.py:596`) does the
same via `get_container_runtime()`. **Neither ever calls
`BinaryBootstrapper.get_bundled_binary_path`/`get_binary_path`.** So even
though `BinaryBootstrapper.KNOWN_VERSIONS` (bootstrap.py:79-84) downloads and
sha256-verifies `openbao` and `opentofu` into `dist/binaries/` /
`~/.local/share/general-ludd/filestore/binaries/`, **those downloaded
binaries are never actually executed** — the real terraform/vault-family
invocations go through the PATH-only resolver and silently rely on a host
install. Same pattern for `codebase-memory-mcp`: bootstrapped as a native
binary (bootstrap.py:230, 292-295) but the MCP catalog launches it via
`npx -y codebase-memory-mcp@0.8.1` (`mcp/catalog.py:301`) instead — ignoring
the bootstrapped binary entirely.

Two call sites in the codebase already implement the **correct** bundle-first
pattern and should be the template for the fix:
- `RgSearch.locate_rg()` (`code_intelligence/rg_search.py:65-80`): bundled
  (`BinaryBootstrapper().get_bundled_binary_path("rg")`) then `shutil.which`.
- `_osquery_facet()` (`routers/facts.py:315-350`): filestore
  (`boot.get_binary_path("osquery")`, line 336) then `shutil.which("osqueryi")`
  (line 344).

---

## 1. Survey — every external binary, bundled/resolved status

| Binary | Shell-out site(s) | Bootstrapped? | Resolved bundle-first? | Gap |
|---|---|---|---|---|
| **opentofu** (`tofu`) | `infra/deployment.py:183-191` via `get_infra_binary()` | Yes — `KNOWN_VERSIONS["opentofu"]` (bootstrap.py:81), URL builder bootstrap.py:243-248 (amd64-only — but **native arm64 assets exist for 1.9.0**, so the emulation comment at bootstrap.py:244-245 is stale; §3 fixes this) | **No** — PATH-only (binary_paths.py:46-49) | Resolver never consults bootstrapper |
| **terraform** | (a) `infra/deployment.py:183` fallback name; (b) **`collections/importer.py:179-185`** `terraform validate` — hardcoded `argv=["terraform","validate"]`, gated only by `shutil.which(argv[0])` (importer.py:384), **never tries `tofu`** | No (only tofu is bootstrapped) | No | Not in manifest (BUSL license makes bundling terraform a deliberate choice, not an oversight — document as optional/operator-supplied). Site (b) is a second PATH-only gap that also ignores the resolver entirely |
| **openbao** (`bao`) | `secrets/manager.py` — only via **container image** (`start_local_container`, manager.py:582-629), never the native `bao` binary | Yes — bootstrap.py:80, URL builder bootstrap.py:233-242 | **No** — bootstrapped binary is unused; real path is `get_container_runtime()` → docker/podman container, not the local binary | Bootstrapped `bao` binary has no caller as a local-mode secrets backend; either wire it in or drop it from KNOWN_VERSIONS |
| **vault** | `binary_paths.py:51-54` `get_secrets_binary()` fallback | No | No | Same PATH-only issue; unreferenced call site (grep shows it's test-only) |
| **osquery** (`osqueryi`) | (a) `routers/facts.py:336` (facet probe); (b) **`connectors/osquery.py:71-118`** (`OsquerySource`) — bare default `self._binary="osqueryi"` (line 72), health-check only `shutil.which` (line 109) | Yes — bootstrap.py:82, tarball extraction `_TARBALL_BINARIES["osquery"]="osqueryi"` (bootstrap.py:354) | (a) **Yes** — reference impl, facts.py:332-347; (b) **No** — PATH-only, inconsistent with (a) | Migrate the connector (b) to the facts.py filestore-first pattern. **Also fix the URL builder** (bootstrap.py:250-275): it wrongly drops linux/arm64 (bootstrap.py:270-271) and wrongly assumes macOS ships arm64 — see §3 (linux has both arches; macOS tarball is x86_64-only) |
| **ripgrep** (`rg`) | `code_intelligence/rg_search.py:75` | **Partially** — NOT in `BinaryBootstrapper.KNOWN_VERSIONS` at all; bundled only via `Makefile:2267-2282 bundle-ripgrep` (curl + shasum -c) | Yes, resolution-wise (rg_search.py:65-80) | **`RG_SHA256` is a literal all-zero placeholder** (Makefile:2266) — `bundle-ripgrep` always fails the `shasum -c` check (Makefile:2274-2277) and falls back to `rm -f dist/binaries/rg` with just a warning (Makefile:2282). **ripgrep is never actually bundled in any build today.** |
| **codebase-memory-mcp** | `mcp/catalog.py:301` via `npx -y codebase-memory-mcp@0.8.1` | Yes — bootstrap.py:83, 292-295 | **No** — the bootstrapped native binary is bundled/downloaded but the MCP launcher ignores it and always uses npx | Wire an MCP transport mode that launches the bootstrapped native binary directly instead of npx, when present |
| **node / npx** | `mcp/transport.py:28-30` (`_MCP_EXEC_ALLOWLIST` includes `npx`,`node`); every `npx -y ...` MCP server in `mcp/catalog.py:209-301` (10 servers) | **No** | No | Real production dependency: every stock MCP server needs Node.js on PATH **plus network access to the npm registry** at every invocation (npx re-resolves unless cached) — the opposite of "bundled, no PATH". Not in `KNOWN_VERSIONS`. Also needed by `.opencode/plugin/*.ts` hooks but that usage (`tests/unit/_hook_fixtures.py:49`, `scripts/hook_plugin_harness.mjs`, `node --experimental-strip-types`) is **dev/CI-only** (testing gludd itself), not shipped-product runtime |
| **playwright / chromium** | **none found** | No | No | Zero references anywhere in `src/` or `scripts/` — browser automation is entirely unbuilt, not merely unbundled |
| **git** | Direct `subprocess`, always the bare literal `"git"`: `git_automation/repo.py:196` (`_run_git`) + clone/worktree/push at repo.py:226,447,480,536,590,764,790; `code_intelligence/git_intel.py:96`; `git_automation/pr_delivery.py:85`. `BinaryPaths.git` field (binary_paths.py:18) is **never referenced** by any resolver | No | No (host apt-get in Dockerfile:74) | Ubiquitous host prerequisite today; a static git binary *could* be bundled but isn't attempted — many bare-literal sites to migrate if pursued |
| **ansible-playbook** (CLI, **runtime**) | **`routers/stream.py:175`** (`/admin/stream/dispatch` role-clone, bare `["ansible-playbook","run-clone.yml"]` via Popen); `dogfood/runner.py:127-132` (syntax-check) | No | No — PATH-only, `BinaryPaths.ansible_playbook` field (binary_paths.py:17) unused | **Corrects a nuance**: the CLI *is* invoked at product runtime here (stream dispatch), not only in dev/CI — so ansible-playbook is a live host-PATH dependency for that route, distinct from the executor-API path that ships inside pyinstaller |
| **ansible-galaxy** (CLI) | `ansible/galaxy.py:152,165` (`search_galaxy`/`install_galaxy`), bare literal | No | No | Galaxy role/collection install — host-PATH dependency (needs network to Galaxy anyway) |
| **podman (ansible isolation)** | `ansible/isolation.py:41-43` default `executable="podman"` → `ansible/core_runner.py:583-629` `to_runner_kwargs()`; `runtime/container.py:76,132` default `runtime="podman"` | No | No | HOST-PREREQUISITE (container runtime) — same class as docker/podman below |
| **docker / podman** | `secrets/manager.py:607-627` (`start_local_container`) via `get_container_runtime()` (binary_paths.py:56-63) | No — correctly not attempted | N/A | **HOST-PREREQUISITE by design** — cannot bundle a container runtime inside a single-file redistributable |
| **ansible-playbook** (CLI) | `BinaryPaths.ansible_playbook` field (binary_paths.py:17) defined but no `get_*` caller found; product runtime drives `ansible.runner`/`core_runner` (the executor **API**, not the CLI) — `gludd.spec:46-50` explicitly excludes `ansible.cli` (Windows locale crash) | N/A | N/A | **PYTHON-RUNTIME-PROVIDED** — ansible-core ships inside the pyinstaller bundle as a Python dependency; the CLI binary itself is dev/CI-only (`Makefile` `ansible-syntax`, `molecule-test`, `verify-feature-claims` targets run `uv run ansible-playbook`) |
| **opa / conftest** | `tests/unit/test_opa_policies.py:27-45`, `tests/unit/test_collection_terraform_layout.py:135-152` — `shutil.which`-guarded, skip-if-absent | No | N/A | **DEV/CI-only** policy-test tooling; `BinaryPaths.opa`/`.conftest` fields (binary_paths.py:20-21) are otherwise unused — not part of the shipped runtime surface today |
| **uv** | `dependency/manager.py:79-80` `_has_uv()` | No | No (PATH-only) | Dependency-management self-update feature; low priority vs. the infra/secrets binaries |

---

## 2. Runtime resolution redesign — bundle → filestore-download → PATH

**Fix `BinaryPathResolver`** (`config/binary_paths.py:24-64`) so it delegates
to `BinaryBootstrapper` before ever calling `shutil.which`:

```python
def resolve(self, binary_name: str) -> str:
    configured = getattr(self._config, binary_name, None)
    if configured and "/" in configured:
        return configured                       # explicit operator override wins
    canonical = configured or binary_name
    bundled = self._bootstrapper.get_bundled_binary_path(canonical)  # NEW
    if bundled:
        return bundled
    stored = self._bootstrapper.get_binary_path(canonical)           # NEW
    if stored and os.path.isfile(stored):
        return stored
    found = shutil.which(canonical)
    return found or canonical
```

`is_available()` mirrors the same three-tier check. `get_infra_binary()` /
`get_secrets_binary()` must call `self.resolve(name)`, not return the bare
config string — today they return `self._config.opentofu` (the string
`"tofu"`) unconditionally (binary_paths.py:48, 53), never the resolved path.

**Call sites to migrate** (currently PATH-only or npx-only, must route
through the fixed resolver):
- `infra/deployment.py:183` — `self._binary_resolver.get_infra_binary()`.
- `collections/importer.py:179-185` — the second terraform site; route
  `["terraform","validate"]` through the resolver so it also honors a bundled
  `tofu` instead of the hardcoded bare `"terraform"` name.
- `connectors/osquery.py:71-118` — migrate `OsquerySource`'s bare
  `self._binary="osqueryi"` to the filestore-first pattern already in
  `routers/facts.py:332-347` (the two osquery sites are inconsistent today).
- `secrets/manager.py:596` — `get_container_runtime()` stays PATH-only
  (docker/podman are host prerequisites — correct as-is, no change).
- `mcp/catalog.py:301` (codebase-memory-mcp) — add a native-binary launch
  mode: if `BinaryBootstrapper().get_binary_path("codebase-memory-mcp")` (or
  bundled) resolves, launch that binary directly (stdio MCP transport already
  supports arbitrary `command`); fall back to the existing pinned `npx` entry
  only when no bundled/downloaded copy exists.
- `mcp/transport.py:28-30` (`_MCP_EXEC_ALLOWLIST`) — once `node` is bundled
  (§3), resolve the `node`/`npx` launcher through `BinaryPathResolver` too,
  so MCP servers still work with an empty `PATH`.

**`sys._MEIPASS` gap**: `BinaryBootstrapper._find_dist_bundled_dir()`
(bootstrap.py:198-208) only checks `__file__`-relative paths and `os.getcwd()`
— under a pyinstaller-frozen exe those don't reliably point at the extracted
bundle. Add a first candidate:
```python
import sys
if getattr(sys, "_MEIPASS", None):
    candidates.insert(0, os.path.join(sys._MEIPASS, "binaries"))
```
This requires `gludd.spec` to actually bundle a `binaries/` datas dir (§3) —
currently it does not.

---

## 3. Binary manifest — extend `BinaryBootstrapper.KNOWN_VERSIONS`

Replace the flat `KNOWN_VERSIONS`/`get_download_url` per-name `if` chain
(bootstrap.py:79-84, 223-248) with a per-platform manifest table so adding a
binary is a data-table edit, not new branching code:

```python
@dataclass(frozen=True)
class BinaryManifestEntry:
    version: str
    # {(os, arch): (url, sha256)} — os in {"darwin","linux","windows"},
    # arch in {"amd64","arm64"}. Absent combos are simply unavailable.
    assets: dict[tuple[str, str], tuple[str, str]]
    archive_member: str | None = None   # basename to extract from a .tar.gz/.zip; None = bare binary

BINARY_MANIFEST: dict[str, BinaryManifestEntry] = { ... }   # see the concrete data table below
```

`get_download_url`/`_extract_executable_member` generalize to look up
`BINARY_MANIFEST[name].assets[(os, arch)]` and
`BINARY_MANIFEST[name].archive_member` instead of the current per-binary
special cases (bootstrap.py:227-298, 349-356) — same fail-closed sha256 check
(`_verify_digest`, bootstrap.py:117-140) applies uniformly, no code change
needed there.

### Concrete manifest data (verified against upstream releases 2026-07-10)

Asset filename per `(os, arch)`; `archive_member` = in-archive path to the
executable. **URL templates** — ripgrep:
`github.com/BurntSushi/ripgrep/releases/download/14.1.1/<asset>`; node:
`nodejs.org/dist/v22.23.1/<asset>`; opentofu:
`github.com/opentofu/opentofu/releases/download/v1.9.0/<asset>`; osquery:
`github.com/osquery/osquery/releases/download/5.10.2/<asset>`; openbao/
codebase-memory-mcp: existing builders (bootstrap.py:233-298) are already
correct — leave as-is.

**ripgrep 14.1.1** (`archive_member` varies per asset dir; each asset has a
`<asset>.sha256` sidecar → pin source is `<url>.sha256`):
| os/arch | asset | member |
|---|---|---|
| darwin/arm64 | `ripgrep-14.1.1-aarch64-apple-darwin.tar.gz` | `ripgrep-14.1.1-aarch64-apple-darwin/rg` |
| darwin/amd64 | `ripgrep-14.1.1-x86_64-apple-darwin.tar.gz` | `.../rg` |
| linux/amd64 | `ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz` (musl is the ONLY x86_64 linux asset — matches Makefile:2260) | `.../rg` |
| linux/arm64 | `ripgrep-14.1.1-aarch64-unknown-linux-gnu.tar.gz` (no musl arm64) | `.../rg` |
| windows/amd64 | `ripgrep-14.1.1-x86_64-pc-windows-msvc.zip` | `.../rg.exe` |
Verified sha256 (fetched sidecar): linux x86_64-musl =
`4cf9f2741e6c465ffdb7c26f38056a59e2a2544b51f7cc128ef28337eeae4d8e`. This is
the value to drop into `Makefile:2266 RG_SHA256` today to unblock the existing
`bundle-ripgrep` target for the linux build.

**node v22.23.1** (LTS; `archive_member` = `node-v22.23.1-<os>-<arch>/bin/node`,
`.../node.exe` on windows; pin source = `nodejs.org/dist/v22.23.1/SHASUMS256.txt`
— **fetch live at manifest-build time, do not hardcode** transcribed values):
darwin/arm64 `node-v22.23.1-darwin-arm64.tar.gz`; darwin/amd64
`...-darwin-x64.tar.gz`; linux/amd64 `...-linux-x64.tar.gz`; linux/arm64
`...-linux-arm64.tar.gz`; windows/amd64 `...-win-x64.zip`.

**opentofu 1.9.0** — flat `tofu` binary at archive root (`archive_member=None`
works; `.exe` on windows). **Native arm64 assets DO exist** — the current
amd64-only URL builder (bootstrap.py:243-248) is needlessly conservative and
should be replaced. Pin source: consolidated `tofu_1.9.0_SHA256SUMS`.
| os/arch | asset | sha256 (from SHA256SUMS) |
|---|---|---|
| darwin/arm64 | `tofu_1.9.0_darwin_arm64.tar.gz` | `c1d3c0a5c9151bac8af48492ee735fcde2a6d27c5a3da056be2d2f3c242f07a6` |
| darwin/amd64 | `tofu_1.9.0_darwin_amd64.tar.gz` | `164e340b78e2799965022d28525be253d1e0ce9b91a020cbb2468bbdaebb68d0` |
| linux/amd64 | `tofu_1.9.0_linux_amd64.tar.gz` | `48b1e2ec8dd23c107d350432b8d73a4393ef014f8eaee063bdf1d8f481083a42` |
| linux/arm64 | `tofu_1.9.0_linux_arm64.tar.gz` | `dd667b0700801b79b102b92b1edaa7c41373c493e7348c5b88e3653458af30d3` |
| windows/amd64 | **not published** for 1.9.0 | n/a |

**osquery 5.10.2** — **corrects two wrong assumptions in the current code**
(bootstrap.py:250-275): (a) linux publishes BOTH amd64 and arm64 tarballs (the
code's `if osq_arch != "x86_64": return None` at bootstrap.py:270-271 wrongly
drops linux/arm64); (b) macOS publishes ONLY an x86_64 tarball — there is NO
darwin/arm64 `.tar.gz` (Apple Silicon runs it under Rosetta or uses the
universal `.pkg`), so the code's assumption that "macos ships both arches" is
wrong. No consolidated checksums file is published → the manifest-builder must
download-and-hash (this is the one binary lacking an upstream pin source).
| os/arch | asset | member |
|---|---|---|
| darwin/amd64 | `osquery-5.10.2_1.macos_x86_64.tar.gz` | `opt/osquery/lib/osquery.app/Contents/MacOS/osqueryi` |
| darwin/arm64 | **no tarball** (universal `.pkg` only) | n/a |
| linux/amd64 | `osquery-5.10.2_1.linux_x86_64.tar.gz` | `opt/osquery/bin/osqueryi` |
| linux/arm64 | `osquery-5.10.2_1.linux_aarch64.tar.gz` | `opt/osquery/bin/osqueryi` |

Note: the existing `_extract_executable_member` (bootstrap.py:300-330) matches
by **basename** (`osqueryi`), so the differing macOS-vs-linux in-archive
*paths* above are already handled — no per-path logic needed.

**terraform**: deliberately *not* added to the manifest — HashiCorp's BUSL
license makes redistributing a terraform binary a licensing decision, not a
technical gap. Keep it as the PATH-only fallback name it is today
(`BinaryPaths.terraform`, binary_paths.py:11) for operators who have it
installed and specifically need Terraform-only features (e.g. HCP Terraform
Cloud integration); `opentofu` is the bundled default.

**playwright / chromium — does not fit the single-URL/single-sha256 model.**
Playwright manages its own browser cache (`~/.cache/ms-playwright` or
`PLAYWRIGHT_BROWSERS_PATH`) and downloads per-Chromium-revision, not a stable
GitHub-release asset name. Two options, pick one at implementation time:
1. **Build-time bake-in**: a build step (`make bundle-playwright`) runs
   `PLAYWRIGHT_BROWSERS_PATH=dist/binaries/playwright-browsers uvx --with playwright playwright install chromium --with-deps` once per platform in CI, then the pyinstaller `datas` list includes that directory; at runtime set
   `PLAYWRIGHT_BROWSERS_PATH` to the bundled dir before importing playwright.
2. **`PlaywrightBootstrapper`**: a bootstrap.py sibling class that, on first
   use, shells `playwright install chromium` into the filestore's binaries
   dir with `PLAYWRIGHT_BROWSERS_PATH` redirected there — same fail-closed
   posture is harder here because playwright's own installer does its own
   (unpinned) download, so this path needs an explicit sha256 allowlist of
   known-good Chromium revision hashes to keep the "fail-closed" invariant
   the rest of `BinaryBootstrapper` guarantees.
Recommend (1) for the redistributable (deterministic, CI-verifiable) and
document (2) as a lighter-weight fallback for non-packaged (`pip install`)
usage.

---

## 4. Build packaging — the actual release pipeline never bundles anything

This is the single biggest concrete gap: **`make dist` bundles binaries
(Makefile:2212 `dist: build-executable bundle-binaries sbom`, tarball copy at
Makefile:2223 `cp -r dist/binaries $(TARBALL_DIR)/binaries`) but `make dist`
is never invoked by `.github/workflows/build.yml`.** The actual release
artifacts come from the `linux`/`macos`/`windows`/`termux` jobs
(build.yml:426-578), which:
1. Run `uv run pyinstaller gludd.spec --clean --noconfirm` directly
   (build.yml:443, 478, 524, 562) — **no `make bundle-binaries` /
   `bundle-ripgrep` step precedes this anywhere in the matrix.**
2. Package the tarball/zip from `dist/gludd` + `config`/`templates`/
   `playbooks` only (build.yml:446-450, 481-485, 528-532, 566-569) —
   **`dist/binaries` is never copied into `dist/release/`.**
3. `gludd.spec:8-14` `datas=[...]` does not list `dist/binaries` at all, so
   even if it were populated, pyinstaller would not embed it into the frozen
   exe's `_MEIPASS` payload.

**Fix:**
- Add a `bundle-binaries` step to each of the 4 release jobs, before the
  `pyinstaller` step, per-platform (Linux amd64/arm64 → `termux`/`linux` jobs,
  macOS arm64 → `macos` job, Windows amd64 → `windows` job) — matching
  `runner.os`/`runner.arch` to the manifest's `(os, arch)` key.
- Add `('dist/binaries', 'binaries')` to `gludd.spec:8-14` `datas=[...]` (and
  the playwright browser cache dir, if using build-packaging option 1 above)
  so pyinstaller embeds it and it's reachable via `sys._MEIPASS/binaries` at
  runtime (§2).
- Copy `dist/binaries` into `dist/release/` in the "Package tarball/zip"
  steps (build.yml:444-453, 479-488, 525-534, 563-572), mirroring the
  existing `cp -r dist/binaries $(TARBALL_DIR)/binaries` pattern already
  proven in `Makefile:2223` for the (currently-unused) `make dist` path.
- **Fix `RG_SHA256`** (Makefile:2266) — the real sha256 for
  `ripgrep-14.1.1-x86_64-unknown-linux-musl.tar.gz` is
  `4cf9f2741e6c465ffdb7c26f38056a59e2a2544b51f7cc128ef28337eeae4d8e` (§3);
  drop that in to unblock the target immediately (today it always fails closed
  silently, Makefile:2274-2282). Add per-platform variants for macOS/Windows if
  ripgrep is wanted there too — but the manifest approach (§3) supersedes this
  single-platform Makefile target entirely once implemented.
- Extend `scripts/download_bundled_binaries.py` (currently iterates
  `boot.KNOWN_VERSIONS`, lines 51-54) to iterate the new
  `BINARY_MANIFEST` and call a generalized per-platform download (it already
  has the right skip-if-bundled / skip-if-unavailable-for-platform shape,
  lines 18-27, 46-60 — no structural rewrite needed, just source from the new
  manifest).
- `container`/Dockerfile job: currently installs only `git`/`ca-certificates`/
  `tini` via apt (Dockerfile:72-77) — add a `COPY dist/binaries
  /app/binaries` (built by a `bundle-binaries` stage) or an apt-based install
  of `opentofu`/`ripgrep`/`osquery` if the container image is meant to be
  self-contained too; currently it relies on nothing beyond git being
  available, consistent with docker/podman being a host-only concern for
  *that* image.

---

## 5. Verification

- **Fail-closed sha256** already exists uniformly (`_verify_digest`,
  bootstrap.py:117-140) — the manifest refactor (§3) must not weaken this;
  every `BINARY_MANIFEST` entry's asset tuple carries its own pin, resolved
  through the same `KNOWN_SHA256`/`GLUDD_BINARY_SHA256` precedence chain
  (bootstrap.py:91-115).
- **New `make verify-bundled-binaries` target**: given a built
  `dist/release/` (or an extracted release tarball), assert every
  `BINARY_MANIFEST` name that has an asset for the current `(os, arch)` is
  present under `binaries/` and its sha256 matches the pin — reuses
  `BinaryBootstrapper._verify_digest` against files read from disk rather
  than downloaded bytes.
- **Startup self-check**: extend the existing `sync_bundled_to_filestore()`
  boot hook (`daemon.py:1953-1960`) to log, per manifest binary, which tier it
  resolved from (`bundle` / `filestore-download` / `PATH` / `missing`) — the
  logging pattern already exists for the synced-list case (daemon.py:1959-1960),
  just needs to run across the full manifest (including `rg`/`node`, not only
  `KNOWN_VERSIONS`) and report the miss case too.

---

## 6. Classification summary (for the doc's own bookkeeping)

- **BUNDLED (target state)**: opentofu, openbao, osquery, ripgrep, node,
  codebase-memory-mcp — single-URL/sha256 manifest model (§3).
- **SPECIAL**: playwright/chromium — its own cache-directory model (§3), not
  a bare binary download.
- **HOST-PREREQUISITE (cannot bundle, document only)**: docker/podman
  (container isolation), git (ubiquitous — bundling is a possible future
  hardening but not attempted today).
- **PYTHON-RUNTIME-PROVIDED (already inside the pyinstaller payload as a
  dependency, not a separate binary)**: ansible-core's executor API (the path
  `ansible.runner`/`core_runner` drive; `gludd.spec:46-50` excludes only
  `ansible.cli`). **Caveat**: the `ansible-playbook` *CLI binary* is
  nonetheless invoked at product runtime by `routers/stream.py:175`
  (`/admin/stream/dispatch`) and `ansible-galaxy` by `ansible/galaxy.py:152` —
  those two remain host-PATH dependencies until migrated to the executor API
  or the resolver; do not treat "ansible is bundled" as fully true.
- **DEV/CI-ONLY (out of scope for the shipped redistributable)**: opa,
  conftest (policy test tooling, `tests/unit/test_opa_policies.py:27-45`),
  node-for-hook-tests (`tests/unit/_hook_fixtures.py`), uv (self-update
  dependency manager, `dependency/manager.py:79-80`).
- **UNRELATED, DO NOT CONFLATE**: `.gitmodules` (llama.cpp, openbao, opentofu,
  osquery, codebase-memory-mcp, mattpocock/skills source repos, tracked at
  branch HEAD) is a *source-vendoring* mechanism for reference/build-from-source,
  entirely separate from the binary-manifest download-and-verify mechanism
  described here — `tests/unit/test_submodule_management.py` covers it and it
  needs no changes for this design.

---

## 7. Test plan

1. **Manifest-completeness test** — every tool name reachable from a
   `subprocess`/`create_subprocess_exec`/`shutil.which(` call site in `src/`
   (enumerated in §1's table) either has a `BINARY_MANIFEST` entry or is
   listed in an explicit `HOST_PREREQUISITE`/`PYTHON_RUNTIME_PROVIDED` set —
   fails the test (not just warns) if a new shell-out is added without either.
2. **Resolver-prefers-bundle test** — `BinaryPathResolver.resolve("opentofu")`
   returns the bundled path when `get_bundled_binary_path` has one, even if
   `shutil.which` would also find one on PATH (bundle wins, never PATH, when
   both exist) — extends the existing `test_binary_paths.py:82-94` prefers-tofu
   pattern with a bundle-vs-PATH precedence case.
3. **`_MEIPASS` resolution test** — with `sys._MEIPASS` monkeypatched to a
   tmp dir containing a fake `binaries/rg`, `_find_dist_bundled_dir()` returns
   that dir before falling through to `os.getcwd()`.
4. **Built-artifact smoke test** — extract a real `dist/release/*.tar.gz`,
   set `PATH=""`, and run `<extracted>/binaries/tofu version`,
   `<extracted>/binaries/rg --version`, `<extracted>/binaries/osqueryi --version`
   directly — proves the bundle is independently executable, not just
   present. Gate this in the `release` job (build.yml:686-767) after assets
   are staged, before `Verify release has assets` (build.yml:749).
5. **`make verify-bundled-binaries`** (§5) run against every platform's built
   artifact in CI, non-blocking initially (mirrors the `continue-on-error`
   posture already used for windows/termux, build.yml:503, 548) until proven
   stable, then promoted to blocking.
