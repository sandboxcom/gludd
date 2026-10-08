"""Path-safe replay run identifiers shared by schemas and storage."""

from __future__ import annotations

import re
from typing import Annotated, Final

from pydantic import ConfigDict, TypeAdapter
from pydantic.functional_validators import AfterValidator

_RUN_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z"
)
_WINDOWS_RESERVED_STEMS: Final[frozenset[str]] = frozenset(
    {
        "AUX",
        "CON",
        "NUL",
        "PRN",
        *(f"COM{number}" for number in range(1, 10)),
        *(f"LPT{number}" for number in range(1, 10)),
    }
)


def _validate_run_id(value: str) -> str:
    """Reject traversal, Unicode ambiguity, and reserved device names."""
    if not value.isascii() or _RUN_ID_RE.fullmatch(value) is None:
        raise ValueError(
            "run_id must be 1-128 ASCII characters matching "
            "[A-Za-z0-9][A-Za-z0-9._-]*"
        )
    if ".." in value or value.endswith("."):
        raise ValueError("run_id must not contain traversal tokens or a trailing dot")
    if value.split(".", maxsplit=1)[0].upper() in _WINDOWS_RESERVED_STEMS:
        raise ValueError("run_id must not use a reserved device name")
    return value


SafeRunId = Annotated[str, AfterValidator(_validate_run_id)]
_RUN_ID_ADAPTER = TypeAdapter(SafeRunId, config=ConfigDict(strict=True))


def validate_run_id(value: str) -> str:
    """Return a path-safe run identifier or raise ``ValidationError``."""
    return _RUN_ID_ADAPTER.validate_python(value, strict=True)


__all__ = ["SafeRunId", "validate_run_id"]
