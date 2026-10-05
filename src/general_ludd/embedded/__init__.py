"""Embedded-systems workload contracts."""

from general_ludd.embedded.firmware import (
    ArduinoFirmwareAdapter,
    ArduinoFirmwareWorkload,
    ArduinoToolRunner,
    BoardProfile,
    CommandEvidence,
    FirmwareCandidate,
    FirmwareRequest,
    FirmwareResult,
    FirmwareStatus,
    FirmwareToolchain,
    ModelRoute,
    SubprocessArduinoToolchain,
    supported_board,
)

__all__ = [
    "ArduinoFirmwareAdapter",
    "ArduinoFirmwareWorkload",
    "ArduinoToolRunner",
    "BoardProfile",
    "CommandEvidence",
    "FirmwareCandidate",
    "FirmwareRequest",
    "FirmwareResult",
    "FirmwareStatus",
    "FirmwareToolchain",
    "ModelRoute",
    "SubprocessArduinoToolchain",
    "supported_board",
]
