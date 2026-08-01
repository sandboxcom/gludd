"""Unit tests for the MacosLogSource connector (mocked runner — no subprocess)."""

from __future__ import annotations

import json

import pytest

from general_ludd.connectors.macos_log import (
    MacosLogSource,
    PredicateError,
    _secret_from_env,
)

# A canned `log show --style json` payload (array of unified-log entries).
CANNED_LOG_JSON = json.dumps(
    [
        {
            "timestamp": "2026-06-16 10:00:00.000000-0700",
            "messageType": "Error",
            "eventMessage": "disk almost full",
            "subsystem": "com.apple.diskmanagement",
            "category": "space",
            "processImagePath": "/usr/libexec/diskarbitrationd",
            "processID": 412,
        },
        {
            "timestamp": "2026-06-16 10:00:01.000000-0700",
            "messageType": "Default",
            "eventMessage": "user logged in",
            "subsystem": "com.apple.loginwindow",
            "category": "auth",
            "process": "loginwindow",
            "pid": 88,
        },
    ]
)


class _CannedRunner:
    """Records the argv it was called with and returns a canned result."""

    def __init__(self, rc: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.rc = rc
        self.stdout = stdout
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(argv)
        return self.rc, self.stdout, self.stderr


class TestNormalization:
    def test_canned_log_show_json_normalizes(self) -> None:
        runner = _CannedRunner(stdout=CANNED_LOG_JSON)
        src = MacosLogSource(config={"name": "host-log"}, runner=runner)
        records = src.query({"duration": "10m"})

        assert len(records) == 2
        first = records[0]
        assert first["kind"] == "logs"
        assert first["source"] == "host-log"
        assert first["level_or_status"] == "error"
        assert first["message"] == "disk almost full"
        assert first["labels"]["subsystem"] == "com.apple.diskmanagement"
        assert first["labels"]["process"] == "/usr/libexec/diskarbitrationd"
        assert first["labels"]["pid"] == 412
        assert first["ts"] == "2026-06-16 10:00:00.000000-0700"
        assert first["raw"]["eventMessage"] == "disk almost full"

    def test_default_message_type_maps_to_info(self) -> None:
        runner = _CannedRunner(stdout=CANNED_LOG_JSON)
        src = MacosLogSource(runner=runner)
        records = src.query({})
        assert records[1]["level_or_status"] == "info"
        assert records[1]["labels"]["process"] == "loginwindow"

    def test_kind_class_attr(self) -> None:
        assert MacosLogSource.KIND == "logs"

    def test_ndjson_fallback_parsing(self) -> None:
        ndjson = (
            '{"messageType":"Info","eventMessage":"a","timestamp":"t1"}\n'
            '{"messageType":"Debug","eventMessage":"b","timestamp":"t2"}'
        )
        runner = _CannedRunner(stdout=ndjson)
        src = MacosLogSource(runner=runner)
        records = src.query({})
        assert [r["message"] for r in records] == ["a", "b"]
        assert records[1]["level_or_status"] == "debug"


class TestArgvAndTimeBound:
    def test_argv_is_a_list_with_duration(self) -> None:
        runner = _CannedRunner(stdout="[]")
        src = MacosLogSource(runner=runner)
        src.query({"duration": "30s"})
        argv = runner.calls[0]
        assert isinstance(argv, list)
        assert argv[:2] == ["log", "show"]
        assert "--last" in argv
        assert argv[argv.index("--last") + 1] == "30s"
        # No shell metacharacters merged in.
        assert all(";" not in part and "&&" not in part for part in argv)

    def test_predicate_passed_as_single_argv_element(self) -> None:
        runner = _CannedRunner(stdout="[]")
        src = MacosLogSource(runner=runner)
        src.query({"predicate": 'subsystem == "com.apple.foo"'})
        argv = runner.calls[0]
        assert "--predicate" in argv
        assert argv[argv.index("--predicate") + 1] == 'subsystem == "com.apple.foo"'

    def test_invalid_duration_rejected(self) -> None:
        runner = _CannedRunner(stdout="[]")
        src = MacosLogSource(runner=runner)
        with pytest.raises(ValueError):
            src.query({"duration": "10 minutes; rm -rf /"})


class TestPredicateRejection:
    @pytest.mark.parametrize(
        "bad",
        [
            "subsystem == `whoami`",
            "x == $(id)",
            "a; rm -rf /",
            "a && b",
            "a | grep x",
            "a > /etc/passwd",
            "a\nb",
        ],
    )
    def test_metachar_predicate_rejected(self, bad: str) -> None:
        runner = _CannedRunner(stdout="[]")
        src = MacosLogSource(runner=runner)
        with pytest.raises(PredicateError):
            src.query({"predicate": bad})
        # Rejected BEFORE the runner is ever invoked.
        assert runner.calls == []

    def test_config_predicate_validated_at_construction(self) -> None:
        with pytest.raises(PredicateError):
            MacosLogSource(config={"predicate": "a `b`"}, runner=_CannedRunner())

    def test_safe_predicate_allowed(self) -> None:
        runner = _CannedRunner(stdout="[]")
        src = MacosLogSource(
            config={"predicate": 'process == "kernel"'}, runner=runner
        )
        src.query({})
        argv = runner.calls[0]
        assert argv[argv.index("--predicate") + 1] == 'process == "kernel"'


class TestHealth:
    def test_health_ok(self) -> None:
        src = MacosLogSource(runner=_CannedRunner(rc=0, stdout="[]"))
        h = src.health()
        assert h["ok"] is True
        assert "detail" in h

    def test_health_not_ok_on_nonzero(self) -> None:
        src = MacosLogSource(runner=_CannedRunner(rc=1, stderr="boom"))
        h = src.health()
        assert h["ok"] is False
        assert "boom" in h["detail"]

    def test_health_never_raises(self) -> None:
        def exploding(argv: list[str]) -> tuple[int, str, str]:
            raise OSError("runner blew up")

        src = MacosLogSource(runner=exploding)
        h = src.health()  # must not raise
        assert h["ok"] is False
        assert "probe error" in h["detail"]

    def test_health_no_runner(self) -> None:
        src = MacosLogSource()
        h = src.health()
        assert h["ok"] is False


class TestQueryRobustness:
    def test_nonzero_rc_returns_empty(self) -> None:
        src = MacosLogSource(runner=_CannedRunner(rc=2, stdout=CANNED_LOG_JSON))
        assert src.query({}) == []

    def test_empty_stdout_returns_empty(self) -> None:
        src = MacosLogSource(runner=_CannedRunner(stdout=""))
        assert src.query({}) == []

    def test_query_without_runner_is_empty(self) -> None:
        src = MacosLogSource()
        assert src.query({}) == []


class TestSecretFromEnv:
    def test_token_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MACOS_LOG_SECRET", "s3cr3t")
        assert _secret_from_env("MACOS_LOG_SECRET") == "s3cr3t"

    def test_missing_env_returns_none(self) -> None:
        assert _secret_from_env(None) is None
        assert _secret_from_env("DEFINITELY_NOT_SET_XYZ") is None
