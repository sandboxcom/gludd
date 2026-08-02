# general_ludd.travel

Travel planning collection — flight search, hotel search, and trip itinerary
planning.

## Implemented modules (`plugins/modules/`)

| Module | Purpose |
|---|---|
| `flight_search` | Search flights between origin/destination with date, cabin, stops, and price filters. |
| `hotel_search` | Search hotels at a destination with dates, budget, stars, and amenities filters. |
| `trip_planner` | Generate a multi-day trip itinerary with daily activities and cost estimates. |

## Implemented roles (`roles/`)

| Role | Purpose |
|---|---|
| `trip_planner` | Orchestrates the `trip_planner` module: validates inputs, calls the module, writes itinerary artifact. |

## Python service API (`src/general_ludd/travel/`)

The planned typed service interfaces. These modules power the ansible modules
with real logic (currently stubbed with sample data).

| Module | Key exports |
|---|---|
| `core.py` | `plan_trip` — full itinerary generation |
| `transport.py` | `FlightSearchEngine` — flight search and ranking |
| `accommodation.py` | `HotelSearchEngine` — hotel search and filtering |

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
