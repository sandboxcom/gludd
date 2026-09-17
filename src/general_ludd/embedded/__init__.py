"""Embedded-systems workload contracts."""

from general_ludd.embedded.firmware import (
    ArduinoFirmwareWorkload,
    BoardProfile,
    CommandEvidence,
    FirmwareRequest,
    FirmwareResult,
    FirmwareStatus,
    FirmwareToolchain,
    ModelRoute,
    SubprocessArduinoToolchain,
    supported_board,
)

__all__ = [
    "ArduinoFirmwareWorkload",
    "BoardProfile",
    "CommandEvidence",
    "FirmwareRequest",
    "FirmwareResult",
    "FirmwareStatus",
    "FirmwareToolchain",
    "ModelRoute",
    "SubprocessArduinoToolchain",
    "supported_board",
]
