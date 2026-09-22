"""Architecture contracts for frozen-delta value validation."""

from __future__ import annotations

from pathlib import Path

import general_ludd.models.freellmapi_frozen_delta_contracts as contracts


def test_contracts_stay_in_universal_model_infrastructure() -> None:
    source = Path(contracts.__file__).read_text(encoding="utf-8")

    assert "general_ludd.self_improve" not in source
    assert contracts.FREELLMAPI_FROZEN_DELTA_SCHEMA_VERSION == 1

