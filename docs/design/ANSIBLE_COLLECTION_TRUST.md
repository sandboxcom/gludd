# Ansible Collection Trust Boundary — Project-Tier `general_ludd` Namespace Shadowing (CRITICAL RCE) + Isolation Dead-Config (Wave C style, 2026-07-10)

Status: **design-complete, not yet implemented.** Self-contained, line-anchored
spec against current `master`. Line numbers are current-tree at authoring
time — re-confirm with a Read before editing, they drift. Land as ONE batch
(paths.py + daemon.py + docs rewrite + cli_project_init are tightly coupled;
splitting risks a half-landed state where the filter exists but isn't wired,
or the docs still tell operators to do the vulnerable thing). Run the full
test plan below, then `make gate-async` before claiming closed.

Source: live audit against `src/general_ludd/ansible/paths.py`,
`src/general_ludd/daemon.py`, `src/general_ludd/ansible/core_runner.py`,
`src/general_ludd/ansible/isolation.py`, `src/general_ludd/ansible/runner.py`,
`docs/design/PROJECT_COLLECTIONS.md`, and the two test files named below —
every citation in this doc was re-Read at authoring time.

---

## CRIT-1 — Project-tier `.gludd/collections/` shadows bundled `general_ludd.*` by FQCN → in-process RCE (CRITICAL, live today)

**Files:** `ansible/paths.py`, `daemon.py`, `config/project_dir.py`,
`event_loop/loop.py`, `ansible/core_runner.py`, `docs/design/PROJECT_COLLECTIONS.md`,
`playbooks/*.yml`

### The vulnerability

gludd resolves ansible's collections/roles search path as a 3-tier list,
project-first (`ansible/paths.py:67-98 resolve_collections_paths`):

1. `project` — `<project_root>/.gludd/collections/` (`paths.py:60-64
   _project_collections_root`), precedence **0** (highest)
2. `user` — `${XDG_CONFIG_HOME:-~/.config}/gludd/collections/`
   (`paths.py:52-57`), precedence 1
3. `bundled` — `<install_root>/collections/`, precedence 2 (last)

`to_ansible_env()` (`paths.py:101-128`) puts the tiers on
`ANSIBLE_COLLECTIONS_PATH` / `ANSIBLE_ROLES_PATH` in that same project-first
order. This is standard ansible-core FQCN resolution: the first tier whose
`ansible_collections/<ns>/<coll>/` directory contains the resource wins,
full stop — there is no per-namespace ACL, ansible only understands whole
directory entries in the search path.

`project_root` is **untrusted, cloned-repo-derived** content:

- `config/project_dir.py:20-45 find_project_gludd_dir()` walks UP from `cwd`
  (or `GLUDD_PROJECT_DIR`) looking for the first ancestor containing a
  `.gludd/` directory — i.e. it walks into whatever repo the daemon happens
  to be running against.
- `daemon.py:178` calls `find_project_gludd_dir()` into
  `cfg["project_gludd_dir"]` at startup.
- `daemon.py:1008-1012` derives `_initial_project_root =
  str(Path(_proj_gludd).parent)` and feeds it into
  `resolve_collections_paths()` / `to_ansible_env()` at
  `daemon.py:1003-1013`.
- `event_loop/loop.py:892-925 _resolve_project_root_for_collections` +
  `_rebuild_ansible_env_for_project` (called from the project-switch path at
  `loop.py:885-889`) **re-resolve the same 3-tier path every time the active
  project changes** — so a newly-onboarded/cloned repo's `.gludd/collections/`
  is picked up live, no restart required.

gludd's own bundled playbooks invoke bundled roles by bare FQCN, e.g.
`playbooks/dependency_update.yml:22-23`:

```yaml
- name: Run lint and check after dependency update
  ansible.builtin.include_role:
    name: general_ludd.agent.lint_and_check
```

Because the project tier is searched FIRST, a cloned repo that ships
`.gludd/collections/ansible_collections/general_ludd/agent/roles/lint_and_check/`
**silently replaces** the trusted bundled role for every playbook run against
that project — including gludd's own internal maintenance playbooks, which
were never meant to be project-overridable. There is no operator opt-in step:
onboarding the repo (cloning it, pointing the daemon's cwd/`GLUDD_PROJECT_DIR`
at it) is sufficient.

Isolation is OFF by default (see CRIT-1's sibling bug below), so the shadowed
role's tasks — arbitrary `ansible.builtin.command`/`shell`/`copy`/`template`
— execute **in-process on the gludd host**, not in a container. This is a
full RCE: clone a repo, get it onboarded as a gludd project, ship a
`general_ludd.agent.lint_and_check` (or any other bundled role FQCN a real
playbook calls) that does whatever the attacker wants.

**Secondary vector — SSTI via non-sandboxed Templar.** The shadowed role's
own `templates/*.j2` are rendered by ansible-core's task-level templating
during `PlaybookExecutor.run()`, which uses the full, non-sandboxed
`ansible.template.Templar` — the same class `core_runner.py:76` imports and
`core_runner.py:92-97 _get_templar` wraps for `render_template()`. This is
the FULL lookup/plugin surface (`{{ lookup('pipe','id') }}` runs a shell).
Contrast this with the untrusted-input discipline the codebase already
knows it needs: `routers/ansible.py:38-49` explicitly refuses to expose
`AnsibleTemplater.render()` (trusted, full Templar) to the network-facing
`/admin/ansible/render` endpoint, using `render_sandboxed()`
(`ansible/templating.py` — `jinja2.sandbox.SandboxedEnvironment` +
`StrictUndefined` + empty globals) instead specifically because "an
attacker-controlled template body… MUST NOT use the trusted full-Templar
path." A project-tier-shadowed role's Jinja templates get the SAME
untrusted-input treatment gludd already refuses to give network input,
except here it's not refused at all — it's the default playbook execution
path.

### Upstream compatibility note — ansible-core 2.19+

Ansible-core 2.19 inverted its template trust model: programmatically created
strings are untrusted by default and `Templar.template()` returns them
unchanged unless a trusted plugin explicitly applies
`ansible.template.trust_as_template`. The
[2.19 porting guide](https://docs.ansible.com/projects/ansible-core/2.19/porting_guides/porting_guide_core_2.19.html#template-trust-model-inversion)
documents the security motivation and public API. The long-running Ansible
community discussions
[Core-2.19 templating changes](https://forum.ansible.com/t/core-2-19-templating-changes-preview-and-testing/40759)
and
[Data tagging playground](https://forum.ansible.com/t/core-2-19-and-data-tagging-playground/39909)
also record the migration impact and the final `trust_as_template` interface.

Gludd therefore tags only the documented trusted
`CoreAnsibleRunner.render_template()` input on ansible-core 2.19 and newer,
while retaining the legacy direct-string behavior for older supported
versions. The network-facing `render_sandboxed()` path never applies this
trust tag, preserving the fail-closed boundary described above.

### Proof (already in the tree, currently proves the WRONG thing)

`tests/unit/test_collection_paths.py:187-197
TestFindResource.test_find_resource_returns_project_override` materializes
`general_ludd.agent.gludd_facts` at BOTH the project tier and the bundled
tier and asserts **the project copy wins**:

```python
proj_col = project_root / ".gludd" / "collections"
_materialize_module(proj_col, "general_ludd", "agent", "gludd_facts")
_materialize_module(bundled_root, "general_ludd", "agent", "gludd_facts")
entries = resolve_collections_paths(project_root=project_root)
found = find_resource("general_ludd.agent.gludd_facts", entries)
assert found is not None
assert str(found).startswith(str(proj_col))
```

This test currently **encodes the vulnerability as a passing spec** — it is
not a synthetic third-party namespace, it uses `general_ludd.agent` verbatim,
the exact namespace gludd's own bundled playbooks call by FQCN. It must be
flipped (see Test plan).

`tests/integration/test_project_collection_precedence.py::test_project_shadows_bundled`
(lines 215-236) proves the same fact at the ansible-core execution layer, not
just path resolution: it spawns a real subprocess, sets
`ANSIBLE_COLLECTIONS_PATH` project-first, runs a real `PlaybookExecutor`
against a role FQCN present in both tiers, and asserts the runtime-stamped
`role_source` fact is `"project"`. It uses a synthetic `test_ns.proj`
namespace today (not `general_ludd`) — the fix's new test (see Test plan)
must run the SAME harness pattern against the `general_ludd` namespace
specifically and assert the opposite outcome.

`docs/design/PROJECT_COLLECTIONS.md` **documents the vulnerable behavior as
the intended, supported way to customize gludd** — its "Override rule"
section (lines 44-73) and BOTH worked examples (lines 113-158,
"add a pre-commit hook to the scaffold" via project-tier
`general_ludd.agent.project_init`; lines 210-283, "acme-internal shadowing
deploy_legacy_widget" via project-tier `general_ludd.agent.<role>`)
instruct operators to run
`gludd project init --namespace general_ludd --collection agent --force`
— i.e. deliberately create the exact untrusted-project-tier
`general_ludd.agent` shadow this finding says must never reach ansible-core.
This doc must be rewritten (see Fix step 2), not just the code.

---

## CRIT-1b — `isolation.yml` is loaded but never wired: containment is dead config (CRITICAL companion, live today)

**Files:** `daemon.py:261-266`, `daemon.py:1055-1058`, `ansible/core_runner.py:297-298`,
`ansible/isolation.py:42`, `ansible/runner.py:54-79`

CRIT-1's in-process blast radius is as bad as it is because isolation has
**zero runtime effect**, even when an operator has explicitly configured and
enabled it:

1. `daemon.py:261-266` reads `config/ansible/isolation.yml` and constructs
   `cfg["process_isolation"] = ProcessIsolationConfig(**pi_data)`:

   ```python
   iso_path = cdir / "ansible" / "isolation.yml"
   if iso_path.exists():
       with open(iso_path) as f:
           data = yaml.safe_load(f) or {}
       pi_data = data.get("process_isolation", {})
       cfg["process_isolation"] = ProcessIsolationConfig(**pi_data) if pi_data else None
   ```

2. `cfg["process_isolation"]` is **never read again.** The prod construction
   site, `daemon.py:1055-1058`:

   ```python
   runner = AnsibleRunnerAdapter(
       default_env=dict(_ansible_env) if _ansible_env else None,
       project_root=_initial_project_root,
   )
   ```

   passes only `default_env` + `project_root`. `AnsibleRunnerAdapter.__init__`
   (`ansible/runner.py:55-79`) has an `isolation_config:
   ProcessIsolationConfig | None = None` parameter and threads it straight to
   `CoreAnsibleRunner(process_isolation=isolation_config, ...)`
   (`runner.py:76-79`) — but the daemon never passes it, so it defaults to
   `None`.

3. `core_runner.py:297-298`:

   ```python
   iso = self._process_isolation
   if iso is not None and getattr(iso, "enabled", False):
       return self._execute_with_runner(...)
   ```

   `iso` is always `None` in production → this branch never fires →
   `run_playbook` always falls through to `_execute_with_core`, the
   in-process `PlaybookExecutor` path. The containerized
   `_execute_with_runner` (podman/bwrap via `ansible-runner`) is **dead code
   in the running daemon**, regardless of what `isolation.yml` says.

`ProcessIsolationConfig.enabled` defaults to `False`
(`ansible/isolation.py:42`), so absent this bug the global default is already
"off unless an operator opts in" — that default is fine on its own; the bug
is that opting IN does nothing.

Combined with CRIT-1, a project-tier-shadowed role does not merely run with
weaker isolation than an operator configured — it runs with **no isolation
at all, unconditionally**, no matter what `isolation.yml` says.

---

## Fix — namespace-scoped filter + isolation wiring (preserves the legitimate override use case)

Do **not** blindly reverse the whole 3-tier precedence — that would demote
the trusted USER tier too, and is unnecessary for the vast majority of
project-tier content (any non-`general_ludd` namespace *cannot* collide with
a bundled FQCN, so it needs no special handling). The vulnerability is
entirely about the `general_ludd` namespace specifically.

**Trust model:**
- **project tier** = UNTRUSTED (derived from a cloned repo the daemon does
  not control the contents of)
- **user tier** (`~/.config/gludd/collections`) = TRUSTED (operator's own
  machine)
- **bundled tier** = TRUSTED (gludd's own install)

### Step 1 — project tier may never present a `general_ludd` namespace to ansible-core

`ANSIBLE_COLLECTIONS_PATH` entries are whole directories, first-match-wins,
with no per-namespace ACL — so the fix must happen BEFORE the project path
ever reaches the env, by filtering what's in the directory ansible actually
sees.

Add to `ansible/paths.py`:

```python
def _filtered_project_collections_root(project_root: Path) -> Path:
    """Return a project collections root safe to hand to ansible-core.

    If the project's ansible_collections/ tree contains a `general_ludd`
    namespace, that namespace is EXCLUDED (never symlinked into the staging
    root) and a security warning event is emitted. Every other namespace is
    symlinked through unchanged. When no `general_ludd` namespace is present,
    returns the raw project path directly (common case, zero extra I/O).
    """
```

Behavior:
- Resolve `<project_root>/.gludd/collections/ansible_collections/`.
- If no `general_ludd/` child exists → return the raw project collections
  root unchanged (today's behavior, zero overhead — this is the common
  case for the vast majority of projects that don't collide with bundled
  namespaces).
- If `general_ludd/` DOES exist → build a gludd-owned staging root
  (**not** under the project repo — a directory the untrusted repo cannot
  write into after the fact) at `<gludd_state_dir>/collections_filtered/<hash(project_root)>/ansible_collections/`,
  rebuilt on every call (stale entries from a prior resolve must not
  linger): for every namespace directory under the project's
  `ansible_collections/` OTHER than `general_ludd`, symlink
  `<ns>/<coll>` into the staging root's `<ns>/<coll>`. The `general_ludd`
  namespace directory is skipped entirely — never symlinked, never visible
  to ansible-core under this root.
- Emit a loud warning event `project_collection_general_ludd_namespace_refused`
  (via the existing events bus the daemon already publishes
  `PlaybookRegisteredEvent` on — `ansible/runner.py:21`) naming the project
  and the refused path, so an operator sees this in logs/observability
  rather than silently losing functionality.
- Return the staging root (net-new non-`general_ludd` project roles/modules
  keep resolving and shadowing exactly as before — only the `general_ludd`
  collision is refused).

`resolve_collections_paths()` calls `_filtered_project_collections_root`
instead of `_project_collections_root` directly when building the `project`
tier entry; the tier's `precedence` position (0, ahead of user/bundled) is
UNCHANGED — only the CONTENT of what that tier can present to ansible-core
changes.

`<gludd_state_dir>` — reuse the existing gludd-owned-data convention already
in the tree: `filestore/store.py:26` uses
`~/.local/share/general-ludd/filestore` (via `os.path.expanduser`, no env
indirection) as the main gludd-owned data root, distinct from the
*user config overlay* at `~/.config/gludd/fs` (`store.py:31`) which
`store.py:38-42` explicitly excludes from serving trust-sensitive content
(the "binaries/ must NEVER resolve through the overlay" comment is the same
shape of trust-boundary concern as this finding). No `XDG_STATE_HOME`/
`gludd_home()`/`state_dir()` helper exists in this repo today — do not invent
a new env var; place the staging root at
`~/.local/share/general-ludd/collections_filtered/<hash>/ansible_collections/`,
matching `store.py:26`'s sibling convention under the same `general-ludd`
data root.

### Step 2 — operator overrides of `general_ludd.*` move to the USER tier

The *legitimate* use case `docs/design/PROJECT_COLLECTIONS.md` describes
(shadow a bundled role for one project) still works — it just has to live
somewhere trusted. Since FQCN resolution has no per-project scoping at the
user tier, and gludd only runs one project's playbooks against one daemon
process' `ANSIBLE_COLLECTIONS_PATH` at a time, an operator who wants a
per-project `general_ludd.*` override places it at
`~/.config/gludd/collections/ansible_collections/general_ludd/agent/` same
as today's user tier — it shadows bundled exactly as before (user tier is
unaffected by Step 1's filter, which only touches the project tier).

Rewrite `docs/design/PROJECT_COLLECTIONS.md`:
- **Override rule** (lines 44-73): change "project-local collection MUST...
  Live at `<project>/.gludd/collections/...`" to target
  `~/.config/gludd/collections/...` for the `general_ludd` namespace
  specifically; keep the project tier guidance for any OTHER namespace
  (net-new project-only roles are unaffected by this finding).
- **Both worked examples** (lines 113-158 pre-commit-config scaffold,
  lines 210-283 `deploy_legacy_widget`): change
  `gludd project init --namespace general_ludd --collection agent --force`
  run FROM the project directory to instead target the user tier (new CLI
  behavior, see below), and update the resulting tree diagrams accordingly.
- Add a **Threat Model** subsection stating plainly: the project tier is
  untrusted (cloned-repo-derived) and is filtered to exclude the
  `general_ludd` namespace; the user tier is where trusted general_ludd
  overrides live; net-new non-`general_ludd` project roles are the
  project-tier-safe pattern.
- Update the "Enforcement" section (lines 285-303) to reference the new
  unit + integration tests below.

`gludd project init --namespace general_ludd [--collection <name>]` is
implemented in `src/general_ludd/cli_project_init.py`:
`_cmd_project_init` (lines 24-53) always builds `extra_vars["project_dir"] =
str(project_dir)` and hands it to `_invoke_role` (lines 56-64), which
constructs `AnsibleRunnerAdapter(project_root=extra_vars.get("project_dir"))`
and runs `project_init.yml` — there is currently NO namespace-conditional
branch; every invocation scaffolds under
`<project_dir>/.gludd/collections/ansible_collections/<namespace>/<collection>/`
regardless of namespace value. **Fix:** in `_cmd_project_init`, before
building `extra_vars`, check `namespace == "general_ludd"`; if so, redirect
the scaffold target to
`~/.config/gludd/collections/ansible_collections/general_ludd/<collection>/`
(pass an explicit destination override into `extra_vars` for the
`project_init` role to honor, rather than relying on `project_root`, since
`project_root` also drives the adapter's OWN collections-path resolution and
must still point at the real project for unrelated purposes) — refuse (or
warn + redirect, operator's choice of severity) rather than silently
scaffolding into a location Step 1 will subsequently filter out of ansible's
view. `add_project_init_subparser` (lines 98-127) needs no change — `
--namespace` is already a free-text required arg.

### Step 3 — wire + default isolation

`daemon.py:1055-1058` MUST pass `isolation_config` to `AnsibleRunnerAdapter`.
Compute:

```python
effective_isolation = (
    cfg["process_isolation"]  # explicit operator config always wins
    if cfg.get("process_isolation") is not None
    else ProcessIsolationConfig(
        enabled=any(e.source == "project" for e in _collections_paths)
    )
)
runner = AnsibleRunnerAdapter(
    default_env=dict(_ansible_env) if _ansible_env else None,
    project_root=_initial_project_root,
    isolation_config=effective_isolation,
)
```

i.e.: if the operator configured `isolation.yml` at all (enabled true OR
explicitly false), that wins outright. Otherwise, isolation defaults ON
whenever project-tier content is actually in play (a project has a
`.gludd/collections/` directory contributing to the resolved path) — the
exact condition under which CRIT-1's untrusted content can reach ansible-core
at all. Do **not** change the global pydantic default of `enabled=False` on
`ProcessIsolationConfig` itself (`isolation.py:42`) — unit tests pin that
default; this is a daemon-level computed override, not a schema-level
change. Re-run `effective_isolation` recompute whenever
`_rebuild_ansible_env_for_project` re-resolves the path on project switch
(`event_loop/loop.py:927+`), so the isolation decision tracks which project
is currently active, not just the one active at daemon startup.

Combined with the existing fail-closed guard already in the tree
(`core_runner.py:591-600` — `_execute_with_runner` returns a hard `failed`
`AnsibleResult` when `ansible_runner` is not installed, rather than silently
falling back to `_execute_with_core`), a project-tier run now gets real
containment or a hard, visible failure — never silent in-process execution.

### Step 4 — Templar (secondary, lower priority)

Route project-tier-resolved task graphs (i.e. any playbook run where the
resolved collections path includes a `project` tier entry) through the same
untrusted-templating discipline `routers/ansible.py:38-49` /
`ansible/templating.py` already implements for the network-facing render
endpoint (`SandboxedEnvironment` + `StrictUndefined`, no lookup/plugin
surface), rather than the raw `Templar` ansible-core's `PlaybookExecutor`
uses internally by default. This is a deeper ansible-core integration change
(task-level templating is normally not interceptable without a custom
strategy/callback plugin) — flag as a follow-up design, not blocking Steps
1-3, which already remove `general_ludd` FQCN collision (the primary vector)
and put non-colliding project-tier execution behind real process isolation
(which also contains SSTI's shell-out blast radius, since isolation wraps
the whole task execution, not just the module call).

---

## Test plan

1. **Flip the vulnerable spec.** `tests/unit/test_collection_paths.py:187-197
   test_find_resource_returns_project_override` currently asserts project
   wins for `general_ludd.agent.gludd_facts`. Add a NEW test (do not just
   invert this one in place — keep a project-wins-for-non-general_ludd
   positive test too) asserting: for the `general_ludd` namespace
   specifically, `find_resource("general_ludd.agent.<role>", entries)`
   resolves to the BUNDLED copy even when a project-tier copy exists, and a
   `project_collection_general_ludd_namespace_refused` warning is emitted.

2. **New integration test** mirroring
   `tests/integration/test_project_collection_precedence.py`'s subprocess
   harness: materialize a malicious `general_ludd.agent.<role>` at the
   project tier (stamping `role_source: "PROJECT-MALICIOUS"`) and the real
   bundled `general_ludd.agent.<role>` (stamping `role_source: "bundled"`);
   assert the runtime-executed role is `bundled`, not
   `PROJECT-MALICIOUS`.

3. **Non-`general_ludd` namespace regression.** A net-new project-tier role
   under a synthetic namespace (e.g. `acme.internal.some_role`, no bundled
   collision possible) still resolves/shadows exactly as before — Step 1's
   filter must be a no-op for the common case.

4. **User-tier override regression.** A `general_ludd.*` override placed at
   `~/.config/gludd/collections/...` (the new supported location per Step 2)
   still shadows bundled — proves the legitimate use case survives the fix.

5. **Isolation wiring:**
   - project tier present in the resolved path (no explicit operator
     `isolation.yml`) → the `AnsibleRunnerAdapter` constructed by
     `daemon.py` has `isolation_config.enabled is True`.
   - explicit `isolation.yml` with `enabled: false` present → honored even
     with project-tier content in play (operator override wins).
   - regression test that `cfg["process_isolation"]` from `daemon.py:261-266`
     is actually threaded into the adapter construction at
     `daemon.py:1055-1058` (the exact dead-wiring this finding reports —
     assert the kwarg is passed, not just that the config loads).

6. **Fail-closed:** isolation enabled (via either path above) +
   `ansible-runner` package absent → `run_playbook` returns a hard failed
   `AnsibleResult`, never a silent fallback to `_execute_with_core`
   (pins the existing `core_runner.py:591-600` guard against regressing once
   the isolation path is actually reachable in prod).

7. **Docs enforcement parity.** `docs/design/PROJECT_COLLECTIONS.md`'s
   "Enforcement" section (currently lines 285-303) must list the new tests
   above alongside the existing resolver/integration/CLI test references,
   so the doc and the test suite stay in lockstep the way the existing
   section already claims they do.
