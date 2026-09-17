"""Canonical module-boundary tests for :mod:`general_ludd.embedded.firmware`."""

from general_ludd.embedded import ArduinoFirmwareAdapter, ArduinoToolRunner
from general_ludd.embedded.firmware import FirmwareToolchain


def test_embedded_package_exports_the_universal_firmware_boundary() -> None:
    """Keep the canonical module test path aligned with the TDD guardrail."""
    assert ArduinoFirmwareAdapter.capability == "arduino-cpp"
    assert ArduinoFirmwareAdapter.required_tool == "arduino_toolchain"
    assert callable(ArduinoToolRunner.run)
    assert FirmwareToolchain.__module__ == "general_ludd.embedded.firmware"
