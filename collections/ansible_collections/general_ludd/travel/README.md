# general_ludd.travel

Travel planning collection — flight search, hotel search, trip itinerary
planning, and SearXNG metasearch integration.

## Implemented modules (`plugins/modules/`)

| Module | Purpose |
|---|---|
| `flight_search` | Search flights between origin/destination with date, cabin, stops, and price filters. |
| `hotel_search` | Search hotels at a destination with dates, budget, stars, and amenities filters. |
| `searxng_batch` | Run up to 32 bounded searches through one controller-native runtime. |
| `searxng_instance` | Manage a controller-local native SearXNG runtime idempotently. |
| `searxng_search` | Search natively on the controller, or use an explicit remote compatibility transport. |
| `trip_planner` | Generate a multi-day trip itinerary with daily activities and cost estimates. |

## Implemented roles (`roles/`)

| Role | Purpose |
|---|---|
| `searxng_setup` | Configure and start the native controller-local SearXNG integration. |
| `trip_planner` | Orchestrates the `trip_planner` module: validates inputs, calls the module, writes itinerary artifact. |

## Module utilities (`plugins/module_utils/`)

Shared Python utilities consumed by the modules above.

| Module | Key exports |
|---|---|
| `core.py` | `plan_trip` — full itinerary generation |
| `transport.py` | `FlightSearchEngine` — flight search and ranking |
| `accommodation.py` | `HotelSearchEngine` — hotel search and filtering |
| `contracts.py` | TypedDict contracts for module I/O |
| `events.py` | Event-model data classes for itinerary building |
| `routing.py` | Transit and routing helpers |
| `searxng_runtime.py` | Bounded in-process `searx.webapp` lifecycle and explicit remote rollback |

## Quick start

```yaml
- hosts: localhost
  tasks:
    - name: Search flights
      general_ludd.travel.flight_search:
        origin: "JFK"
        destination: "LHR"
        depart_date: "2026-09-01"
      register: results
```

### Native SearXNG search

The official SearXNG source package must provide the `searx.webapp` import on
the controller. The collection does not install a similarly named PyPI client,
start Docker, or require a Terraform project.

```yaml
- hosts: localhost
  tasks:
    - name: Start the native runtime
      general_ludd.travel.searxng_instance:
        state: started
        namespace: travel-demo

    - name: Find accessible attractions
      general_ludd.travel.searxng_search:
        query: accessible attractions near Central Park
        category: activities
        namespace: travel-demo
      register: attractions

    - name: Research related travel questions through one runtime
      general_ludd.travel.searxng_batch:
        namespace: travel-demo
        requests:
          - id: transit
            query: accessible public transit near Central Park
          - id: dining
            query: accessible restaurants near Central Park
      register: travel_research
```

See [Native SearXNG Controller Runtime](../../../../docs/features/NATIVE_SEARXNG_CONTROLLER_RUNTIME.md)
for lifecycle, check-mode, security, remote compatibility, upstream evidence,
and zero-downtime image rollout details. The
[batch consumer contract](../../../../docs/features/NATIVE_SEARXNG_BATCH_CONSUMERS.md)
records role migration, result compatibility, and batch-specific bounds.
