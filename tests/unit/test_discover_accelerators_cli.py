from __future__ import annotations

import json

from general_ludd.hardware.accelerator_discovery import (
    AcceleratorInventory,
    DiscoveryEvent,
    DiscoveryTrace,
)


def test_cli_prints_inventory_and_content_free_progress(monkeypatch, capsys) -> None:
    from scripts import discover_accelerators as subject

    class FakeDiscovery:
        def __init__(self, *, trace_sink) -> None:
            self._trace_sink = trace_sink

        def discover_local(self) -> AcceleratorInventory:
            self._trace_sink(
                DiscoveryTrace(DiscoveryEvent.DISCOVERY_STARTED, "local")
            )
            self._trace_sink(
                DiscoveryTrace(DiscoveryEvent.DISCOVERY_COMPLETED, "local", 0)
            )
            return AcceleratorInventory()

    monkeypatch.setattr(subject, "HardwareDiscovery", FakeDiscovery)

    assert subject.main() == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "available_count": 0,
        "resources": [],
        "schema_version": 1,
        "total_count": 0,
    }
    assert captured.err.splitlines() == [
        "GLUDD_ACCELERATOR_DISCOVERY event=discovery_started source=local count=0",
        "GLUDD_ACCELERATOR_DISCOVERY event=discovery_completed source=local count=0",
    ]
    assert "model" not in captured.err
