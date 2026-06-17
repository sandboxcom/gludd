"""Shared Pydantic field-validator callables for general_ludd schemas."""
from __future__ import annotations


def strip_nonempty(v: str) -> str:
    """Strip whitespace and reject empty strings."""
    if isinstance(v, str):
        v = v.strip()
    if not v:
        raise ValueError("field must not be empty")
    return v


def non_negative_float(v: float) -> float:
    """Reject negative floats."""
    if v < 0:
        raise ValueError("must be non-negative")
    return v


def non_negative_int(v: int) -> int:
    """Reject negative integers."""
    if v < 0:
        raise ValueError("must be non-negative")
    return v


def percent_range(v: float) -> float:
    """Reject values outside [0.0, 100.0]."""
    if not (0.0 <= v <= 100.0):
        raise ValueError("coverage percent must be between 0.0 and 100.0")
    return v
