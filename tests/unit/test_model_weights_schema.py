"""Unit tests for general_ludd.model_weights.schema."""

import pytest
from pydantic import ValidationError

from general_ludd.model_weights import ModelRoleWeight, ModelWeightConfig
from general_ludd.schemas.benchmark import TaskRole

# ---------------------------------------------------------------------------
# ModelRoleWeight
# ---------------------------------------------------------------------------


def test_model_role_weight_valid() -> None:
    w = ModelRoleWeight(model_id="claude-sonnet-4-6", role=TaskRole.PLANNER, weight=0.8)
    assert w.model_id == "claude-sonnet-4-6"
    assert w.role == TaskRole.PLANNER
    assert w.weight == pytest.approx(0.8)


def test_model_role_weight_strips_whitespace() -> None:
    w = ModelRoleWeight(model_id="  claude-haiku  ", role=TaskRole.EDITOR, weight=0.5)
    assert w.model_id == "claude-haiku"


def test_model_role_weight_empty_model_id_rejected() -> None:
    with pytest.raises(ValidationError, match="model_id must not be empty"):
        ModelRoleWeight(model_id="   ", role=TaskRole.PLANNER, weight=0.5)


def test_model_role_weight_below_zero_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelRoleWeight(model_id="m", role=TaskRole.PLANNER, weight=-0.1)


def test_model_role_weight_above_one_rejected() -> None:
    with pytest.raises(ValidationError):
        ModelRoleWeight(model_id="m", role=TaskRole.PLANNER, weight=1.1)


def test_model_role_weight_boundary_values() -> None:
    # 0.0 and 1.0 are valid inclusive bounds
    ModelRoleWeight(model_id="m", role=TaskRole.COMPACTOR, weight=0.0)
    ModelRoleWeight(model_id="m", role=TaskRole.COMPACTOR, weight=1.0)


# ---------------------------------------------------------------------------
# ModelWeightConfig
# ---------------------------------------------------------------------------


def _sample_config() -> ModelWeightConfig:
    return ModelWeightConfig(
        entries=[
            ModelRoleWeight(model_id="claude-sonnet-4-6", role=TaskRole.PLANNER, weight=0.9),
            ModelRoleWeight(model_id="claude-sonnet-4-6", role=TaskRole.EDITOR, weight=0.7),
            ModelRoleWeight(model_id="claude-haiku-3-5", role=TaskRole.COMPACTOR, weight=0.6),
        ]
    )


def test_config_valid_sample() -> None:
    cfg = _sample_config()
    assert len(cfg.entries) == 3


def test_config_empty_entries_allowed() -> None:
    cfg = ModelWeightConfig()
    assert cfg.entries == []


def test_config_weight_for_known_pair() -> None:
    cfg = _sample_config()
    assert cfg.weight_for("claude-sonnet-4-6", TaskRole.PLANNER) == pytest.approx(0.9)
    assert cfg.weight_for("claude-haiku-3-5", TaskRole.COMPACTOR) == pytest.approx(0.6)


def test_config_weight_for_unknown_pair_returns_default() -> None:
    cfg = _sample_config()
    assert cfg.weight_for("unknown-model", TaskRole.ENUMERATOR) == pytest.approx(0.0)
    assert cfg.weight_for("unknown-model", TaskRole.ENUMERATOR, default=0.5) == pytest.approx(0.5)


def test_config_duplicate_pair_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate"):
        ModelWeightConfig(
            entries=[
                ModelRoleWeight(model_id="m", role=TaskRole.PLANNER, weight=0.8),
                ModelRoleWeight(model_id="m", role=TaskRole.PLANNER, weight=0.3),
            ]
        )


def test_config_duplicate_different_roles_allowed() -> None:
    # same model_id but different roles is fine
    cfg = ModelWeightConfig(
        entries=[
            ModelRoleWeight(model_id="m", role=TaskRole.PLANNER, weight=0.8),
            ModelRoleWeight(model_id="m", role=TaskRole.EDITOR, weight=0.5),
        ]
    )
    assert len(cfg.entries) == 2


def test_config_invalid_weight_propagates() -> None:
    with pytest.raises(ValidationError):
        ModelWeightConfig(
            entries=[
                ModelRoleWeight(model_id="m", role=TaskRole.PLANNER, weight=2.0),
            ]
        )
