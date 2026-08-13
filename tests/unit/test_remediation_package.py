"""Coverage for the remediation package's lazy public exports."""

from __future__ import annotations

import pytest

import general_ludd.remediation as remediation


def test_lazy_remediation_action_model_export() -> None:
    assert remediation.RemediationActionModel.__name__ == "RemediationActionModel"


def test_unknown_remediation_export_raises_attribute_error() -> None:
    with pytest.raises(AttributeError, match="unknown_export"):
        remediation.__getattr__("unknown_export")
