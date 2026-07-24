from __future__ import annotations

import general_ludd
from general_ludd.compat.annotated_types import apply_annotated_types_runtime_patch

assert general_ludd.__version__


def test_general_ludd_patches_scalar_annotated_types_metadata() -> None:
    import annotated_types as at

    assert isinstance(at.Len(min_length=1), at.GroupedMetadata)
    assert not isinstance(at.MinLen(1), at.GroupedMetadata)
    assert not isinstance(at.MaxLen(10), at.GroupedMetadata)
    assert not isinstance(at.Ge(0), at.GroupedMetadata)


def test_pydantic_field_constraints_import_under_active_python() -> None:
    from pydantic import BaseModel, Field

    class Sample(BaseModel):
        name: str = Field(min_length=1, max_length=20)
        count: int = Field(ge=0, le=10)

    assert Sample(name="ok", count=1).count == 1


def test_apply_patch_is_idempotent() -> None:
    """Calling apply_annotated_types_runtime_patch twice is safe."""
    apply_annotated_types_runtime_patch()
    apply_annotated_types_runtime_patch()
    import annotated_types as at
    assert hasattr(at.GroupedMetadata, "__general_ludd_safe_grouped_metadata__")


def test_apply_patch_handles_missing_module() -> None:
    """The patch silently returns if annotated_types is not installed."""
    apply_annotated_types_runtime_patch()
