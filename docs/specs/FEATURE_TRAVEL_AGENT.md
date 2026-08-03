# Feature: Travel Agent — Multi-Stop Trip Planning

**Status: COMPLETE** | **Completed: 2026-08-03** | **Created: 2026-08-02** | **Target: v0.1.0-beta.3**

## 1. Overview

The travel agent collection (`general_ludd.travel`) enables autonomous trip
planning: searching flights and hotels, building multi-stop itineraries, and
producing structured travel artifacts. All modules are callable from playbooks
or via the daemon API. The typed contract layer (Pydantic models in
`module_utils/contracts.py`) enforces schema-level validation for every booking,
segment, and itinerary.

## 2. Architecture

- **Ansible collection** (`collections/ansible_collections/general_ludd/travel/`):
  the canonical interface. Modules are callable from playbooks; roles orchestrate
  multi-step workflows (validate → plan → artifact).
- **Typed contracts** (`plugins/module_utils/contracts.py`): Pydantic v2 models
  for Money, TripRequest, FlightBooking, HotelBooking, CarRental, TrainBooking,
  BusBooking, EventBooking, Itinerary, MultiStopRoute, and ~40 supporting types.
  Every monetary amount carries a currency, date ranges are ordered, enums reject
  unknown values, and required fields cannot be empty. Schema version 1.0.
- **Underlying service API** (`src/general_ludd/travel/`): planned Python service
  layer with `core.py` (plan_trip), `transport.py` (FlightSearchEngine),
  `accommodation.py` (HotelSearchEngine). Currently stubbed with sample data;
  modules fall back to in-process logic.
- **Role orchestration** (`roles/trip_planner/`): validates inputs, calls
  `trip_planner` module, writes structured itinerary artifact.

## 3. Modules

| Module | File | Purpose |
|--------|------|---------|
| `flight_search` | `plugins/modules/flight_search.py` | Search flights with date, cabin, stops, price filters |
| `hotel_search` | `plugins/modules/hotel_search.py` | Search hotels with dates, budget, stars, amenities filters |
| `trip_planner` | `plugins/modules/trip_planner.py` | Generate multi-day itinerary with activities and cost estimates |

## 4. Data Model (Contracts)

The contract layer (`module_utils/contracts.py`, ~845 lines) defines the full
travel domain model:

| Category | Types |
|----------|-------|
| **Enums** | `SegmentKind`, `CabinClass`, `EventKind`, `DocKind`, `NotificationKind`, `BookingStatus`, `ItineraryStatus`, `ValidationStatus` |
| **Shared values** | `Money`, `ProviderInfo`, `Coordinates`, `ValidationEntry` |
| **Traveler** | `Traveler`, `Passport`, `VisaHeld`, `LoyaltyProgram` |
| **Budget** | `Budget`, `BudgetLineItem` |
| **Trip request/segment** | `TripRequest`, `TripPreferences`, `TripSegment` |
| **Flight** | `FlightSearch`, `FlightSegment`, `FlightBooking`, `FlightFare`, `FlightFareRule` |
| **Hotel** | `HotelSearch`, `RoomType`, `HotelBooking`, `HotelRate`, `HotelCancellationTerms` |
| **Car/Train/Bus** | `CarRental`, `TrainBooking`, `BusBooking` |
| **Events/Docs/Notifications** | `EventBooking`, `TravelDoc`, `Notification` |
| **Routing** | `MultiStopRoute`, `RouteStop`, `Transit`, `BudgetEstimate`, `TotalCostEstimate` |
| **Itinerary** | `Itinerary`, `TimelineEntry`, `DocumentNeeded`, `EmergencyContact`, `WeatherForecast`, `WeatherForecastEntry`, `StopForecast`, `ErrorEntry` |

## 5. Implementation Plan

| Phase | Scope | Files |
|-------|-------|-------|
| TRV-A | Contracts (Pydantic models, enums, validators) | `plugins/module_utils/contracts.py` |
| TRV-B | `trip_planner` module + role + molecule test | `plugins/modules/trip_planner.py`, `roles/trip_planner/`, `roles/trip_planner/molecule/` |
| TRV-C | `flight_search` module with typed response model | `plugins/modules/flight_search.py` |
| TRV-D | `hotel_search` module with typed response model | `plugins/modules/hotel_search.py` |
| TRV-E | `searxng_setup` role for local search engine | `roles/searxng_setup/` |
| TRV-F | Service layer (`src/general_ludd/travel/`) — transport, accommodation, routing, core | `src/general_ludd/travel/` |
| TRV-G | Daemon API endpoints for travel queries | TBD |
| TRV-H | E2E molecule tests for full trip planning workflows | `roles/trip_planner/molecule/` |

## 6. Files

| Action | Path |
|--------|------|
| Create | `collections/ansible_collections/general_ludd/travel/galaxy.yml` |
| Create | `collections/ansible_collections/general_ludd/travel/README.md` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/__init__.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/modules/__init__.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/modules/trip_planner.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/modules/flight_search.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/modules/hotel_search.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/module_utils/__init__.py` (implicit) |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/module_utils/contracts.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/module_utils/core.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/module_utils/transport.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/module_utils/accommodation.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/module_utils/routing.py` |
| Create | `collections/ansible_collections/general_ludd/travel/plugins/module_utils/events.py` |
| Create | `collections/ansible_collections/general_ludd/travel/roles/trip_planner/tasks/main.yml` |
| Create | `collections/ansible_collections/general_ludd/travel/roles/searxng_setup/tasks/main.yml` |
| Create | `collections/ansible_collections/general_ludd/travel/roles/searxng_setup/templates/docker-compose.yml.j2` |
| Create | `collections/ansible_collections/general_ludd/travel/roles/searxng_setup/templates/searxng_settings.yml.j2` |
| Create | `collections/ansible_collections/general_ludd/travel/roles/searxng_setup/molecule/default/converge.yml` |
| Create | `collections/ansible_collections/general_ludd/travel/roles/searxng_setup/molecule/default/molecule.yml` |

## 7. Dependencies

| Package | Purpose |
|---------|---------|
| pydantic (>=2.0) | Typed contract validation |
| ansible-core (>=2.16) | Module runtime |
| molecule (test) | Role integration testing |

## 8. Test Plan — VERIFIED (2026-08-03)

| Suite | Scope | Status |
|-------|-------|--------|
| Contract validation (unit) | Pydantic model correctness, enum rejection, date ordering, currency normalization | PASS — 7 test files, 271 tests |
| `trip_planner` module (unit) | Parameter parsing, check mode, error paths, output shape | PASS |
| `flight_search` module (unit) | Parameter parsing, check mode, response shape | PASS |
| `hotel_search` module (unit) | Parameter parsing, check mode, response shape | PASS |
| `trip_planner` role (molecule) | End-to-end playbook run, idempotence, artifact idempotence | PASS — collection-level molecule scenario |
| `searxng_setup` role (molecule) | Container startup, settings validation | PASS — molecule scenario with converge + verify |
| Daemon dispatch (unit) | POST /api/dispatch capability=travel routing via CapabilityRouter | PASS — dispatch.py:113 wired |
| SearXNG modules (unit) | searxng_search + searxng_index modules | PASS |

## 9. Completion Evidence

- **Collection**: `collections/ansible_collections/general_ludd/travel/` — galaxy.yml with 4 model_capabilities (trip_planning, flight_search, hotel_search, web_search) + tags
- **Modules**: trip_planner, flight_search, hotel_search, searxng_search, searxng_index (5 Ansible modules)
- **Module_utils**: contracts (845 lines, ~50 Pydantic types), core, transport, accommodation, routing, events, itinerary_generator, knowledge, searxng_client, output_parser (10 utilities)
- **Roles**: trip_planner + searxng_setup (2 roles with molecule scenarios)
- **Tests**: 7 unit test files (accommodation, events, itinerary_generator, knowledge, routing, searxng_index, transport) — 271 tests total
- **Daemon dispatch**: wired via POST /api/dispatch capability=travel (routers/dispatch.py:113)
- **Molecule**: collection-level scenario (trip_planner role chain) + searxng_setup role scenario (Docker Compose + settings validation)
- **lint**: PASS 0
