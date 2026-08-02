# general_ludd.travel

Travel planning collection — flight search, hotel search, trip itinerary
planning, and SearXNG metasearch integration.

## Implemented modules (`plugins/modules/`)

| Module | Purpose |
|---|---|
| `flight_search` | Search flights between origin/destination with date, cabin, stops, and price filters. |
| `hotel_search` | Search hotels at a destination with dates, budget, stars, and amenities filters. |
| `searxng_search` | Query a SearXNG instance for web, news, or image results. |
| `trip_planner` | Generate a multi-day trip itinerary with daily activities and cost estimates. |

## Implemented roles (`roles/`)

| Role | Purpose |
|---|---|
| `searxng_setup` | Install and configure a local SearXNG metasearch instance via Docker. |
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
