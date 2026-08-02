---
name: travel-agent
description: "Use for end-to-end travel planning: multi-modal itinerary construction (flights, hotels, car rentals), multi-stop and open-jaw routing, booking management, travel-document readiness (passport/visa timelines), event-based trip planning (vacation, wedding, funeral, meeting, tour), budget estimation, local transportation, and web-backed research via searxng. Trigger keywords: travel, trip, flight, hotel, car rental, vacation, wedding, funeral, meeting, tour, itinerary, booking, multi-stop, route, passport, visa, searxng."
location: "/Users/shawnwilson/gludd/.opencode/skills/travel-agent/SKILL.md"
---

# Travel Agent

A full-service travel planning agent that constructs multi-modal, multi-stop
itineraries from natural-language requests, researches availability and pricing
via web search (searxng), validates document readiness, and returns an
actionable trip plan.

## When to Use

Any actionable travel task: building a flight + hotel + car itinerary,
comparing routes, checking passport/visa requirements, estimating costs for an
event trip (vacation, wedding, funeral, business meeting, guided tour), or
researching destination logistics. If the query is purely about geography or
time-zone trivia without a booking/planning component, use a general search
instead.

## Available Task Kinds

| TaskKind | Workflow | Description |
|---|---|---|
| `itinerary` | build_itinerary | Multi-stop, multi-modal trip construction with dates, durations, and segment details |
| `flight` | search_flights | Flight-only search: one-way, round-trip, open-jaw, multi-city; fare comparison |
| `hotel` | search_hotels | Hotel search by city/dates, filter by stars, amenities, proximity to venues |
| `car_rental` | search_car_rentals | Car rental at pickup location with date range, vehicle class, drop-off preference |
| `route` | compare_routes | Compare multiple routing options (direct vs. layover, train vs. fly, drive vs. fly) |
| `budget` | estimate_budget | Cost estimation for flights, lodging, meals, ground transport, activities, incidentals |
| `documents` | check_documents | Passport expiration, visa requirements, entry/exit forms, vaccination requirements |
| `booking` | manage_booking | Track bookings, confirmations, cancellation policies, check-in windows |
| `event_trip` | plan_event | Vacation, wedding, funeral, meeting, or tour trip with event-centric scheduling |
| `local` | local_transport | Airport transfers, rail passes, public transit, ride-share in destination city |

## Trip Plan Structure

Every itinerary response includes:

```yaml
trip:
  name: "short label"
  dates: { start: YYYY-MM-DD, end: YYYY-MM-DD }
  purpose: vacation | wedding | funeral | meeting | tour | other
  travelers: N
segments:
  - kind: flight | hotel | car | train | bus | ferry | other
    provider: airline/hotel/agency name
    booking_reference: null | REF123
    dates: { start: YYYY-MM-DD, end: YYYY-MM-DD }
    origin: { city, country, code }
    destination: { city, country, code }
    details: { flight_number, cabin, room_type, vehicle_class, ... }
documents:
  passports: [{ expiry, months_remaining }]
  visas: [{ country, required, processing_days }]
budget:
  currency: USD
  items: [{ category, estimate, confidence }]
  total_estimate: 1234
```

## Safety Boundaries

- **Never book or charge.** This skill plans and compares; it does NOT place
  reservations, enter payment details, or submit forms.
- **Passport validation is advisory.** Always direct the user to the
  destination country's official embassy/consulate website for current visa
  rules.
- **Pricing is indicative.** Fares and rates retrieved via search are
  snapshots; final prices may differ at booking time.
- **Document timelines flag risks.** Passport with <6 months remaining on
  arrival date, or visa processing time exceeding remaining lead time, must
  produce a prominent warning.
- **Multi-stop routing respects legal constraints.** Open-jaw and multi-city
  routes are constructed per airline alliance rules; unusual routings are
  surfaced with a caveat.

## Usage Examples

```
User: "Plan a 10-day vacation for 2 to Tokyo in October, flying from SFO."
→ Searches flights (SFO→NRT round-trip), hotels (3-4 star, Shinjuku/Shibuya),
  rail pass feasibility, budget estimate, passport check. Returns a 10-day
  itinerary with cost breakdown.

User: "I need to attend a funeral in London on Friday, returning Sunday. Find
me the quickest flights from New York."
→ Searches last-minute fares, short-trip hotel near the venue, airport
  transfer options. Flags tight turnaround for passport validity.

User: "Multi-stop: Chicago → Munich (3 days) → Vienna (2 days, train from
Munich) → back to Chicago. Two travelers, mid-December."
→ Open-jaw flight search, train segment MUC→VIE, hotel at each city, winter
  travel tips, Schengen visa check.

User: "Compare a direct flight vs. driving from Portland to Seattle for a
weekend meeting."
→ Flight search (PDX→SEA, Friday evening / Sunday return) vs. drive
  (distance, fuel cost, parking, time). Recommends based on cost and door-to-
  door time.
```

## Research Protocol

For live pricing and availability, dispatch structured searches via searxng
with the following conventions:

- **Flights:** `"{origin} {destination} flights {date}"` on Google Flights,
  Kayak, Skyscanner
- **Hotels:** `"hotels in {city} {checkin} {checkout} {stars}-star"`
- **Visas:** `"{nationality} visa requirements {destination} official"`
- **Events:** `"{event_type} venues in {city}"` for weddings, meetings, tours

Always attribute search results to the source engine and retrieval timestamp.

## See Also

- `general` subagent — for destination research and general web searches
- `docs/specs/` — project specification directory for feature specs
