from __future__ import annotations

import general_ludd

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
