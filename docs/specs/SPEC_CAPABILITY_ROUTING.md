# SPEC: Capability-Based Model Dispatch Routing

**Spec ID:** SPEC_CAPABILITY_ROUTING
**Status:** draft
**Created:** 2026-08-02
**Author:** Session 52 — architectural design
**Depends on:** `ModelRouter` (src/general_ludd/models/router.py),
                `DynamicDispatcher` (src/general_ludd/dispatch/dynamic_dispatcher.py),
                `ModelRoutingConfig` (src/general_ludd/config/model_routing.py),
                `resolve_collections_paths` (src/general_ludd/ansible/paths.py),
                `config/model_routing.yml`

---

## 1. Problem Statement

Today model routing is fully operator-defined in `config/model_routing.yml`:
roles, quality tiers, latency tiers, and patterns are mapped to model profile IDs
by a human. When a new collection ships — `chemistry`, `electronics`, `physics` —
its model-to-capability mapping must be added to the config file manually, or the
collection's roles fall through to `default_profile`. This is fragile: the
operator must know which capabilities each collection's roles need, and core has
no automatic discovery of capabilities shipped by collections.

**The user asked:** design a capability-based routing system where collections
declare their capabilities, core discovers them at startup, and a `POST
/api/dispatch` + `gludd dispatch` CLI route work to the best model for a named
capability without any operator config changes.

---

## 2. Existing Architecture (What We Build On)

### 2.1 Collection Layout

Each collection lives under:
```text
collections/ansible_collections/general_ludd/<name>/
  galaxy.yml          ← ansible-galaxy metadata (namespace, name, version, tags, dependencies)
  roles/
    <role_name>/tasks/main.yml
  plugins/modules/<name>.py   (optional)
```

27 collections exist today (`ai_ml`, `agent`, `chemistry`, `physics`, `language`,
`security`, `business`, `governance`, `azure`, `networking`, `materials`,
`forensics`, `xml`, `radio`, `travel`, `os_expert`, `web`, `web_server`,
`chat`, `operations`, `infrastructure`, `behavioral`, `binary_re`, `e2e_test_gen`,
`formal`, `git_release`, `electronics`).

### 2.2 Collection Discovery (Startup)

`resolve_collections_paths()` (ansible/paths.py) returns the 3-tier ordered list:
1. **project** — `<project_root>/.gludd/collections/`
2. **user** — `~/.config/gludd/collections/`
3. **bundled** — `<install_root>/collections/`

The daemon calls this at startup (daemon.py:1188), publishes resolved paths as
`app.state._collections_paths`, and can rebind on project switch (daemon.py:1262).

### 2.3 Model Routing (Current)

`config/model_routing.yml` → `ModelRoutingConfig` (pydantic) → `ModelRouter`:
- `role_routing`: role name → model profile ID
- `quality_routing`: quality tier → model profile ID
- `latency_routing`: latency tier → model profile ID
- `pattern_routing`: task pattern → role name (indirect)
- `default_profile`: fallback
- `fallback_chain`: ordered list on API error

`ModelRouter.resolve_role(role_name)` → model profile ID, falling through to
`default_profile` when the role is unmapped.

### 2.4 Dynamic Dispatcher

`DynamicDispatcher.dispatch(ToolCall)` routes by `kind` (`role`, `collection`,
`mcp`, `skill`) to injected handlers. The `collection` kind is currently wired
to the ansible runner via `make_collection_handler()`.

### 2.5 Agent Registry

`AgentRegistry` (agents/registry.py) holds per-agent configs including
`allowed_subagents`, `can_edit`, `can_bash`, etc. Used by `AgentDispatcher` for
permission gating.

---

## 3. Design

### 3.1 Capability Declaration Format

Each release collection declares capabilities in the Gludd-owned extension
file while keeping Galaxy's manifest schema clean:

**Option A — `capabilities.yml` at collection root (canonical):**
```yaml
# collections/ansible_collections/general_ludd/chemistry/capabilities.yml
model_capabilities:
  - name: chemistry           # capability name — the key clients ask for
    description: >
      Chemical identity resolution, reaction analysis, stoichiometry,
      hazard review, cheminformatics, spectroscopy, and electrochemistry.
    roles:                    # which roles within this collection serve this capability
      - chemistry_router
      - identity_resolve
      - reaction_analyze
      - stoichiometry
      - hazard_review
      - cheminformatics
      - spectra_analyze
      - electrochemistry
      - thermo_kinetics
      - quantum_workflow
      - molecular_simulation
      - property_lookup
      - analytical_validate
      - protocol_draft
      - process_scaleup
      - inventory_check
      - chemistry_research
      - chemistry_refresh
      - chemistry_promote
      - tool_discover
    quality_class: high       # optional — high | medium | low
    latency_class: fast       # optional — fast | normal
    model_profile_id: null    # optional — explicit override; null = use router default
    aliases:                  # optional
      - chem
      - chemical-analysis
```

Inline declarations in `galaxy.yml` remain a read-only compatibility path for
older project collections, but release artifacts must not use it: Galaxy warns
on extension keys outside its published schema. `capabilities.yml` takes
precedence when both exist, so a project-local `.gludd/collections/` override
can replace capabilities without editing the bundled manifest.

#### 2026-08-20 language collection metadata incident

The beta4 Galaxy build accepted the language collection but warned that
`model_capabilities` and `role_capabilities` were unknown manifest keys. The
declarations now live unchanged in `capabilities.yml`, and discovery merges
only those two named extension fields with standard Galaxy metadata. This is a
fail-closed boundary: arbitrary extension-file fields cannot replace a
collection's namespace, name, version, dependencies, or signing metadata.

The [April 4, 2022 Ansible collection publishing report](https://github.com/ansible/ansible/issues/77460)
is durable practitioner evidence that Automation Hub validates collection
metadata by its specified file and schema, even when a local collection build
appears usable. Gludd consequently owns capability metadata in a distinct file
instead of relying on a tolerant parser or suppressing the Galaxy warning.

The migration is ZDD-safe because the scanner retains legacy inline reads,
prefers the sidecar atomically when present, and rebuilds the in-memory registry
before it is swapped into a daemon. Rollback removes the sidecar and restores
the inline fields; no running job or installed collection is mutated. Scans
remain bounded to one small file per discovered collection and create no
processes, clients, or temporary artifacts.

**Field reference:**

| Field | Required | Type | Description |
|---|---|---|---|
| `name` | **yes** | string | Canonical capability name. Must be unique across all collections. |
| `description` | no | string | Human-readable description (displayed in `gludd dispatch list`). |
| `roles` | **yes** | list[str] | Role names within this collection that serve the capability. Used for tool mapping. |
| `quality_class` | no | enum | `high` / `medium` / `low`. Routes through `quality_routing` in model_routing.yml. |
| `latency_class` | no | enum | `fast` / `normal`. Routes through `latency_routing` in model_routing.yml. |
| `model_profile_id` | no | string | Explicit model profile override. Bypasses role/quality/latency resolution. |
| `aliases` | no | list[str] | Alternative names clients can use. |

**Conflict rule (same capability name in two collections):** last-wins per
collection path precedence (project > user > bundled). A warning is logged.
If the operator wants both, they define distinct capability names.

### 3.2 Discovery Mechanism

At startup (and on hot-reload), core walks every tier in the resolved
collections path:

```text
for each tier in resolve_collections_paths():
    ns_dir = tier.path / "ansible_collections" / "general_ludd"
    for collection_dir in ns_dir.iterdir():
        if not collection_dir.is_dir(): continue
        capabilities_yml = collection_dir / "capabilities.yml"
        galaxy_yml = collection_dir / "galaxy.yml"
        caps = read_capabilities_yml if exists else read_capabilities_from_galaxy_yml
        for cap in caps:
            registry[cap.name] = CapabilityEntry(
                name=cap.name,
                collection=collection_dir.name,
                roles=cap.roles,
                quality_class=cap.quality_class,
                latency_class=cap.latency_class,
                model_profile_id=cap.model_profile_id,
                aliases=cap.aliases,
                tier=entry.source,
            )
```

**Hot-reload integration:** `HotReloader.reload_collection_paths()` (already
exists for project-switch) triggers a capability registry rebuild. Collections
added/removed while the daemon is running are picked up without restart.

**Validation (non-fatal):** duplicate capability names → warning. Capability
with no roles → warning (still registered, but `adds_no_tools: true`).
`quality_class`/`latency_class` values not in `model_routing.yml` → warning
(uses `default_profile` fallback, same as unmapped roles today).

### 3.3 Capability Registry Data Model

```python
@dataclass(slots=True)
class CapabilityEntry:
    name: str                     # canonical name
    collection: str               # collection name (e.g. "chemistry")
    roles: list[str]              # role FQCNs serving this capability
    quality_class: str | None     # high | medium | low | None
    latency_class: str | None     # fast | normal | None
    model_profile_id: str | None  # explicit override
    aliases: list[str]            # alternative names
    tier: str                     # project | user | bundled

class CapabilityRegistry:
    _by_name: dict[str, CapabilityEntry]
    _by_alias: dict[str, str]     # alias → canonical name
    _by_collection: dict[str, list[str]]  # collection → capability names

    def find(self, name: str) -> CapabilityEntry | None: ...
    def resolve_to_profile(
        self, name: str, router: ModelRouter
    ) -> str | None: ...                    # capability → model_profile_id
    def list_all(self) -> list[str]: ...    # all capability names
    def tools_for(self, name: str) -> list[str]: ...  # role FQCNs
```

### 3.4 Matching Algorithm

`CapabilityRegistry.resolve_to_profile(capability_name, router)` → model profile ID:

```text
1. Lookup by canonical name in _by_name.
   If not found, try _by_alias (aliases map).
   If not found, return None → fallback to router.default_profile_id.

2. If entry.model_profile_id is set (explicit override):
   → return entry.model_profile_id immediately.

3. If entry.quality_class is set AND router._quality_map has a match:
   → return router._quality_map[entry.quality_class].

4. If entry.latency_class is set AND router._latency_map has a match:
   → return router._latency_map[entry.latency_class].

5. Try role-based resolution: for each role in entry.roles:
   result = router.resolve_role(f"{entry.collection}:{role}")
   if result → return result.

6. Fall through to router.default_profile_id.
```

**Rationale for step order:** explicit override trumps all (operator control).
Quality/latency routing is collection-declared intent. Role-based resolution is
the existing mechanism (collection roles are in the `role_routing` table).
Default is the safety net.

**Ambiguity (same capability, many quality tiers across collections):** not
possible — capability names are unique (last-wins per tier precedence). A
capability is a unique key. If two collections both want "security", they use
distinct names ("security-sast", "security-secrets").

### 3.5 Fallback Behavior

| Scenario | Behavior |
|---|---|
| Capability not in registry | Return `default_profile` from `model_routing.yml`. Log `WARNING: capability "X" unknown — using default_profile "Y"`. |
| Capability found, no profile resolved (no quality/latency match, roles unmapped) | Return `default_profile`. Log `INFO: capability "X" has no routing hints — using default_profile "Y"`. |
| Model call fails (API error, timeout) | Walk `fallback_chain` from `model_routing.yml` exactly as today. The capability only affects initial routing, not error recovery. |
| Collection declares capability but is disabled/broken | Skip at discovery time (non-fatal). Capability won't appear in registry → falls through to `default_profile`. |
| Collection declares capability with roles that don't exist on disk | Warning at discovery. Roles list is treated as advisory — routing still works (step 5 just produces no hits). |

**No capability-aware fallback chain (out of scope for v1):** the `fallback_chain`
is global. Per-capability fallback chains would require a separate config surface
and are deferred.

### 3.6 API

#### `POST /api/dispatch`

Request:
```json
{
  "capability": "translation",
  "payload": {"text": "hello", "target_language": "es"},
  "quality": "medium",
  "model_profile_override": null
}
```

| Field | Required | Description |
|---|---|---|
| `capability` | **yes** | Capability name (or alias). |
| `payload` | **yes** | Arbitrary dict — forwarded to the dispatched role/module. |
| `quality` | no | Override quality class (`high`/`medium`/`low`). Overrides the capability's declared `quality_class`. |
| `model_profile_override` | no | Bypass all routing: use this profile ID directly. |

Response:
```json
{
  "ok": true,
  "capability": "translation",
  "resolved_profile": "deepseek_coder",
  "resolution_path": ["capability_registry", "quality_routing"],
  "collection": "language",
  "tool_calls": [
    {"role": "general_ludd.language.translate_text", "args": {...}}
  ],
  "output": "...",
  "duration_ms": 1234
}
```

Error:
```json
{
  "ok": false,
  "capability": "unknown_cap",
  "error": "unknown_capability",
  "detail": "Capability 'unknown_cap' not found in registry. 42 capabilities available. Use GET /api/capabilities to list."
}
```

**Wiring:** A new FastAPI route `POST /api/dispatch` added to daemon.py. It
calls `capability_registry.find()` → `resolve_to_profile()` → `model_gateway.call_model()`
with the resolved profile. If the resolved capability has roles, the handler
evaluates them via the ansible runner (exactly as `collection` kind dispatches
today).

#### `GET /api/capabilities`

Returns the full capability registry snapshot:
```json
{
  "capabilities": [
    {"name": "chemistry", "aliases": ["chem"], "collection": "chemistry", "roles_count": 20, "quality_class": "high"},
    {"name": "translation", "aliases": ["i18n", "l10n"], "collection": "language", "roles_count": 3, "quality_class": "medium"}
  ],
  "count": 42,
  "collections_scanned": 27,
  "collections_with_capabilities": 15
}
```

#### `POST /api/capabilities/reload`

Forces a hot-reload re-scan of all collections paths. Returns the same as `GET
/api/capabilities`. Used by `make reload-capabilities` and the TUI.

### 3.7 CLI

```bash
# Dispatch a capability request
gludd dispatch translation --text "hello" --target-language es
gludd dispatch chemistry --smiles "CCO" --query "identity"
gludd dispatch security-audit --path ./src/

# With quality override
gludd dispatch code-review --quality high --diff "$(make git-diff)"

# With explicit model override
gludd dispatch translation --text "hello" --model qwen_coder

# List all registered capabilities
gludd dispatch list
# Output:
#   chemistry            (chem, chemical-analysis)    chemistry     high     20 roles
#   translation          (i18n, l10n)                 language      medium    3 roles
#   ...

# Show details for one capability
gludd dispatch show chemistry
# Output:
#   Capability: chemistry
#   Aliases: chem, chemical-analysis
#   Collection: chemistry
#   Quality class: high
#   Latency class: fast
#   Resolved model profile: deepseek_coder (via quality_routing)
#   Roles (20):
#     general_ludd.chemistry.chemistry_router
#     general_ludd.chemistry.identity_resolve
#     ...

# Reload capability registry from disk
gludd dispatch reload
```

**Implementation:** A new `CLICommand` subclass (or extension of the existing
`gludd` CLI) that hits `POST /api/dispatch` and `GET /api/capabilities`.
No new daemon logic needed — the CLI is a thin HTTP client.

### 3.8 How Collections Register Without Core Changes

The entire mechanism is **zero-configuration for the operator**:

1. **Collection author** adds `capabilities.yml` to their collection root and
   leaves `galaxy.yml` within Ansible's published schema.

2. **On daemon startup** (or `POST /api/capabilities/reload`), the
   `CapabilityRegistryScanner` walks every tier in the resolved collections
   path, reads all `capabilities.yml`/`galaxy.yml`, and builds the registry
   in-memory.

3. **No restart needed** for collections added to a live daemon:
   `make reload-capabilities` → `POST /api/capabilities/reload` → re-scan.

4. **No core code changes** when a new collection ships. The collection is
   self-describing. A new `physics` collection with `capabilities.yml` will be
   picked up on the next scan without any edits to core Python code, YAML
   config, or `model_routing.yml`.

5. **Project-local overrides work naturally:** if a project drops
   `<project>/.gludd/collections/ansible_collections/general_ludd/chemistry/capabilities.yml`,
   it shadows the bundled copy at discovery time because project tier has
   highest precedence.

**What the operator still controls in `model_routing.yml`:**
- `default_profile` — fallback for unknown capabilities
- `fallback_chain` — error recovery for ALL dispatches
- `role_routing` / `quality_routing` / `latency_routing` — the routing tables
  that `resolve_to_profile()` consults in steps 3-5

**What the operator no longer needs to do:**
- Add every new collection's roles to `role_routing`
- Manually map capabilities to profiles (the capability declares its
  quality/latency class, and `model_routing.yml` maps those classes to profiles)

---

## 4. Implementation Plan

### Phase 1 — Data Model (src/general_ludd/capability/)

```text
src/general_ludd/capability/
  __init__.py
  registry.py        # CapabilityEntry, CapabilityRegistry
  scanner.py         # CapabilityRegistryScanner — reads collections, builds registry
  routing.py         # resolve_to_profile() — capability → profile resolution
```

### Phase 2 — Daemon Wiring (daemon.py)

- Startup: `CapabilityRegistryScanner.scan(app.state._collections_paths)` →
  `app.state.capability_registry`
- Hot-reload: on `reload_collection_paths()`, re-scan and publish updated registry
- New routes: `POST /api/dispatch`, `GET /api/capabilities`, `POST /api/capabilities/reload`
- `POST /api/dispatch` handler: resolve capability → resolve profile →
  call `model_gateway.call_model_by_role()` with the resolved profile

### Phase 3 — CLI (cli/)

- New subcommand group: `gludd dispatch {list|show|reload|<capability>}`
- Thin client over the daemon API

### Phase 4 — Collection Registration (26 collections, one-time)

- Add `capabilities.yml` to each bundled collection that has model-facing roles
- Not every collection needs one: `networking`, `infrastructure`, `operations`
  may have no model-facing roles

### Phase 5 — Config Migration

- `model_routing.yml` gains an optional `capability_routing_enabled: true`
  (default true when any `capabilities.yml` exists)
- Existing `role_routing` entries that map collection roles remain compatible
  (capability resolution checks them in step 5)

---

## 5. Non-Goals (Out of Scope for v1)

- **Per-capability fallback chains** — global `fallback_chain` only
- **Capability composition** (`chemistry AND physics`) — single capability per dispatch
- **Dynamic capability scoring** — routing is deterministic (explicit override >
  quality class > latency class > role mapping > default). No ML-based selection.
- **Collection dependencies for capabilities** — a collection can't declare
  "my capability needs collection X's capability." Each capability is self-contained.
- **Capability versioning** — if a collection upgrades and changes its
  capabilities, the new `capabilities.yml` replaces the old one on scan. No
  version-locked routing.
- **Streaming dispatch** (SSE) — `POST /api/dispatch` returns a complete
  response. Streaming is a separate feature.

---

## 6. Security Considerations

- **Capability names are user-controlled strings** — they flow into log messages
  and API responses. Sanitize to 128-char max, alphanumeric + hyphens only.
- **`model_profile_override` on `POST /api/dispatch`** bypasses all routing.
  Callers with the `dispatch` capability (in the capability lattice) are
  already trusted. No additional gating needed.
- **Collection `capabilities.yml` is read from disk** — same trust model as
  `galaxy.yml` today. A malicious collection on the filesystem can already
  execute arbitrary ansible tasks. Capability declarations add no new attack
  surface.
- **No secrets in `capabilities.yml`** — model profile IDs, quality tiers, and
  role names are already public (they're in `model_routing.yml`).

---

## 7. Observability

- **Metric:** `gludd_capability_dispatch_total{capability, profile, ok}` —
  counter emitted on every `POST /api/dispatch`
- **Metric:** `gludd_capability_resolution_ms{capability}` — histogram of
  registry lookup + profile resolution wall time
- **Log:** `INFO: capability "chemistry" resolved to profile "deepseek_coder"
  via quality_routing (collection=chemistry, tier=bundled)`
- **Log:** `WARNING: capability "unknown" not in registry — falling through to
  default_profile "deepseek_coder"`
- **Log:** `WARNING: duplicate capability "security" in collections
  "security" (bundled) and "infrastructure" (bundled) — "security" wins
  (later in scan order)`

---

## 8. Testing Strategy

| Level | Test file | What it validates |
|---|---|---|
| Unit | `tests/unit/test_capability_registry.py` | `CapabilityRegistry` add/find/alias/lookup; `resolve_to_profile()` all 6 resolution steps; duplicate handling |
| Unit | `tests/unit/test_capability_scanner.py` | Scanner reads `capabilities.yml` and `galaxy.yml` correctly; tier precedence; missing files are non-fatal |
| Unit | `tests/unit/test_capability_routing.py` | `resolve_to_profile()` integration with real `ModelRouter` from `config/model_routing.yml` |
| Integration | `tests/integration/test_capability_api.py` | `POST /api/dispatch` → resolves → calls model gateway (mock); `GET /api/capabilities` → returns registry snapshot |
| E2E | `tests/e2e/test_capability_cli.py` | `gludd dispatch list` → actual output; `gludd dispatch chemistry --help` → usage |

---

## 9. Acceptance Criteria

1. `POST /api/dispatch` with `{"capability": "chemistry", "payload": {"smiles": "CCO"}}`
   resolves to a model profile and returns a response.
2. `GET /api/capabilities` lists all capabilities discovered from bundled + user
   + project collections.
3. Adding a new `capabilities.yml` to a collection and running
   `POST /api/capabilities/reload` makes the new capability available without
   a daemon restart.
4. `gludd dispatch list` prints a table of capabilities with names, collections,
   quality tiers, and role counts.
5. An unknown capability `POST /api/dispatch {"capability": "nonexistent", ...}`
   returns `{"ok": false, "error": "unknown_capability"}` with a message
   listing available capabilities.
6. `make test-capability-routing` passes (dedicated target for capability
   subsystem tests).
7. No changes to `model_routing.yml` are required for capabilities that declare
   a `quality_class` or `latency_class` matching existing routing entries.
