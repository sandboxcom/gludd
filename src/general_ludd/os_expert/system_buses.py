"""System bus primitives per platform.

Covers dbus, COM, XPC, Binder, Mach ports — connect/query primitives
and introspection APIs for each inter-process communication bus.
"""

from __future__ import annotations

from typing import TypedDict


class SystemBus(TypedDict):
    platform: str
    bus_name: str
    transport: str
    default_address: str
    introspection_tool: str


SYSTEM_BUSES: list[SystemBus] = []
