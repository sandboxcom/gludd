"""Sandbox backend implementations: process-level, container-level, and VM-level isolation."""

from __future__ import annotations

from general_ludd.sandbox.backends.container_backend import ContainerBackend
from general_ludd.sandbox.backends.firecracker_backend import FirecrackerBackend
from general_ludd.sandbox.backends.process_backend import ProcessBackend

__all__ = [
    "ContainerBackend",
    "FirecrackerBackend",
    "ProcessBackend",
]
