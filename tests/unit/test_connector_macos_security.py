"""Unit tests for MacOSSecuritySource connector (injected runner pattern)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from general_ludd.connectors.macos_security import MacOSSecuritySource


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


# ── kind / name ──────────────────────────────────────────────────────────


def test_kind_and_name() -> None:
    src = MacOSSecuritySource(config={"name": "macos-sec"}, runner=_make_runner())
    assert src.KIND == "logs"
    assert src.name == "macos-sec"


def test_name_defaults() -> None:
    assert MacOSSecuritySource().name == "macos_security"


# ── health ───────────────────────────────────────────────────────────────


def test_health_ok_on_csrutil_status() -> None:
    runner = _make_runner(
        stdout="System Integrity Protection status: enabled."
    )
    src = MacOSSecuritySource(runner=runner)
    health = src.health()
    assert health["ok"] is True


def test_health_not_ok_on_nonzero() -> None:
    runner = _make_runner(rc=1, stderr="permission denied")
    src = MacOSSecuritySource(runner=runner)
    health = src.health()
    assert health["ok"] is False
    assert "permission denied" in health["detail"]


def test_health_never_raises() -> None:
    class Boom:
        def __call__(self, argv: list[str]) -> tuple[int, str, str]:
            raise OSError("no binary")

    src = MacOSSecuritySource(runner=Boom())
    health = src.health()
    assert health["ok"] is False
    assert "OSError" in health["detail"]


# ── csrutil / SIP ────────────────────────────────────────────────────────


CSRUTIL_OUTPUT = """\
System Integrity Protection status: enabled.
Apple Internal: disabled
Kext Signing: enabled
Filesystem Protections: enabled
Debugging Restrictions: enabled
DTrace Restrictions: enabled
NVRAM Protections: enabled
BaseSystem Verification: enabled
"""


def test_query_csrutil() -> None:
    runner = _make_runner(stdout=CSRUTIL_OUTPUT)
    src = MacOSSecuritySource(config={"name": "macos-sec"}, runner=runner)
    records = src.query({"target": "csrutil"})
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "logs"
    assert rec["source"] == "macos-sec"
    assert rec["level_or_status"] == "enabled"
    assert rec["message"] == "SIP: enabled"
    assert rec["value"] == "enabled"
    assert rec["labels"]["Apple Internal"] == "disabled"
    assert rec["raw"]["command"] == "csrutil status"
    assert runner.calls == [["csrutil", "status"]]


def test_query_sip() -> None:
    runner = _make_runner(stdout=CSRUTIL_OUTPUT)
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "sip"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "enabled"


# ── spctl / Gatekeeper ───────────────────────────────────────────────────


def test_query_spctl() -> None:
    runner = _make_runner(stdout="assessments enabled")
    src = MacOSSecuritySource(config={"name": "spctl-src"}, runner=runner)
    records = src.query({"target": "spctl"})
    assert len(records) == 1
    rec = records[0]
    assert rec["level_or_status"] == "enabled"
    assert "assessments enabled" in rec["message"]
    assert rec["labels"]["gatekeeper"] == "enabled"
    assert runner.calls == [["spctl", "--status"]]


def test_query_gatekeeper() -> None:
    runner = _make_runner(stdout="assessments disabled")
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "gatekeeper"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "disabled"


# ── xprotect ─────────────────────────────────────────────────────────────


def test_query_xprotect() -> None:
    runner = _make_runner(stdout="1700000000")
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "xprotect"})
    assert len(records) == 1
    rec = records[0]
    assert rec["level_or_status"] == "present"
    assert "XProtect.meta.plist mtime" in rec["message"]
    assert rec["value"] == 1700000000.0


# ── tccutil ──────────────────────────────────────────────────────────────


TCC_OUTPUT = """\
kTCCServiceCamera:
    com.apple.Terminal
    com.google.Chrome
"""


def test_query_tcc() -> None:
    runner = _make_runner(stdout=TCC_OUTPUT)
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "tcc", "service": "Camera"})
    assert len(records) == 2
    assert records[0]["labels"]["service"] == "kTCCServiceCamera"
    assert runner.calls == [["tccutil", "list", "Camera"]]


# ── injection rejection ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        ";",
        "|",
        "$(cmd)",
        "-x",
        "`id`",
    ],
)
def test_injection_rejected(bad: str) -> None:
    src = MacOSSecuritySource(runner=_make_runner())
    with pytest.raises(ValueError):
        src.query({"target": bad})


@pytest.mark.parametrize(
    "bad_service",
    [
        "Camera; rm -rf /",
        "-",
        "",
        "|nc",
        "`id`",
        "$(whoami)",
    ],
)
def test_injection_rejected_service(bad_service: str) -> None:
    src = MacOSSecuritySource(runner=_make_runner())
    with pytest.raises(ValueError):
        src.query({"target": "tcc", "service": bad_service})


# ── unknown target ───────────────────────────────────────────────────────


def test_unknown_target_returns_empty() -> None:
    src = MacOSSecuritySource(runner=_make_runner())
    records = src.query({"target": "nonexistent"})
    assert records == []


# ── argv is always a list ────────────────────────────────────────────────


def test_no_shell_single_argv_elements() -> None:
    runner = _make_runner(stdout=CSRUTIL_OUTPUT)
    src = MacOSSecuritySource(runner=runner)
    src.query({"target": "csrutil"})
    argv = runner.calls[0]
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    for a in argv:
        assert " " not in a


# ── normalized record shape ──────────────────────────────────────────────


REQUIRED_KEYS = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}


def test_query_returns_normalized_shape() -> None:
    runner = _make_runner(stdout=CSRUTIL_OUTPUT)
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "csrutil"})
    assert len(records) == 1
    rec = records[0]
    assert set(rec.keys()) == REQUIRED_KEYS
    assert isinstance(rec["ts"], float)
    assert isinstance(rec["labels"], dict)
    assert isinstance(rec["raw"], dict)


def test_xprotect_record_has_normalized_shape() -> None:
    runner = _make_runner(stdout="1700000000")
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "xprotect"})
    assert len(records) == 1
    assert REQUIRED_KEYS.issubset(set(records[0].keys()))


def test_spctl_record_has_normalized_shape() -> None:
    runner = _make_runner(stdout="assessments enabled")
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "spctl"})
    assert REQUIRED_KEYS.issubset(set(records[0].keys()))


# ── edge cases ───────────────────────────────────────────────────────────


def test_csrutil_nonzero_returns_empty() -> None:
    runner = _make_runner(rc=1, stderr="csrutil: must run as root")
    src = MacOSSecuritySource(runner=runner)
    assert src.query({"target": "csrutil"}) == []


def test_spctl_nonzero_returns_empty() -> None:
    runner = _make_runner(rc=1)
    src = MacOSSecuritySource(runner=runner)
    assert src.query({"target": "spctl"}) == []


def test_xprotect_stat_zero_returns_unknown() -> None:
    runner = _make_runner(rc=1, stdout="")
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "xprotect"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "unknown"


def test_xprotect_stat_fails_falls_back_to_softwareupdate() -> None:
    runner = _make_runner(rc=1, stdout="")
    src = MacOSSecuritySource(runner=runner)
    records = src.query({"target": "xprotect"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "unknown"
