"""Unit contract for structured polymer candidates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from general_ludd.chemistry.polymer_design import PolymerCandidate


def test_polymer_candidate_forbids_model_scaffolding_fields() -> None:
    with pytest.raises(ValidationError):
        PolymerCandidate.model_validate(
            {
                "candidate_id": "draft",
                "status": "TODO",
                "monomers": ["glucose"],
                "repeat_unit": "repeat",
                "properties": [],
                "synthesis_scale": "lab",
                "facility_controls": [],
                "validation": {},
                "provenance": {},
            }
        )
