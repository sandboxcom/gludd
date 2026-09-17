#!/usr/bin/env python3
"""Print the provider-neutral local accelerator inventory with live progress."""

from __future__ import annotations

import json
import sys

from general_ludd.hardware.accelerator_discovery import (
    DiscoveryTrace,
    HardwareDiscovery,
)


def _trace(trace: DiscoveryTrace) -> None:
    print(
        "GLUDD_ACCELERATOR_DISCOVERY "
        f"event={trace.event.value} source={trace.source} "
        f"count={trace.discovered_count}",
        file=sys.stderr,
        flush=True,
    )


def main() -> int:
    """Discover local resources without provisioning and print exact JSON."""
    inventory = HardwareDiscovery(trace_sink=_trace).discover_local()
    print(json.dumps(inventory.to_dict(), sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
