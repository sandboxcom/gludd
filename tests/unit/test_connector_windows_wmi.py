"""Unit tests for WinWmiConnector (injected runner pattern)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from general_ludd.connectors.windows_wmi import WinWmiConnector


@dataclass
class FakeRunner:
    rc: int = 0
    stdout: str = ""
    stderr: str = ""
    calls: list[list[str]] = field(default_factory=list)

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        return (self.rc, self.stdout, self.stderr)


def _make_runner(rc: int = 0, stdout: str = "", stderr: str = "") -> FakeRunner:
    return FakeRunner(rc=rc, stdout=stdout, stderr=stderr)


# -- kind / name --


def test_kind_and_name() -> None:
    src = WinWmiConnector(config={"name": "wmi-src"}, runner=_make_runner())
    assert src.KIND == "metrics"
    assert src.name == "wmi-src"


def test_name_defaults() -> None:
    assert WinWmiConnector().name == "windows_wmi"


# -- health --


def test_health_ok() -> None:
    runner = _make_runner(
        stdout='[{"Caption": "Microsoft Windows Server 2022 Standard", "Version": "10.0.20348"}]'
    )
    src = WinWmiConnector(runner=runner)
    health = src.health()
    assert health["ok"] is True
    assert "Windows Server" in health["detail"]


def test_health_not_ok_on_nonzero() -> None:
    runner = _make_runner(rc=1, stderr="permission denied")
    src = WinWmiConnector(runner=runner)
    health = src.health()
    assert health["ok"] is False
    assert "permission denied" in health["detail"]


def test_health_never_raises() -> None:
    class Boom:
        def __call__(self, argv: list[str]) -> tuple[int, str, str]:
            raise OSError("powershell not found")

    src = WinWmiConnector(runner=Boom())
    health = src.health()
    assert health["ok"] is False
    assert "OSError" in health["detail"]


# -- query: os / operating_system --


OS_JSON = """\
[{"Caption": "Microsoft Windows Server 2022 Standard", "Version": "10.0.20348",
  "OSArchitecture": "64-bit", "BuildNumber": "20348",
  "Manufacturer": "Microsoft Corporation"}]\
"""


def test_query_os() -> None:
    runner = _make_runner(stdout=OS_JSON)
    src = WinWmiConnector(config={"name": "wmi-os"}, runner=runner)
    records = src.query({"target": "os"})
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "metrics"
    assert rec["source"] == "wmi-os"
    assert rec["level_or_status"] == "ok"
    assert "Windows Server 2022 Standard" in rec["message"]
    assert rec["labels"]["wmi_class"] == "Win32_OperatingSystem"
    assert rec["labels"]["osarchitecture"] == "64-bit"
    assert "Get-CimInstance" in rec["raw"]["command"]


def test_query_operating_system() -> None:
    runner = _make_runner(stdout=OS_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "operating_system"})
    assert len(records) == 1
    assert records[0]["labels"]["wmi_class"] == "Win32_OperatingSystem"


# -- query: cpu --


CPU_JSON = """\
[{"Caption": "Intel64 Family 6 Model 85 Stepping 4", "DeviceID": "CPU0",
  "Name": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
  "NumberOfCores": 4, "NumberOfLogicalProcessors": 8,
  "MaxClockSpeed": 2500, "CurrentClockSpeed": 2500,
  "SocketDesignation": "CPU 1", "Manufacturer": "GenuineIntel"},
 {"Caption": "Intel64 Family 6 Model 85 Stepping 4", "DeviceID": "CPU1",
  "Name": "Intel(R) Xeon(R) Platinum 8259CL CPU @ 2.50GHz",
  "NumberOfCores": 4, "NumberOfLogicalProcessors": 8,
  "MaxClockSpeed": 2500, "CurrentClockSpeed": 2500,
  "SocketDesignation": "CPU 2", "Manufacturer": "GenuineIntel"}]\
"""


def test_query_cpu() -> None:
    runner = _make_runner(stdout=CPU_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "cpu"})
    assert len(records) == 2
    assert all(r["level_or_status"] == "ok" for r in records)
    assert records[0]["labels"]["wmi_class"] == "Win32_Processor"
    assert records[0]["labels"]["numberofcores"] == 4
    assert records[1]["labels"]["numberofcores"] == 4


# -- query: bios --


BIOS_JSON = """\
[{"Manufacturer": "American Megatrends Inc.",
  "Name": "BIOS Date: 10/15/21 14:22:33 Ver: 3.6",
  "SMBIOSBIOSVersion": "3.6",
  "ReleaseDate": "20211015000000.000000+000",
  "SerialNumber": "1234-5678-ABCD-EFGH",
  "Version": "3.6"}]\
"""


def test_query_bios() -> None:
    runner = _make_runner(stdout=BIOS_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "bios"})
    assert len(records) == 1
    rec = records[0]
    assert rec["labels"]["wmi_class"] == "Win32_BIOS"
    assert rec["labels"]["manufacturer"] == "American Megatrends Inc."
    assert rec["labels"]["smbiosbiosversion"] == "3.6"
    assert rec["level_or_status"] == "ok"


# -- query: disk --


DISK_JSON = """\
[{"DeviceID": "C:", "FileSystem": "NTFS", "Size": 128849018880,
  "FreeSpace": 64424509440, "VolumeName": "System"},
 {"DeviceID": "D:", "FileSystem": "NTFS", "Size": 536870912000,
  "FreeSpace": 268435456000, "VolumeName": "Data"}]\
"""


def test_query_disk() -> None:
    runner = _make_runner(stdout=DISK_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "disk"})
    assert len(records) == 2
    assert all(r["level_or_status"] == "ok" for r in records)
    assert records[0]["labels"]["filesystem"] == "NTFS"
    assert records[0]["value"] == 128849018880.0
    assert "C:" in records[0]["message"]
    assert "D:" in records[1]["message"]


# -- query: memory --


MEMORY_JSON = """\
[{"Capacity": 17179869184, "Speed": 3200, "Manufacturer": "Samsung",
  "PartNumber": "M393A4K40DB3-CWE", "MemoryType": 0, "FormFactor": 8,
  "DeviceID": "DIMM1"},
 {"Capacity": 17179869184, "Speed": 3200, "Manufacturer": "Samsung",
  "PartNumber": "M393A4K40DB3-CWE", "MemoryType": 0, "FormFactor": 8,
  "DeviceID": "DIMM2"}]\
"""


def test_query_memory() -> None:
    runner = _make_runner(stdout=MEMORY_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "memory"})
    assert len(records) == 2
    assert records[0]["labels"]["wmi_class"] == "Win32_PhysicalMemory"
    assert records[0]["value"] == 17179869184.0
    assert records[0]["labels"]["manufacturer"] == "Samsung"
    assert records[0]["labels"]["partnumber"] == "M393A4K40DB3-CWE"


# -- query: network --


NETWORK_JSON = """\
[{"Name": "Intel(R) 82599 10 Gigabit Dual Port",
  "NetConnectionID": "Ethernet 2", "MACAddress": "00:15:5D:01:02:03",
  "AdapterType": "Ethernet 802.3", "NetConnectionStatus": 2,
  "NetEnabled": true, "DeviceID": "1"},
 {"Name": "Microsoft Hyper-V Network Adapter",
  "NetConnectionID": "Ethernet", "MACAddress": "00:15:5D:01:02:04",
  "AdapterType": "Ethernet 802.3", "NetConnectionStatus": 2,
  "NetEnabled": true, "DeviceID": "2"}]\
"""


def test_query_network() -> None:
    runner = _make_runner(stdout=NETWORK_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "network"})
    assert len(records) == 2
    assert "Win32_NetworkAdapter" in records[0]["labels"]["wmi_class"]
    assert records[0]["labels"]["macaddress"] == "00:15:5D:01:02:03"
    assert records[0]["labels"]["netconnectionstatus"] == 2
    assert records[0]["labels"]["adaptertype"] == "Ethernet 802.3"


# -- unknown target falls back to OS --


def test_unknown_target_falls_back_to_os() -> None:
    runner = _make_runner(stdout=OS_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "nonexistent"})
    assert len(records) == 1
    assert records[0]["labels"]["wmi_class"] == "Win32_OperatingSystem"
    assert records[0]["level_or_status"] == "ok"


# -- nonzero returns error record --


def test_query_nonzero_returns_error_record() -> None:
    runner = _make_runner(rc=1, stderr="access denied")
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "os"})
    assert len(records) == 1
    rec = records[0]
    assert rec["level_or_status"] == "error"
    assert rec["raw"]["exit_code"] == 1
    assert "WMI query failed" in rec["message"]


# -- invalid json returns error record --


def test_query_invalid_json_returns_error_record() -> None:
    runner = _make_runner(stdout="not valid json {{{")
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "os"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "error"
    assert "JSON" in records[0]["message"]


# -- injection rejection --


@pytest.mark.parametrize("bad", [";", "|", "$(cmd)", "-x", "`id`"])
def test_injection_rejected(bad: str) -> None:
    src = WinWmiConnector(runner=_make_runner())
    with pytest.raises(ValueError):
        src.query({"target": bad})


# -- no shell single argv elements --


def test_no_shell_single_argv_elements() -> None:
    runner = _make_runner(stdout=OS_JSON)
    src = WinWmiConnector(runner=runner)
    src.query({"target": "os"})
    argv = runner.calls[0]
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    # Fixed prefix arguments (powershell, -NoProfile, -NonInteractive, -Command)
    # must be single tokens with no spaces; the last element is the PowerShell
    # command string which naturally contains spaces.
    for a in argv[:-1]:
        assert " " not in a


# -- normalized record shape --


REQUIRED_KEYS = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}


def test_query_returns_normalized_shape() -> None:
    runner = _make_runner(stdout=OS_JSON)
    src = WinWmiConnector(runner=runner)
    records = src.query({"target": "os"})
    assert len(records) == 1
    rec = records[0]
    assert REQUIRED_KEYS.issubset(set(rec.keys()))
    assert isinstance(rec["ts"], float)
    assert isinstance(rec["labels"], dict)
    assert isinstance(rec["raw"], dict)
