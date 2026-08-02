"""Sandbox backend implementations: process-level and container-level isolation."""

from __future__ import annotations

from general_ludd.sandbox.backends.container_backend import ContainerBackend
from general_ludd.sandbox.backends.process_backend import ProcessBackend

__all__ = [
    "ContainerBackend",
    "ProcessBackend",
]
