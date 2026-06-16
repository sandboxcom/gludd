"""Unit tests for the /proc + /sys metrics connector (injected reader)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from general_ludd.connectors.proc_sys import ProcSysSource


@dataclass
class FakeReader:
    """Canned reader mapping normalized path -> file contents."""

    files: dict[str, str] = field(default_factory=dict)
    reads: list[str] = field(default_factory=list)

    def __call__(self, path: str) -> str:
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]


MEMINFO = "MemTotal:       16384000 kB\nMemFree:         1024000 kB\n"
LOADAVG = "0.50 0.75 1.00 2/345 6789\n"
STAT = "cpu  100 0 200 3000 50 0 10\ncpu0 50 0 100 1500 25 0 5\nctxt 99999\n"
PRESSURE = "some avg10=1.20 avg60=0.80 avg300=0.10 total=12345\n"


def test_kind_and_name() -> None:
    src = ProcSysSource(config={"name": "host-procsys"}, reader=FakeReader())
    assert src.KIND == "metrics"
    assert src.name == "host-procsys"


def test_meminfo_normalized() -> None:
    reader = FakeReader(files={"/proc/meminfo": MEMINFO})
    src = ProcSysSource(reader=reader)
    records = src.query({"select": "meminfo"})

    assert reader.reads == ["/proc/meminfo"]
    by_msg = {r["message"]: r for r in records}
    assert by_msg["MemTotal"]["value"] == 16384000
    assert by_msg["MemTotal"]["labels"]["unit"] == "kB"
    assert by_msg["MemTotal"]["labels"]["path"] == "/proc/meminfo"
    assert by_msg["MemTotal"]["labels"]["select"] == "meminfo"
    assert by_msg["MemTotal"]["kind"] == "metrics"
    assert by_msg["MemTotal"]["level_or_status"] == "ok"
    assert by_msg["MemTotal"]["source"] == "proc_sys"


def test_loadavg_normalized() -> None:
    reader = FakeReader(files={"/proc/loadavg": LOADAVG})
    src = ProcSysSource(reader=reader)
    records = src.query({"select": "loadavg"})
    by_msg = {r["message"]: r["value"] for r in records}
    assert by_msg["load1"] == 0.50
    assert by_msg["load5"] == 0.75
    assert by_msg["load15"] == 1.00


def test_stat_cpu_breakdown() -> None:
    reader = FakeReader(files={"/proc/stat": STAT})
    src = ProcSysSource(reader=reader)
    records = src.query({"select": "stat"})
    by = {(r["message"], r["labels"].get("cpu")): r["value"] for r in records}
    assert by[("cpu.user", "cpu")] == 100
    assert by[("cpu.idle", "cpu")] == 3000
    assert by[("cpu.user", "cpu0")] == 50
    # non-cpu line still captured
    assert any(r["message"] == "ctxt" and r["value"] == 99999 for r in records)


def test_pressure_kv_labels() -> None:
    reader = FakeReader(files={"/proc/pressure/cpu": PRESSURE})
    src = ProcSysSource(reader=reader)
    records = src.query({"select": "pressure_cpu"})
    rec = records[0]
    assert rec["message"] == "some"
    assert rec["labels"]["avg10"] == "1.20"
    assert rec["labels"]["total"] == "12345"


@pytest.mark.parametrize(
    "bad_path",
    [
        "/etc/shadow",
        "/proc/../etc/passwd",
        "/sys/../root/.ssh/id_rsa",
        "proc/meminfo",  # relative
        "/var/log/syslog",
        "/proc/../../etc/hosts",
    ],
)
def test_path_outside_proc_sys_refused(bad_path: str) -> None:
    reader = FakeReader(files={bad_path: "secret"})
    src = ProcSysSource(reader=reader)
    with pytest.raises(ValueError, match="outside /proc,/sys"):
        src.query({"path": bad_path})
    # confinement check happens before the reader is ever called
    assert reader.reads == []


def test_confined_explicit_path_allowed() -> None:
    reader = FakeReader(files={"/sys/class/net/eth0/statistics/rx_bytes": "42\n"})
    src = ProcSysSource(reader=reader)
    records = src.query({"path": "/sys/class/net/eth0/statistics/rx_bytes"})
    assert records[0]["value"] == 42


def test_unknown_selector_rejected() -> None:
    src = ProcSysSource(reader=FakeReader())
    with pytest.raises(ValueError, match="unknown proc_sys selector"):
        src.query({"select": "does_not_exist"})


def test_health_ok() -> None:
    reader = FakeReader(files={"/proc/loadavg": LOADAVG})
    src = ProcSysSource(reader=reader)
    health = src.health()
    assert health["ok"] is True
    assert reader.reads == ["/proc/loadavg"]


def test_health_not_ok_when_reader_raises() -> None:
    reader = FakeReader(files={})  # loadavg missing -> FileNotFoundError
    src = ProcSysSource(reader=reader)
    health = src.health()
    assert health["ok"] is False
    assert "FileNotFoundError" in health["detail"]


def test_health_never_raises() -> None:
    class Boom:
        def __call__(self, path: str) -> str:
            raise PermissionError("denied")

    src = ProcSysSource(reader=Boom())
    health = src.health()
    assert health["ok"] is False
    assert "PermissionError" in health["detail"]
