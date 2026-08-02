"""CLI subcommand: ``gludd travel`` — trip planning, flight/hotel search, and event planning.

Each subcommand dispatches to an Ansible collection module via
``AnsibleRunnerAdapter.run_playbook`` with a one-task transient playbook.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from general_ludd.ansible.runner import AnsibleRunnerAdapter

TRAVEL_PLAN_PLAYBOOK = "travel_plan.yml"
TRAVEL_SEARCH_FLIGHTS_PLAYBOOK = "travel_search_flights.yml"
TRAVEL_SEARCH_HOTELS_PLAYBOOK = "travel_search_hotels.yml"
TRAVEL_EVENT_PLAN_PLAYBOOK = "travel_event_plan.yml"


def _resolve_playbook_path(name: str) -> Path:
    here = Path(__file__).resolve().parent.parent.parent
    candidates = [here / "playbooks" / name, Path.cwd() / "playbooks" / name]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def _run_travel_playbook(playbook_name: str, extra_vars: dict[str, Any]) -> dict[str, Any]:
    adapter = AnsibleRunnerAdapter()
    if playbook_name not in adapter.list_playbooks():
        adapter.register_playbook(playbook_name, str(_resolve_playbook_path(playbook_name)))
    return adapter.run_playbook(playbook_name, extravars=extra_vars)


def _cmd_travel_plan(args: argparse.Namespace) -> None:
    extra_vars: dict[str, Any] = {
        "origin": getattr(args, "origin", None),
        "destination": getattr(args, "destination", None),
        "departure_date": getattr(args, "departure_date", None),
        "return_date": getattr(args, "return_date", None),
        "budget": getattr(args, "budget", None),
    }
    result = _run_travel_playbook(TRAVEL_PLAN_PLAYBOOK, extra_vars)
    _emit_result(result)


def _cmd_travel_search_flights(args: argparse.Namespace) -> None:
    extra_vars: dict[str, Any] = {
        "origin": getattr(args, "origin", None),
        "destination": getattr(args, "destination", None),
        "departure_date": getattr(args, "departure_date", None),
        "return_date": getattr(args, "return_date", None),
        "passengers": getattr(args, "passengers", 1),
    }
    result = _run_travel_playbook(TRAVEL_SEARCH_FLIGHTS_PLAYBOOK, extra_vars)
    _emit_result(result)


def _cmd_travel_search_hotels(args: argparse.Namespace) -> None:
    extra_vars: dict[str, Any] = {
        "destination": getattr(args, "destination", None),
        "checkin_date": getattr(args, "checkin_date", None),
        "checkout_date": getattr(args, "checkout_date", None),
        "guests": getattr(args, "guests", 1),
    }
    result = _run_travel_playbook(TRAVEL_SEARCH_HOTELS_PLAYBOOK, extra_vars)
    _emit_result(result)


def _cmd_travel_event_plan(args: argparse.Namespace) -> None:
    extra_vars: dict[str, Any] = {
        "destination": getattr(args, "destination", None),
        "event_date": getattr(args, "event_date", None),
        "event_type": getattr(args, "event_type", None),
        "attendees": getattr(args, "attendees", 1),
    }
    result = _run_travel_playbook(TRAVEL_EVENT_PLAN_PLAYBOOK, extra_vars)
    _emit_result(result)


def _emit_result(result: dict[str, Any]) -> None:
    rc = int(result.get("rc", 1))
    status = str(result.get("status", "failed"))
    print(f"travel playbook finished: status={status} rc={rc}")
    events = result.get("events") or []
    if events:
        print(f"events={len(events)}")
    if status != "successful" or rc != 0:
        sys.exit(1 if rc == 0 else rc)


def add_travel_subparser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    travel = sub.add_parser("travel", help="Travel commands — trip planning, flights, hotels, events")
    travel_sub = travel.add_subparsers(dest="travel_command")

    plan = travel_sub.add_parser("plan", help="Plan a complete trip")
    plan.add_argument("--origin", default=None, help="Departure city or airport code")
    plan.add_argument("--destination", default=None, help="Destination city or airport code")
    plan.add_argument("--departure-date", default=None, help="Departure date (YYYY-MM-DD)")
    plan.add_argument("--return-date", default=None, help="Return date (YYYY-MM-DD)")
    plan.add_argument("--budget", type=float, default=None, help="Budget cap")
    plan.set_defaults(func=_cmd_travel_plan)

    search = travel_sub.add_parser("search", help="Search flights or hotels")
    search_sub = search.add_subparsers(dest="travel_search_command")

    flights = search_sub.add_parser("flights", help="Search flights")
    flights.add_argument("--origin", default=None, help="Departure city or airport code")
    flights.add_argument("--destination", default=None, help="Destination city or airport code")
    flights.add_argument("--departure-date", default=None, help="Departure date (YYYY-MM-DD)")
    flights.add_argument("--return-date", default=None, help="Return date (YYYY-MM-DD)")
    flights.add_argument("--passengers", type=int, default=1, help="Number of passengers")
    flights.set_defaults(func=_cmd_travel_search_flights)

    hotels = search_sub.add_parser("hotels", help="Search hotels")
    hotels.add_argument("--destination", default=None, help="Destination city")
    hotels.add_argument("--checkin-date", default=None, help="Check-in date (YYYY-MM-DD)")
    hotels.add_argument("--checkout-date", default=None, help="Check-out date (YYYY-MM-DD)")
    hotels.add_argument("--guests", type=int, default=1, help="Number of guests")
    hotels.set_defaults(func=_cmd_travel_search_hotels)

    event = travel_sub.add_parser("event", help="Plan an event at a destination")
    event_sub = event.add_subparsers(dest="travel_event_command")

    event_plan = event_sub.add_parser("plan", help="Plan an event")
    event_plan.add_argument("--destination", default=None, help="Event destination city")
    event_plan.add_argument("--event-date", default=None, help="Event date (YYYY-MM-DD)")
    event_plan.add_argument("--event-type", default=None, help="Event type (conference, wedding, meetup, etc.)")
    event_plan.add_argument("--attendees", type=int, default=1, help="Number of attendees")
    event_plan.set_defaults(func=_cmd_travel_event_plan)
