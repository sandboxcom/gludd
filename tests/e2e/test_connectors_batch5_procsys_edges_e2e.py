"""Deterministic E2E coverage for ProcSys selector parser branches.

The batch-5 workflow exercises explicit-path reads.  These cases drive the
public selector API for load average, CPU statistics, network devices, and
disk statistics without touching the host filesystem.
"""

from __future__ import annotations

import re
from typing import Any


def _source(fixtures: dict[str, str]):
    from general_ludd.connectors.proc_sys import ProcSysSource

    def reader(path: str) -> str:
        # The source realpath()s selectors (resolving /proc/self → the
        # caller's PID); normalize both forms back to the fixture's
        # canonical /proc/... key so the canned reads stay deterministic.
        normalized = re.sub(r"/proc/(?:self|\d+)/", "/proc/", path)
        return fixtures[normalized]

    return ProcSysSource({"name": "proc-sys-e2e"}, reader=reader)


def test_selector_parsers_emit_normalized_records() -> None:
    source = _source(
        {
            "/proc/loadavg": "0.12 0.34 0.56 1/100 1234\n",
            "/proc/stat": "cpu 10 2 3 40 0 0 1\nintr 99\n",
            "/proc/net/dev": "eth0: 10 2 0 1 0 0 0 0 20 3 0 4 0 0 0 0\n",
            "/proc/diskstats": "8 0 sda 7 1 2 3 4 5 6 7\n",
        }
    )

    load = source.query({"select": "loadavg"})
    assert [record["message"] for record in load] == ["load1", "load5", "load15"]
    assert [record["value"] for record in load] == [0.12, 0.34, 0.56]

    stat = source.query({"select": "stat"})
    assert any(record["message"] == "cpu.user" and record["value"] == 10 for record in stat)
    assert any(record["message"] == "intr" and record["value"] == 99 for record in stat)

    net = source.query({"select": "net_dev"})
    assert {record["labels"]["iface"] for record in net} == {"eth0"}
    assert any(record["message"] == "rx_bytes" and record["value"] == 10 for record in net)
    assert any(record["message"] == "tx_bytes" and record["value"] == 20 for record in net)

    disk = source.query({"select": "diskstats"})
    assert {record["labels"]["device"] for record in disk} == {"sda"}
    assert any(record["message"] == "reads" and record["value"] == 7 for record in disk)


def test_selector_keyed_and_unknown_paths_are_explicit() -> None:
    source = _source({"/proc/meminfo": "MemTotal: 1024 kB\nHugePages_Total: 2\n"})

    records: list[dict[str, Any]] = source.query({"select": "meminfo"})
    assert records[0]["message"] == "MemTotal"
    assert records[0]["value"] == 1024
    assert records[0]["labels"]["unit"] == "kB"
    assert records[1]["message"] == "HugePages_Total"
    assert records[1]["value"] == 2
