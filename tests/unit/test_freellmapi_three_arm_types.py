"""Contract tests for the cohesive three-arm replay value types."""

from general_ludd.models.freellmapi.three_arm_delta import (
    ArmComparison as ExportedArmComparison,
)
from general_ludd.models.freellmapi.three_arm_delta import (
    ReplayObservation as ExportedReplayObservation,
)
from general_ludd.models.freellmapi.three_arm_delta import (
    ReplayResources as ExportedReplayResources,
)
from general_ludd.models.freellmapi.three_arm_delta import (
    ThreeArmDecision as ExportedThreeArmDecision,
)
from general_ludd.models.freellmapi.three_arm_types import (
    ArmComparison,
    ReplayObservation,
    ReplayResources,
    ThreeArmDecision,
)


def test_three_arm_delta_preserves_public_value_type_identity() -> None:
    """Moving passive contracts must not fork their public runtime identities."""
    assert ExportedReplayObservation is ReplayObservation
    assert ExportedReplayResources is ReplayResources
    assert ExportedArmComparison is ArmComparison
    assert ExportedThreeArmDecision is ThreeArmDecision

