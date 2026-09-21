"""Architecture contracts for universally reusable FreeLLMAPI scoring."""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path


def test_freellmapi_scoring_has_one_models_owned_canonical_module() -> None:
    """Model scoring belongs to the model layer, not to one task consumer."""
    module_name = "general_ludd.models.freellmapi_scoring_kernel"
    assert importlib.util.find_spec(module_name) is not None

    canonical = importlib.import_module(module_name)
    legacy = importlib.import_module(
        "general_ludd.self_improve.freellmapi_scoring_kernel"
    )

    assert legacy.FreeLLMScoringKernel is canonical.FreeLLMScoringKernel
    assert legacy.FreeLLMScoringInput is canonical.FreeLLMScoringInput
    assert legacy.FreeLLMScoringFactors is canonical.FreeLLMScoringFactors
    assert canonical.__file__ is not None
    module_path = Path(canonical.__file__)
    assert module_path.parent.name == "models"
    assert (
        module_path.parent / "vendor" / "freellmapi"
    ) == canonical._VENDOR_ROOT


def test_models_owned_kernel_does_not_import_self_improvement() -> None:
    """Universal model infrastructure must not depend on task specialization."""
    module_path = (
        Path(__file__).resolve().parents[2]
        / "src/general_ludd/models/freellmapi_scoring_kernel.py"
    )

    assert module_path.is_file()
    assert "general_ludd.self_improve" not in module_path.read_text(encoding="utf-8")
