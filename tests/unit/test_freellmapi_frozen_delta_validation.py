"""Architecture contracts for frozen-delta candidate and plan validation."""

from __future__ import annotations

from pathlib import Path

import general_ludd.models.freellmapi_frozen_delta_validation as validation


def test_validation_stays_in_universal_model_infrastructure() -> None:
    source = Path(validation.__file__).read_text(encoding="utf-8")

    assert "general_ludd.self_improve" not in source
    assert callable(validation.validate_candidate)
    assert callable(validation.validate_plan)
    assert callable(validation.abi_compatible)
