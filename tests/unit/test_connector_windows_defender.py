"""Unit tests for WindowsDefenderConnector (injected runner pattern)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from general_ludd.connectors.windows_defender import WindowsDefenderConnector


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


REQUIRED_KEYS = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}

# ── JSON fixtures ───────────────────────────────────────────────────────────

STATUS_JSON = (
    '[{"AntivirusEnabled": true, "AMServiceEnabled": true, '
    '"AntispywareEnabled": true, "RealTimeProtectionEnabled": true, '
    '"LastScan": "/Date(1718000000000)/"}]'
)

PREFERENCES_JSON = (
    '[{"DisableRealtimeMonitoring": false, "SubmitSamplesConsent": 1, '
    '"PUAProtection": 1}]'
)

THREATS_JSON = (
    '[{"ThreatName": "TestMalware", "SeverityName": "Severe", '
    '"StatusName": "Remediated"}, '
    '{"ThreatName": "TestMalware2", "SeverityName": "Moderate", '
    '"StatusName": "Active"}]'
)

EXCLUSIONS_JSON = (
    '[{"ExclusionPath": ["C:\\\\foo"], '
    '"ExclusionExtension": [".exe"], '
    '"ExclusionProcess": ["bar.exe"]}]'
)

SCAN_JSON = '[{"ScanId": 42, "ScanType": "QuickScan"}]'

HEALTH_JSON = (
    '[{"AntivirusEnabled": true, "AMServiceEnabled": true, '
    '"AntispywareEnabled": true, "RealTimeProtectionEnabled": true}]'
)


# ── kind / name ─────────────────────────────────────────────────────────────


def test_kind_and_name() -> None:
    src = WindowsDefenderConnector(
        config={"name": "defender-01"}, runner=_make_runner()
    )
    assert src.KIND == "logs"
    assert src.name == "defender-01"


def test_name_defaults() -> None:
    assert WindowsDefenderConnector().name == "windows_defender"


# ── health ──────────────────────────────────────────────────────────────────


def test_health_ok() -> None:
    runner = _make_runner(stdout=HEALTH_JSON)
    src = WindowsDefenderConnector(runner=runner)
    health = src.health()
    assert health["ok"] is True
    assert health["detail"] == "Get-MpComputerStatus responded"


def test_health_not_ok_on_nonzero() -> None:
    runner = _make_runner(rc=1, stderr="permission denied")
    src = WindowsDefenderConnector(runner=runner)
    health = src.health()
    assert health["ok"] is False
    assert "permission denied" in health["detail"]


def test_health_never_raises() -> None:
    class Boom:
        def __call__(self, argv: list[str]) -> tuple[int, str, str]:
            raise OSError("no binary")

    src = WindowsDefenderConnector(runner=Boom())
    health = src.health()
    assert health["ok"] is False
    assert "OSError" in health["detail"]


# ── status / computer_status ────────────────────────────────────────────────


def test_query_status() -> None:
    runner = _make_runner(stdout=STATUS_JSON)
    src = WindowsDefenderConnector(config={"name": "def-status"}, runner=runner)
    records = src.query({"target": "status"})
    assert len(records) == 1
    rec = records[0]
    assert rec["kind"] == "logs"
    assert rec["source"] == "def-status"
    assert rec["message"].startswith("Defender status:")
    assert rec["labels"]["AntivirusEnabled"] is True
    assert "Get-MpComputerStatus" in rec["raw"]["command"]


def test_query_computer_status() -> None:
    runner = _make_runner(stdout=STATUS_JSON)
    src = WindowsDefenderConnector(runner=runner)
    records = src.query({"target": "computer_status"})
    assert len(records) == 1
    assert "Get-MpComputerStatus" in records[0]["raw"]["command"]


# ── preferences / mp_preference ─────────────────────────────────────────────


def test_query_preferences() -> None:
    runner = _make_runner(stdout=PREFERENCES_JSON)
    src = WindowsDefenderConnector(config={"name": "def-pref"}, runner=runner)
    records = src.query({"target": "preferences"})
    assert len(records) == 1
    rec = records[0]
    assert rec["source"] == "def-pref"
    assert "DisableRealtimeMonitoring" in rec["message"]
    assert rec["labels"]["PUAProtection"] == 1
    assert "Get-MpPreference" in rec["raw"]["command"]


def test_query_mp_preference() -> None:
    runner = _make_runner(stdout=PREFERENCES_JSON)
    src = WindowsDefenderConnector(runner=runner)
    records = src.query({"target": "mp_preference"})
    assert len(records) == 1
    assert "Get-MpPreference" in records[0]["raw"]["command"]


# ── threats / threat_detection ──────────────────────────────────────────────


def test_query_threats() -> None:
    runner = _make_runner(stdout=THREATS_JSON)
    src = WindowsDefenderConnector(config={"name": "def-threat"}, runner=runner)
    records = src.query({"target": "threats"})
    assert len(records) == 2
    assert records[0]["level_or_status"] == "severe"
    assert "TestMalware" in records[0]["message"]
    assert "TestMalware2" in records[1]["message"]
    assert "Get-MpThreatDetection" in records[0]["raw"]["command"]


def test_query_threat_detection() -> None:
    runner = _make_runner(stdout=THREATS_JSON)
    src = WindowsDefenderConnector(runner=runner)
    records = src.query({"target": "threat_detection"})
    assert len(records) == 2


# ── exclusions / get_exclusions ─────────────────────────────────────────────


def test_query_exclusions() -> None:
    runner = _make_runner(stdout=EXCLUSIONS_JSON)
    src = WindowsDefenderConnector(runner=runner)
    records = src.query({"target": "exclusions"})
    assert len(records) == 1
    rec = records[0]
    assert "3 total" in rec["message"]
    assert rec["labels"]["ExclusionPath"] == ["C:\\foo"]


def test_query_get_exclusions() -> None:
    runner = _make_runner(stdout=EXCLUSIONS_JSON)
    src = WindowsDefenderConnector(runner=runner)
    records = src.query({"target": "get_exclusions"})
    assert len(records) == 1


# ── scan (mutating) ─────────────────────────────────────────────────────────


def test_query_scan_blocked_without_allow_mutate() -> None:
    src = WindowsDefenderConnector(runner=_make_runner())
    records = src.query({"target": "scan", "allow_mutate": False})
    assert len(records) == 1
    rec = records[0]
    assert rec["level_or_status"] == "blocked"
    assert "allow_mutate" in rec["message"]


def test_query_scan_allowed_with_allow_mutate() -> None:
    runner = _make_runner(stdout=SCAN_JSON)
    src = WindowsDefenderConnector(config={"name": "def-scan"}, runner=runner)
    records = src.query(
        {"target": "scan", "allow_mutate": True, "scan_type": "QuickScan"}
    )
    assert len(records) == 1
    rec = records[0]
    assert rec["source"] == "def-scan"
    assert "QuickScan" in rec["message"]
    assert rec["raw"]["command"] == "Start-MpScan -ScanType QuickScan"


def test_query_scan_invalid_scan_type_raises() -> None:
    src = WindowsDefenderConnector(runner=_make_runner())
    with pytest.raises(ValueError, match="scan_type must be one of"):
        src.query(
            {"target": "scan", "allow_mutate": True, "scan_type": "MaliciousScan"}
        )


def test_query_scan_allow_mutate_not_bool_raises() -> None:
    src = WindowsDefenderConnector(runner=_make_runner())
    with pytest.raises(ValueError, match="allow_mutate must be a bool"):
        src.query({"target": "scan", "allow_mutate": "yes"})


# ── unknown target ──────────────────────────────────────────────────────────


def test_query_unknown_target_raises() -> None:
    src = WindowsDefenderConnector(runner=_make_runner())
    with pytest.raises(ValueError, match="unknown target"):
        src.query({"target": "nonexistent"})


# ── injection rejection ─────────────────────────────────────────────────────


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
    src = WindowsDefenderConnector(runner=_make_runner())
    with pytest.raises(ValueError):
        src.query({"target": bad})


# ── argv is always a list ───────────────────────────────────────────────────


def test_no_shell_single_argv_elements() -> None:
    runner = _make_runner(stdout=STATUS_JSON)
    src = WindowsDefenderConnector(runner=runner)
    src.query({"target": "status"})
    argv = runner.calls[0]
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)
    cmd_idx = argv.index("-Command")
    for idx, a in enumerate(argv):
        if idx in (cmd_idx, cmd_idx + 1):
            continue
        assert " " not in a, f"argv[{idx}]={a!r} contains space"


# ── normalized record shape ─────────────────────────────────────────────────


def test_query_returns_normalized_shape() -> None:
    runner = _make_runner(stdout=STATUS_JSON)
    src = WindowsDefenderConnector(runner=runner)
    records = src.query({"target": "status"})
    assert len(records) == 1
    rec = records[0]
    assert set(rec.keys()) == REQUIRED_KEYS
    assert isinstance(rec["ts"], float)
    assert isinstance(rec["labels"], dict)
    assert isinstance(rec["raw"], dict)


# ── nonzero rc returns empty ────────────────────────────────────────────────


def test_status_nonzero_returns_empty() -> None:
    runner = _make_runner(rc=1, stderr="access denied")
    src = WindowsDefenderConnector(runner=runner)
    assert src.query({"target": "status"}) == []


def test_threats_nonzero_returns_empty() -> None:
    runner = _make_runner(rc=1)
    src = WindowsDefenderConnector(runner=runner)
    assert src.query({"target": "threats"}) == []


def test_exclusions_nonzero_returns_empty() -> None:
    runner = _make_runner(rc=1)
    src = WindowsDefenderConnector(runner=runner)
    assert src.query({"target": "exclusions"}) == []


def test_scan_nonzero_returns_empty() -> None:
    runner = _make_runner(rc=1)
    src = WindowsDefenderConnector(runner=runner)
    records = src.query(
        {"target": "scan", "allow_mutate": True, "scan_type": "QuickScan"}
    )
    assert records == []
