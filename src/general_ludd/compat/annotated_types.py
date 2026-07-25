"""Runtime compatibility for annotated-types grouped metadata checks.

Python 3.14 can make ``typing.runtime_checkable`` protocol checks drift after
other runtime protocols are imported. Pydantic uses
``isinstance(annotation, annotated_types.GroupedMetadata)`` while building
models, so a false positive for scalar metadata such as ``Ge`` or ``MinLen``
turns into ``TypeError: object is not iterable`` during import.  Replace the
protocol object with a narrow runtime sentinel check while keeping the public
``annotated_types`` classes untouched.
"""

from __future__ import annotations

from typing import Any

_PATCH_MARKER = "__general_ludd_safe_grouped_metadata__"


class _SafeGroupedMetadataMeta(type):
    def __instancecheck__(cls, instance: object) -> bool:
        marker = getattr(instance, "__is_annotated_types_grouped_metadata__", False)
        iterator = getattr(instance, "__iter__", None)
        return marker is True and callable(iterator)


class _SafeGroupedMetadata(metaclass=_SafeGroupedMetadataMeta):
    __general_ludd_safe_grouped_metadata__ = True


def apply_annotated_types_runtime_patch() -> None:
    """Make annotated-types grouped metadata checks stable under Python 3.14.

    The patch is deliberately narrow: grouped metadata must expose the sentinel
    documented by annotated-types and be iterable. Scalar BaseMetadata values do
    not satisfy that contract, so Pydantic no longer tries to expand them.
    """
    try:
        import annotated_types as at
    except Exception:
        return

    grouped: Any = getattr(at, "GroupedMetadata", None)
    if getattr(grouped, _PATCH_MARKER, False):
        return
    vars(at)["GroupedMetadata"] = _SafeGroupedMetadata
