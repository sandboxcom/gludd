"""HTTP router: travel expert endpoints.

Surfaces travel planning, flight search, hotel search, and event planning
over HTTP via the daemon::

  - POST /api/travel/plan    -- trip itinerary planning
  - POST /api/travel/flights -- flight search
  - POST /api/travel/hotels  -- hotel search
  - POST /api/travel/event   -- event planning at a destination

Each endpoint delegates to the ``AnsibleRunnerAdapter``, running the
corresponding playbook from the ``playbooks/`` directory.

Add ``?live=true`` to flights, hotels, or event endpoints to also query
SearXNG for real-time search data, returned under the ``live_data`` key.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TRAVEL_PLAN_PLAYBOOK = "travel_plan.yml"
TRAVEL_SEARCH_FLIGHTS_PLAYBOOK = "travel_search_flights.yml"
TRAVEL_SEARCH_HOTELS_PLAYBOOK = "travel_search_hotels.yml"
TRAVEL_EVENT_PLAN_PLAYBOOK = "travel_event_plan.yml"


def _resolve_playbook_path(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [here / "playbooks" / name, Path.cwd() / "playbooks" / name]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def _ensure_playbook_registered(runner: Any, playbook_name: str, path: Path) -> None:
    if playbook_name not in runner.list_playbooks():
        runner.register_playbook(playbook_name, str(path))


# ---------------------------------------------------------------------------
# SearXNG live-data helper
# ---------------------------------------------------------------------------


def _call_searxng(query: str, category: str) -> dict[str, Any]:
    """Query SearXNG directly and return structured travel results."""
    collections = Path(__file__).resolve().parent.parent.parent.parent / "collections" / "ansible_collections"
    collections_str = str(collections)
    if collections_str not in sys.path:
        sys.path.insert(0, collections_str)

    from general_ludd.travel.plugins.modules.searxng_search import search_searxng  # type: ignore[import-untyped]

    results, raw, search_url = search_searxng(
        query=query,
        category=category,
        searxng_url="http://localhost:8080",
        engines="",
        max_results=10,
        safe_search=0,
        language="en",
        timeout=10,
    )
    return {
        "results": results,
        "raw_results": raw,
        "result_count": len(results),
        "query": query,
        "category": category,
        "search_url": search_url,
    }


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class TravelPlanRequest(BaseModel):
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None
    return_date: str | None = None
    budget: float | None = None
    interests: list[str] = Field(default_factory=list)
    travelers: int = Field(default=1, ge=1)


class TravelFlightsRequest(BaseModel):
    origin: str | None = None
    destination: str | None = None
    departure_date: str | None = None
    return_date: str | None = None
    passengers: int = Field(default=1, ge=1)
    cabin_class: str = "economy"
    max_stops: int = Field(default=-1, ge=-1)


class TravelHotelsRequest(BaseModel):
    destination: str | None = None
    checkin_date: str | None = None
    checkout_date: str | None = None
    guests: int = Field(default=1, ge=1)
    rooms: int = Field(default=1, ge=1)
    min_stars: int = Field(default=0, ge=0, le=5)
    max_price_per_night: float | None = None


class TravelEventRequest(BaseModel):
    destination: str | None = None
    event_date: str | None = None
    event_type: str | None = None
    attendees: int = Field(default=1, ge=1)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register(app: FastAPI, _daemon_state: dict[str, object]) -> None:
    @app.post("/api/travel/plan")
    async def travel_plan(body: TravelPlanRequest) -> dict[str, Any]:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        try:
            runner = AnsibleRunnerAdapter()
            path = _resolve_playbook_path(TRAVEL_PLAN_PLAYBOOK)
            _ensure_playbook_registered(runner, TRAVEL_PLAN_PLAYBOOK, path)
            result = runner.run_playbook(
                TRAVEL_PLAN_PLAYBOOK,
                extravars={
                    "origin": body.origin or "",
                    "destination": body.destination or "",
                    "departure_date": body.departure_date,
                    "return_date": body.return_date,
                    "budget": body.budget,
                    "interests": body.interests,
                    "travelers": body.travelers,
                },
            )
        except Exception as err:
            logger.exception("travel plan failed")
            raise HTTPException(status_code=500, detail="travel plan failed") from err
        return _travel_result(result)

    @app.post("/api/travel/flights")
    async def travel_flights(
        body: TravelFlightsRequest,
        live: bool = Query(False, description="Query SearXNG for live flight data"),
    ) -> dict[str, Any]:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        try:
            runner = AnsibleRunnerAdapter()
            path = _resolve_playbook_path(TRAVEL_SEARCH_FLIGHTS_PLAYBOOK)
            _ensure_playbook_registered(runner, TRAVEL_SEARCH_FLIGHTS_PLAYBOOK, path)
            result = runner.run_playbook(
                TRAVEL_SEARCH_FLIGHTS_PLAYBOOK,
                extravars={
                    "origin": body.origin or "",
                    "destination": body.destination or "",
                    "departure_date": body.departure_date,
                    "return_date": body.return_date,
                    "passengers": body.passengers,
                    "cabin_class": body.cabin_class,
                    "max_stops": body.max_stops,
                },
            )
        except Exception as err:
            logger.exception("travel flight search failed")
            raise HTTPException(status_code=500, detail="travel flight search failed") from err

        output = _travel_result(result)
        if live:
            query_str = (
                f"flights from {body.origin or ''} to {body.destination or ''} on {body.departure_date or 'any date'}"
            )
            output["live_data"] = _call_searxng(query_str, "flights")
        return output

    @app.post("/api/travel/hotels")
    async def travel_hotels(
        body: TravelHotelsRequest,
        live: bool = Query(False, description="Query SearXNG for live hotel data"),
    ) -> dict[str, Any]:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        try:
            runner = AnsibleRunnerAdapter()
            path = _resolve_playbook_path(TRAVEL_SEARCH_HOTELS_PLAYBOOK)
            _ensure_playbook_registered(runner, TRAVEL_SEARCH_HOTELS_PLAYBOOK, path)
            result = runner.run_playbook(
                TRAVEL_SEARCH_HOTELS_PLAYBOOK,
                extravars={
                    "destination": body.destination or "",
                    "checkin_date": body.checkin_date,
                    "checkout_date": body.checkout_date,
                    "guests": body.guests,
                    "rooms": body.rooms,
                    "min_stars": body.min_stars,
                    "max_price_per_night": body.max_price_per_night,
                },
            )
        except Exception as err:
            logger.exception("travel hotel search failed")
            raise HTTPException(status_code=500, detail="travel hotel search failed") from err

        output = _travel_result(result)
        if live:
            query_str = f"hotels in {body.destination or ''} for {body.guests} guest(s)"
            output["live_data"] = _call_searxng(query_str, "hotels")
        return output

    @app.post("/api/travel/event")
    async def travel_event(
        body: TravelEventRequest,
        live: bool = Query(False, description="Query SearXNG for live event data"),
    ) -> dict[str, Any]:
        from general_ludd.ansible.runner import AnsibleRunnerAdapter

        try:
            runner = AnsibleRunnerAdapter()
            path = _resolve_playbook_path(TRAVEL_EVENT_PLAN_PLAYBOOK)
            _ensure_playbook_registered(runner, TRAVEL_EVENT_PLAN_PLAYBOOK, path)
            result = runner.run_playbook(
                TRAVEL_EVENT_PLAN_PLAYBOOK,
                extravars={
                    "destination": body.destination or "",
                    "event_date": body.event_date,
                    "event_type": body.event_type,
                    "attendees": body.attendees,
                },
            )
        except Exception as err:
            logger.exception("travel event plan failed")
            raise HTTPException(status_code=500, detail="travel event plan failed") from err

        output = _travel_result(result)
        if live:
            query_str = f"{body.event_type or 'events'} in {body.destination or ''} on {body.event_date or 'any date'}"
            output["live_data"] = _call_searxng(query_str, "events")
        return output


def _travel_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(result.get("status", "unknown")),
        "rc": int(result.get("rc", -1)),
        "events": result.get("events", []),
    }
