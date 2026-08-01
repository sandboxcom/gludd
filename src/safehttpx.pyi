"""Minimal type surface for safehttpx 0.1.x, which does not ship ``py.typed``."""

from httpx import AsyncBaseTransport

class AsyncSecureTransport(AsyncBaseTransport):
    def __init__(self, verified_ip: str) -> None: ...
