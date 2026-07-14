"""Logging system APIs per platform.

Covers os_log, journald, EventLog, logcat, syslog — query and stream
APIs for each platform's native logging subsystem.
"""

from __future__ import annotations

from typing import TypedDict


class LoggingSystem(TypedDict):
    platform: str
    system_name: str
    log_path: str
    query_command: str
    stream_command: str


LOGGING_SYSTEMS: list[LoggingSystem] = []
