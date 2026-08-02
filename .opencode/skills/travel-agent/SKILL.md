---
name: travel-agent
description: "Use for travel planning, booking research, and itinerary construction. Covers flight search and comparison, hotel/accommodation research, car rental and ground transport, multi-stop route optimization, visa and passport requirements, travel insurance, packing recommendations, local customs and etiquette, budget estimation, and trip-day itinerary generation. Trigger keywords: travel, trip, flight, hotel, car, vacation, wedding, funeral, meeting, tour, itinerary, booking, multi-stop, route, passport, visa, searxng."
location: "/Users/shawnwilson/gludd/.opencode/skills/travel-agent/SKILL.md"
---

# Travel Agent

A practical travel-planning service: destination research → transit and lodging
search → visa/passport gating → itinerary construction → budget estimation.
Implements the travel-planning lifecycle from discovery to day-by-day schedule.
Uses web search (searxng / direct URL fetch) for live pricing and availability;
returns structured recommendations with cited sources.

## When to Use

Any actionable travel-planning task: researching flights, comparing hotels,
building a multi-city itinerary, checking visa requirements, estimating a trip
budget, finding local transit options, or packing for a specific climate/duration.
If the query is purely about food at the destination, use `culinary-expert`
alongside this skill.

## Capabilities

| Domain | What it does | Tools / sources |
|---|---|---|
| Flight search | Compare routes, carriers, layovers, and price ranges for one-way, round-trip, and multi-stop trips | Google Flights, Skyscanner, Kayak, airline sites |
| Hotel / accommodation | Find and compare hotels, hostels, short-term rentals by location, budget, amenities, and review score | Booking.com, Airbnb, Hotels.com, Tripadvisor |
| Ground transport | Car rental comparison, train/bus routes, public transit passes, airport transfers | Rentalcars, Rome2Rio, local transit authority sites |
| Multi-stop routing | Optimize city order and transit mode for trips visiting 3+ destinations | Rome2Rio, flight matrix tools, rail maps |
| Visa & passport | Check entry requirements by citizenship + destination, processing times, eVisa eligibility | Government sites, IATA TravelCentre, Timatic |
| Travel insurance | Compare coverage tiers (medical, cancellation, baggage), provider recommendations | Squaremouth, InsureMyTrip, policy aggregators |
| Budget estimation | Build per-person trip budgets: transit, lodging, food, activities, contingency | Aggregated cost-of-living data, tourism board estimates |
| Itinerary construction | Assemble day-by-day schedules factoring in transit times, opening hours, meal breaks, and rest | Calendar tools, attraction hours, transit schedules |
| Packing & prep | Climate-appropriate packing lists, adapter/voltage info, vaccination requirements, local customs | Weather APIs, CDC/WHO, State Dept advisories, Wikitravel |
| Events (wedding, funeral, meeting) | Group travel coordination, venue-area lodging, attendee logistics | Group booking tools, venue-area search |

## Workflow

### 1. Gather requirements
- **Trip type:** vacation, business, wedding, funeral, meeting, tour, multi-stop
- **Dates:** fixed or flexible window (e.g. "first two weeks of June")
- **Origin + destination(s):** city or airport code
- **Travelers:** number, ages, nationalities, special needs
- **Budget range:** per-person or total; rough or firm
- **Constraints:** nonstop only, specific airline/alliance, layover limits, visa timeline

### 2. Check gates (non-negotiable)
- **Passport validity**: ≥6 months beyond return date for most countries
- **Visa required?** Check by citizenship → destination; note eVisa vs embassy timelines
- **Vaccination requirements** (yellow fever, COVID, routine)
- **Travel advisories / warnings** (State Dept, FCO, Smartraveller)
- Flag any gate that would block the trip BEFORE doing detailed booking work

### 3. Research & compare
- Flights: search 3-5 aggregators, note price vs connections vs duration tradeoffs
- Hotels: filter by budget, location radius, review threshold, amenities
- Ground: compare car rental vs train vs rideshare for each leg
- Present a **scored comparison table** (price, convenience, risk) — never a single option

### 4. Build itinerary
- Day-by-day schedule with transit times, activity durations, meal gaps, rest
- Factored into blocks: morning, afternoon, evening
- Opening hours and seasonal closures checked where available
- Downloadable offline map pins / directions referenced

### 5. Estimate budget
- Line-item budget: flights, lodging, food, transit, activities, insurance, contingency (10-15%)
- Per-person and group totals
- Note which items are pre-pay vs pay-at-destination

## Comparison Table Template

For every search return a table with at minimum:

| Option | Price | Duration / Check-in | Stops / Location | Score (1-10) | Notes |
|---|---|---|---|---|---|
| Flight A | $X | Hh Mm | N stops | 8 | best departure time |
| Flight B | $Y | Hh Mm | N stops | 6 | redeye; saves hotel night |
| Hotel A | $X/night | 3pm / 11am | 0.5mi from center | 9 | breakfast included |

## Multi-Stop Routing Rules

1. Minimize backtracking: plot destinations on a map, prefer linear or loop routes
2. Open-jaw flights (fly into A, out of B) often save a day of backtrack travel
3. For 3+ cities in one region, compare flying vs train vs bus per leg
4. Allocate ≥2 nights per stop (1-night stops are exhausting)
5. Factor transit time between cities into the itinerary — a 4-hour train ride burns a morning

## Visa & Passport Quick Reference

| Region | 6-mo passport rule | Common visa pattern |
|---|---|---|
| Schengen Area (EU) | Yes (90/180 rule) | ETIAS from 2025; currently visa-free for US/CA/UK/AU/NZ ≤90 days |
| UK | No (valid for stay) | ETA from 2024; visa-free for US/CA/UK/AU/NZ ≤6 months |
| Japan | No (valid for stay) | Visa-free ≤90 days for 68+ nationalities |
| China | Yes | Tourist visa (L) required; 144-hr transit-without-visa at select cities |
| India | Yes | eVisa for 160+ nationalities (30d/1yr/5yr) |
| Brazil | No | eVisa returning 2025; currently visa-free for US/CA/AU ≤90 days |
| Australia | No | ETA or eVisitor required before departure |
| Thailand | Yes | Visa-free ≤30 days (expanding to 60); eVisa available |
| UAE (Dubai) | Yes | Visa-free ≤30 days for many; GCC residents different rules |

Always verify against an official government source — never rely on memory for entry requirements.

## Search Protocol

1. **Searxng** for initial discovery (aggregates multiple engines, privacy-respecting)
2. **Direct site fetch** for pricing confirmation (airline.com, hotel.com, rentalcar.com)
3. **Government sites** for visa, passport, travel advisory data
4. Always include the URL of the source used in results

## Budget Estimation Defaults (per person, per day)

| Category | Budget | Mid-range | Premium |
|---|---|---|---|
| Hotel | $50-100 | $100-250 | $250+ |
| Food | $20-40 | $40-80 | $80+ |
| Local transit | $5-15 | $15-40 | $40+ |
| Activities | $10-30 | $30-100 | $100+ |

Adjust for destination cost-of-living. Southeast Asia ≈ 0.3-0.5x US baseline; Switzerland/Norway ≈ 1.5-2x.

## Event-Specific Guidance

### Weddings
- Book lodging as close to venue as possible (walking distance ideal)
- Arrive ≥2 days before the event (recovery from travel, rehearsal)
- Group rate inquiries: contact venue-area hotels directly, not aggregators
- Gift/registry logistics: ship to destination or bring? Check local customs

### Funerals
- Prioritize speed over cost — flexible/refundable tickets, shortest total travel time
- Bereavement fares: call airlines directly (not available online); require funeral home documentation
- Accommodation near family/venue, not tourist areas
- Dark/conservative attire; check cultural norms for the specific service

### Business Meetings
- Hotel with reliable WiFi, workspace, and proximity to meeting venue
- Direct flights preferred over connections (reduces delay risk)
- Timezone adjustment: arrive 1 day early if crossing ≥5 time zones
- Local SIM / eSIM for data; backup internet (coworking pass)

## Packing Rules of Thumb

- **Carry-on only** for trips ≤5 days; checked bag for 6+ days or formal events
- **Layer, don't bulk**: 3 thin layers beat 1 heavy coat for variable climates
- **Shoes are the packing bottleneck**: wear the bulkiest pair; pack ≤2 additional pairs
- **Adapters**: universal adapter + check voltage (most electronics are dual-voltage 110-240V)
- **Medications**: carry-on only; original packaging; copy of prescription; note generic names
- **Documents**: passport, visa printout, travel insurance card, 2 extra passport photos

## Output Format

Every response should include:
1. **1-line recommendation** (best option with key reason)
2. **Comparison table** (all researched options scored)
3. **Gates status** (passport, visa, advisory — green/yellow/red per traveler)
4. **Sources** (URLs for each datum)
5. **Caveats** (prices change, visa rules change, hours change — all claims are time-of-search)

## See Also

- `culinary-expert` — food and restaurant recommendations at destination
- `materials-engineer` — luggage durability, gear selection
- Official sources: IATA TravelCentre, Timatic, State Dept travel.state.gov, CDC travelers' health
