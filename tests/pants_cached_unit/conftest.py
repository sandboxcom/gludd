"""Hermetic fixtures for the explicitly allowlisted Pants unit slice."""

from __future__ import annotations

import os


def pytest_sessionstart(session: object) -> None:
    """Reject a supplied policy value unless it matches the admitted contract."""
    del session
    policy = os.environ.get("GLUDD_PANTS_UNIT_POLICY")
    if policy is not None and policy != "v1":
        raise RuntimeError("GLUDD_PANTS_UNIT_POLICY must equal v1")
