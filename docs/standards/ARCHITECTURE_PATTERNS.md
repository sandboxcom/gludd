# Gludd Architecture Patterns — Separation of Concerns

Status: **codified** — defines the four UI-architecture patterns applied to
gludd Ansible collections. Every role, module, and module_utils file must
satisfy the layer rules below. Violations are flagged in collection audits.

---

## Pattern 1: MVC (Model–View–Controller)

Traditional server-rendered separation. A Controller (role playbook) receives
input, calls Model logic, and hands structured output to a View.

| Layer | Gludd component | What it contains |
|-------|----------------|------------------|
| **Model** | `plugins/module_utils/contracts.py` | Pydantic models, dataclasses, enums, validation logic. Pure data — no display strings, no CLI output, no filesystem I/O. |
| **View** | `roles/<role>/templates/`, `ansible.builtin.debug` output, molecule `converge.yml` register-and-assert blocks | Renders structured model output for human or machine consumption. Never mutates model data. |
| **Controller** | `roles/<role>/tasks/main.yml` | Orchestrates: validates input, calls a module, routes the result to the correct view. Does NOT contain business logic or data transformations. |

### Rules (enforceable)

1. **Models never produce display strings.** A contract must not call
   `strftime`, `isoformat`, `json.dumps`, or Jinja in a model method.
   Serialization (`model_dump(mode="json")`) is the View's job — the Model
   returns objects; the View renders them.
2. **Controllers never contain business logic.** `tasks/main.yml` MAY call
   `assert` (input validation) and `debug` / `copy` (output routing). It MUST
   NOT transform data — that belongs in the module or module_utils.
3. **Views never call modules directly.** A View (template, debug msg, molecule
   converge) consumes output the Controller provided. It never invokes a
   `general_ludd.*` module itself.

### Example: travel/trip_planner (MVC-aligned)

```text
Model:   plugins/module_utils/contracts.py     → TripRequest, Itinerary, TimelineEntry
Controller: roles/trip_planner/tasks/main.yml  → validates input, calls trip_planner module, writes artifact
View:    tasks/main.yml `ansible.builtin.debug` → renders trip plan summary
```

---

## Pattern 2: MVVM (Model–View–ViewModel)

Used when a role transforms raw model data into display-ready shape before
rendering. The ViewModel (role `defaults/main.yml` + `vars/main.yml`) bridges
Model and View.

| Layer | Gludd component | What it contains |
|-------|----------------|------------------|
| **Model** | `plugins/module_utils/contracts.py` | Pydantic models, enums, validation. Identical to MVC Model. |
| **ViewModel** | `roles/<role>/defaults/main.yml`, `roles/<role>/vars/main.yml`, `meta/main.yml` | Transforms raw model data into display names, sort orders, format choices. e.g. `date_lengths: [full, long, medium, short]`, `plural_categories`, currency formatters. |
| **View** | `molecule/default/verify.yml` assertions, `roles/<role>/templates/` | Asserts that the ViewModel-transformed output matches expected display. Molecule `verify.yml` is the View counterpart — it proves the rendered output is correct. |

### Rules (enforceable)

1. **ViewModel files never duplicate contract data.** `vars/main.yml` must
   not re-declare enum values, field names, or validation thresholds already
   in `contracts.py`. The ViewModel ADDS display-specific metadata (labels,
   sort orders, format strings); it does not REDEFINE the model.
2. **ViewModel transforms are pure.** A Jinja filter in `defaults/main.yml`
   that transforms a model field is a ViewModel. A Python transformation in
   `module_utils/` that also touches the filesystem is a Controller — it
   belongs in the module, not the ViewModel.
3. **Molecule verify.yml assertions test the ViewModel+View.** They assert
   the rendered output (View) given transformed data (ViewModel), never raw
   model internals.

### Example: language/locale_format (MVVM-aligned)

```text
Model:     src/general_ludd/language/locale_data.py  → CLDR_FIRST_DAY_OF_WEEK, format_number, format_currency
ViewModel: roles/locale_format/vars/main.yml         → date_lengths, plural_categories, rtl_languages
           roles/locale_format/defaults/main.yml     → display defaults (locale, date_style)
View:      molecule verify.yml assertions            → assert rendered format matches expected
```

---

## Pattern 3: MVI (Model–View–Intent)

Unidirectional data flow. An Intent (role input vars) drives state changes
through the Model. The View renders the resulting state.

| Layer | Gludd component | What it contains |
|-------|----------------|------------------|
| **Model** | `plugins/module_utils/contracts.py` | Immutable-ish state: Pydantic models with validation. New Intent = new Model instance. |
| **View** | Module `RETURN` block, `ansible.builtin.debug` output, artifact JSON | Renders the model state resulting from the intent. Read-only — never mutates. |
| **Intent** | Role input vars (`origin`, `destinations`, `start_date`, `budget`, `interests`), module `argument_spec` | The user's intent: a set of named parameters that drive state change. One intent → one model output. |

### Rules (enforceable)

1. **Intents are self-contained input structs.** Every module's
   `argument_spec` must be a flat namespace of named parameters. A module
   accepting an opaque `config: dict` with no nested schema is an MVI
   violation — the Intent is untyped.
2. **Intents produce Model instances, not dicts.** A module receiving an
   Intent must construct a Pydantic model from it (e.g. `TripRequest`). If
   the module passes raw dicts directly to business logic, the Intent layer
   is missing.
3. **One Intent produces exactly one observable state change.** A module that
   returns `changed: false` but mutates internal state is an MVI violation.

### Example: travel/hotel_search (MVI-aligned)

```text
Intent: role input vars → destination, check_in, check_out, guests, rooms, min_stars, amenities
Model:  HotelSearch (Pydantic), HotelBooking (output)
View:   module exit_json → hotels, total_nights, search_params
```

---

## Pattern 4: MVP (Model–View–Presenter)

The Presenter mediates all Model ↔ View interaction. In Ansible, the role's
`tasks/main.yml` structured as a Presenter pattern: it calls the module
(Model), receives output, and routes it to the View.

| Layer | Gludd component | What it contains |
|-------|----------------|------------------|
| **Model** | `plugins/module_utils/contracts.py` + `plugins/modules/<name>.py` | Contracts + module logic. The module is part of the Model layer — it instantiates, validates, and returns contracts. |
| **View** | `roles/<role>/templates/`, `ansible.builtin.debug`, artifact JSON files | Displays presenter-routed output. Passive — never decides what to show. |
| **Presenter** | `roles/<role>/tasks/main.yml` | Orchestrates: calls module, registers result, decides WHICH view to use. The Presenter IS the Controller in MVP — the distinction is that the Presenter owns the routing decision, not just the orchestration. |

### Rules (enforceable)

1. **Presenters select between multiple Views.** A `tasks/main.yml` that
   always writes the same shape to the same path is a Controller. A
   Presenter distinguishes itself by selecting among multiple output
   formats (JSON artifact vs. debug msg vs. template render vs. registered
   fact) based on input flags or result content.
2. **The Presenter never mutates model data.** It calls the module, receives
   the result, and routes it. No `set_fact` that reshapes the module's
   return value — that's a ViewModel, which belongs in `vars/` or `defaults/`.
3. **Modules return structured contracts, not display strings.** A module
   returning `msg: "Successfully booked hotel at 123 Main St"` violates
   MVP — the Presenter should compose that string from structured fields.

### Example: travel/trip_planner (MVP-aligned)

```text
Model:     plugins/modules/trip_planner.py           → constructs TripRequest, Itinerary, returns structured result
Presenter: roles/trip_planner/tasks/main.yml         → receives trip_result, routes to artifact write + debug output
View:      artifact JSON file + ansible.builtin.debug → two different views of the same model output
```

---

## Pattern selection guide

| When to use | Pattern | Key question |
|------------|---------|-------------|
| Simple input→output module with one display format | **MVC** | Does the role always render the same way? |
| Module output needs display transformation before rendering | **MVVM** | Is there a non-trivial mapping from raw data to display? |
| User intent drives a single state transition | **MVI** | Is each invocation a complete, self-contained action? |
| Multiple display formats selected by input flags | **MVP** | Does the role choose between output formats? |

A single role may use a HYBRID: e.g. MVC for the module call + MVVM for locale-aware
formatting of the result. When hybrid, document which pattern applies to which layer.

---

## Layer-wiring contract

Across all four patterns, these wiring rules hold:

1. **Contracts are the single source of truth for data shape.** Every
   collection MUST have `plugins/module_utils/contracts.py` (or equivalent)
   with Pydantic models for every entity the collection manipulates. No
   raw dicts as public return types from modules.
2. **Module_utils delegate to core, never reimplement.** If logic exists in
   `src/general_ludd/`, the module_utils wraps it (per
   [MODULE_UTILS_CONTRACT.md](MODULE_UTILS_CONTRACT.md) §3.4). A
   module_utils that duplicates core logic is an architecture violation.
3. **Roles never contain business logic.** `tasks/main.yml` is orchestration
   glue — validation, module invocation, output routing. Any data
   transformation, calculation, or formatting belongs in the module or
   module_utils.
4. **All modules must have a documented argument_spec with typed parameters.**
   `argument_spec=dict(foo=dict(type="str", required=True))` — no passthrough
   `config: dict` entries.

---

## Collection Audits

### Travel (`collections/ansible_collections/general_ludd/travel/`)

**Structure quality:** Contracts (`contracts.py`, 845 lines) are well-defined
with 40+ Pydantic models covering TripRequest, FlightBooking, HotelBooking,
Itinerary, RouteStop, etc. This is the strongest contract layer of any
collection audited.

**Violations found:**

1. **[MVI] `trip_planner.py:171-193` — Model mixes with View rendering.**
   `plan_trip()` constructs Pydantic models, then appends display-formatted
   `result["trip"]` and `result["days"]` dicts with activity strings
   (`"Arrive in {dest}"`, `"Explore {dest} highlights"`). These are View
   strings injected into the Model return. Fix: return only contracts; let
   the role's `tasks/main.yml` (Presenter) compose display text.

2. **[MVI] `hotel_search.py:242-246` — Contract mutated with presentation fields.**
   After `booking.model_dump(mode="json")`, presentation keys (`stars`,
   `rating`, `amenities`, `distance_km`) are injected into the dict. The
   HotelBooking contract has no `stars`, `rating`, or `distance_km` fields.
   Fix: these belong in a ViewModel or the return values should use a
   dedicated display wrapper model.

3. **[MVP] `core.py:42-82` — Hardcoded data mixed with business logic.**
   `_AIRLINE_DB`, `_HOTEL_DB`, `_ROUGH_DISTANCES`, `_VISA_REQUIRED` are
   knowledge/seed data embedded in the business logic module. They are
   neither contracts nor config. Fix: extract to `module_utils/knowledge.py`
   (already partially done — `itinerary_generator.py` imports from
   `knowledge.py` but `core.py` and `accommodation.py` duplicate data).

4. **[MVC] `accommodation.py:274-318` — Returns raw dicts, not contracts.**
   `HotelSearchEngine.search()` returns `list[dict]` with keys like
   `"hotel_name"`, `"total_price"`, `"check_in": search.check_in.isoformat()`.
   The method calls `.isoformat()` (a display serialization) inside model
   logic. Fix: return `list[HotelBooking]` and let the caller serialize.

5. **[Cross-layer] `output_parser.py:27-29` — Cross-collection import.**
   Imports `extract_price` and `extract_stars` from
   `agent.plugins.module_utils.searxng`. General-purpose utility functions
   needed by multiple collections should live in a shared
   `module_utils/common.py` under the agent collection, not be imported
   cross-collection. Per MODULE_UTILS_CONTRACT.md §3.1, duplicate instead of
   cross-import — or, better, move the shared functions to agent.

6. **[MVI] `searxng_client.py:143-158` — Generates display text in query logic.**
   `TravelIndexManager.query()` synthesizes `title`, `content`, `url` fields
   with human-readable strings (`"Simulated {engine} result matching..."`).
   This is stub/simulation code in what should be a pure model layer. Fix:
   keep simulation data in a separate `_SIMULATED_RESULTS` constant or
   dedicated test fixture.

### Language (`collections/ansible_collections/general_ludd/language/`)

**Structure quality:** No Pydantic contracts exist. All roles use
`ansible.builtin.script` to call `files/*.py` scripts — there are zero custom
Ansible modules. The collection relies entirely on `src/general_ludd/language/`
for business logic, with `module_utils/core.py` acting as a re-export adapter.

**Violations found:**

1. **[MVC/MVI gap] No contracts module.** The language collection has no
   `plugins/module_utils/contracts.py`. Every role invokes `ansible.builtin.script`
   with ad-hoc string parameters instead of calling a typed module. This means
   no structured input validation at the Ansible layer, no typed return values,
   and no molecule verification against Pydantic schemas. Fix: add contracts
   for at minimum `LanguageDetectionResult`, `TransliterationResult`,
   `TranslationResult`, `HomoglyphScanResult`.

2. **[MVVM] `locale_format/vars/main.yml` — ViewModel without a Model.**
   `date_lengths`, `plural_categories`, `rtl_languages` are display constants
   that have no corresponding Pydantic enum or contract. They duplicate data
   already in `src/general_ludd/language/locale_data.py`. Fix: import these
   from the source-of-truth in `locale_data.py` rather than hardcoding a
   subset.

3. **[MVP] All language roles — Model bypass.** Every `tasks/main.yml` uses
   `ansible.builtin.script` to call a Python file directly. This pattern
   bypasses Ansible's module contract system entirely:
   - No `argument_spec` = no input validation at playbook parse time
   - No `RETURN` documentation = callers cannot know the output shape
   - No `check_mode` support
   - No idempotency (`changed_when: false` on all script invocations)
   Fix: promote each `files/*.py` to a proper `plugins/modules/<name>.py`
   module with full argument_spec + RETURN docs + contracts integration.

4. **[MVVM] `phonetic_transcribe/defaults/main.yml` — ViewModel controls destructive ops.**
   `enable_git_push: false` is a destructive-operations guard comment-labeled
   "REPORT-ONLY." A ViewModel should not gate side effects — that's the
   Controller or Presenter's job. If the role must not push, the task simply
   should not call a push task. A default var that claims to disable something
   the role never does is dead metadata.

5. **[Cross-layer] `core.py` imports from `src/general_ludd/language/`** via
   `sys.path.insert(0, str(_SRC))`. This is a correct delegation pattern per
   MODULE_UTILS_CONTRACT.md §3.4, but it means the module_utils cannot run
   without the full repo checkout. Collections intended for independent
   distribution (via Ansible Galaxy) would break. Not a violation of the
   layer rules, but a deployment constraint worth noting.

### Agent/STS (`collections/ansible_collections/general_ludd/agent/`)

**Structure quality:** The agent collection has 45 modules and 120+ roles —
the largest by far. Its `galaxy.yml:25-37` declares five STS roles
(`sts_mint`, `sts_validate`, `sts_revoke`, `sts_reap`, `sts_audit`) with
capability tags (`token_mint`, `capability_narrowing`, etc.).

**Violations found:**

1. **[GAP] STS roles declared but not implemented.**
   `galaxy.yml:26-37` lists `sts_mint` through `sts_audit` as
   `role_capabilities`. No corresponding role directories exist under
   `agent/roles/`, no modules exist under `agent/plugins/modules/`, and no
   module_utils STS code exists. The collection manifest claims capabilities
   (`token_mint`, `token_validate`, `token_revoke`, `cascade_revocation`,
   `token_reap`, `token_audit`) that are entirely unimplemented. This is a
   **manifest-content gap** — the galaxy.yml is advertising functionality
   that does not exist on disk. Per the layer-wiring contract, contracts
   must exist before roles reference them. No contracts → no roles → no
   capability.

   As of HEAD 693d35d9, this gap affects: `sts_mint`, `sts_validate`,
   `sts_revoke`, `sts_reap`, `sts_audit` — all five declared STS roles.

---

## Cross-collection audit summary

| Collection | Contracts | Modules | Roles with own module | MVC gaps | MVI violations | Data in business logic |
|-----------|-----------|---------|----------------------|---------|---------------|----------------------|
| travel | 40+ Pydantic models (strong) | 3 modules | 2 roles | 1 (accommodation returns dicts) | 2 (trip_planner, hotel_search) | 2 (core.py, accommodation.py duplicate data) |
| language | 0 (none) | 0 custom modules | 11 roles via script | All roles (script bypass) | All roles (no argument_spec) | 1 (vars/main.yml duplicates src/) |
| agent/STS | 0 (none) | 0 STS modules | 0 STS roles | Not applicable (unimplemented) | Not applicable | galaxy.yml-only declarations |

### Priority fixes

1. **Language:** Add `contracts.py` with Pydantic models. Upgrade at least the
   top 3 roles (translate, language_detect, homoglyph_scan) from script calls
   to proper modules.
2. **Travel:** Extract `_HOTEL_DB` and `_AIRLINE_DB` from `core.py` and
   `accommodation.py` into `knowledge.py`. Add display-wrapper models
   (`HotelBookingDisplay`) so modules don't inject presentation fields into
   contract dicts.
3. **Agent/STS:** Either build STS contracts + modules + roles, or remove
   unimplemented role_capabilities from `galaxy.yml`. A manifest that claims
   capabilities not on disk is a correctness bug.

---

## Enforcement

All four patterns are structurally enforced:

1. **Editor gate** — `enforce-tdd.ts`: new module_utils and modules must
   have a corresponding test file on disk before the editor allows writes.
2. **Module contract** — `MODULE_UTILS_CONTRACT.md`: delegation from
   module_utils → `src/general_ludd/`, documented API surface, type hints.
3. **This document** — architectural pattern selection and layer rules.
   Collection audits are re-run each time a new role or module is added.
4. **Molecule self-tests** — `molecule/default/verify.yml` for each role
   validates the View layer (MVVM/MVP) against expected output.
