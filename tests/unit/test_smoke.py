from __future__ import annotations

from general_ludd.smoke import list_smoke_tests


def test_smoke_module_registers_multi_model_smokes() -> None:
    registered = {(item["provider"], item["test"]) for item in list_smoke_tests()}

    assert ("multi-provider", "model-juggle") in registered
    assert ("multi-platform", "model-juggle") in registered
