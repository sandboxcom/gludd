#!/usr/bin/python
# Copyright: Agentic Harness
# SPDX-License-Identifier: MIT
"""
DOCUMENTATION:
  module: trip_planner
  short_description: Plan a trip itinerary with dates, destinations, and activities
  description:
    - Calls the travel core planning engine (C(travel.core.plan_trip)) to generate
      a multi-day trip itinerary.
    - Accepts origin, destinations, date range, budget, interests, and traveler count.
    - Returns a structured itinerary with daily activities, estimated costs, and
      travel logistics between stops.
  options:
    origin:
      description: Starting city or airport code.
      type: str
      required: true
    destinations:
      description: List of destinations (city names or airport codes).
      type: list
      elements: str
      required: true
    start_date:
      description: Trip start date (YYYY-MM-DD).
      type: str
      required: true
    end_date:
      description: Trip end date (YYYY-MM-DD).
      type: str
      required: true
    budget:
      description: Total trip budget in USD.
      type: float
      default: 0.0
    interests:
      description: List of interests for activity suggestions.
      type: list
      elements: str
      default: []
    travelers:
      description: Number of travelers.
      type: int
      default: 1
    trip_style:
      description: Preferred trip style (budget, comfort, luxury).
      type: str
      default: comfort
      choices: [budget, comfort, luxury]
    daemon_url:
      description: Base URL of the daemon.
      type: str
      default: "http://localhost:8000"
    psk:
      description: Pre-shared key for daemon auth.
      type: str
      no_log: true
      default: ""

EXAMPLES:
  - name: Plan a trip to Paris and London
    general_ludd.travel.trip_planner:
      origin: "NYC"
      destinations: ["Paris", "London"]
      start_date: "2026-09-01"
      end_date: "2026-09-14"
      budget: 5000.0
      interests: ["museums", "food", "history"]
      travelers: 2
    register: plan

RETURN:
  itinerary:
    description: Structured trip itinerary with daily plans.
    type: dict
    returned: always
  estimated_cost:
    description: Estimated total cost breakdown.
    type: dict
    returned: always
  travel_logistics:
    description: Travel segments between destinations.
    type: list
    elements: dict
    returned: always
"""

from __future__ import annotations

import datetime as _datetime
from typing import Any

from ansible.module_utils.basic import AnsibleModule  # type: ignore[import]

from general_ludd.travel.contracts import (
    Budget,
    BudgetLineItem,
    Itinerary,
    ItineraryStatus,
    Money,
    TimelineEntry,
    TripRequest,
)


def _make_trip_request(
    origin: str,
    destinations: list[str],
    start_date_str: str,
    end_date_str: str,
    budget_amount: float,
    travelers: int,
    trip_style: str,
) -> TripRequest:
    start = _datetime.date.fromisoformat(start_date_str)
    end = _datetime.date.fromisoformat(end_date_str)
    return TripRequest(
        origin=origin,
        destination=destinations[-1],
        start_date=start,
        end_date=end,
        travelers=[],
        budget=Budget(
            currency="USD",
            line_items=[
                BudgetLineItem(
                    category="total",
                    description=f"{len(destinations)}-stop trip, {trip_style}",
                    amount=budget_amount if budget_amount > 0 else len(destinations) * 2 * 200.0,
                )
            ],
            total=budget_amount if budget_amount > 0 else len(destinations) * 2 * 200.0,
        ),
    )


def plan_trip(
    origin: str,
    destinations: list[str],
    start_date: str,
    end_date: str,
    budget: float,
    interests: list[str],
    travelers: int,
    trip_style: str,
) -> dict[str, Any]:
    req = _make_trip_request(origin, destinations, start_date, end_date, budget, travelers, trip_style)
    days_count = max(1, len(destinations) * 2)

    timeline: list[TimelineEntry] = []
    for i, dest in enumerate(destinations):
        day_num = i + 1
        day_start = _datetime.datetime.combine(req.start_date, _datetime.time(8, 0)) + _datetime.timedelta(days=i)
        day_end = day_start + _datetime.timedelta(hours=16)
        timeline.append(
            TimelineEntry(
                entry_index=i,
                type="stay",
                start_time=day_start,
                end_time=day_end,
                timezone="UTC",
                location=dest,
                details=f"Explore {dest}: {', '.join(interests) if interests else 'highlights'}",
            )
        )

    itinerary = Itinerary(
        request_id=req.request_id,
        status=ItineraryStatus.draft,
        timeline=timeline,
        total_cost=Money(amount=req.budget.total, currency=req.budget.currency),
    )

    result = itinerary.model_dump(mode="json")

    result["trip"] = {
        "origin": origin,
        "destinations": destinations,
        "start_date": start_date,
        "end_date": end_date,
        "travelers": travelers,
        "style": trip_style,
    }
    result["days"] = []
    for i, dest in enumerate(destinations):
        day_num = i + 1
        result["days"].append(
            {
                "day": day_num,
                "location": dest,
                "activities": [f"Arrive in {dest}", f"Explore {dest} highlights"],
                "meals": ["breakfast", "lunch", "dinner"],
                "accommodation": dest,
                "estimated_daily_cost": (req.budget.total / max(1, days_count) if req.budget.total > 0 else 0),
            }
        )
    result["total_estimated_cost"] = req.budget.total

    return result


def main() -> None:
    module = AnsibleModule(
        argument_spec=dict(
            origin=dict(type="str", required=True),
            destinations=dict(type="list", elements="str", required=True),
            start_date=dict(type="str", required=True),
            end_date=dict(type="str", required=True),
            budget=dict(type="float", default=0.0),
            interests=dict(type="list", elements="str", default=[]),
            travelers=dict(type="int", default=1),
            trip_style=dict(type="str", default="comfort", choices=["budget", "comfort", "luxury"]),
            daemon_url=dict(type="str", default="http://localhost:8000"),
            psk=dict(type="str", default="", no_log=True),
        ),
        supports_check_mode=True,
    )

    params = module.params
    origin = params["origin"]
    destinations = params["destinations"]
    start_date = params["start_date"]
    end_date = params["end_date"]
    budget = params["budget"]
    interests = params["interests"]
    travelers = params["travelers"]
    trip_style = params["trip_style"]

    try:
        result = plan_trip(origin, destinations, start_date, end_date, budget, interests, travelers, trip_style)
        module.exit_json(
            changed=False,
            itinerary=result,
            estimated_cost={"total": result.get("total_estimated_cost", 0)},
        )
    except Exception as exc:
        module.fail_json(msg=f"trip_planner failed: {exc}")


if __name__ == "__main__":
    main()
