"""Close hosted-only monitoring and Azure Boards branch gaps."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from typing import Any, cast

import httpx
import pytest

from general_ludd.connectors.nagios import (
    NagiosSource,
    _coerce_state,
    _default_http_get,
    _validate_base_url,
)
from general_ludd.connectors.osquery import OsquerySource, RunResult
from general_ludd.issue_sources.azure_boards import (
    AzureBoardsIssueSource,
    _reject_internal_base_url,
)


class _HttpResponse:
    def __init__(self, status: int, content: bytes) -> None:
        self.status_code = status
        self.content = content


class _HttpClient:
    response = _HttpResponse(200, b"{}")

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def __enter__(self) -> _HttpClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, url: str, **kwargs: object) -> _HttpResponse:
        del url, kwargs
        return self.response


class _RunResult:
    def __init__(self, returncode: int = 0, stdout: str = "[]", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _Runner:
    def __init__(self, result: RunResult) -> None:
        self.result = result
        self.argv: Sequence[str] = ()

    def __call__(self, argv: Sequence[str]) -> RunResult:
        self.argv = argv
        return self.result


class TestNagiosHostedBranches:
    def test_default_transport_parses_json_and_empty_body(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(httpx, "Client", _HttpClient)
        _HttpClient.response = _HttpResponse(202, b'{"ok": true}')
        assert _default_http_get("https://nagios.example/status") == (202, {"ok": True})
        _HttpClient.response = _HttpResponse(204, b"")
        assert _default_http_get("http://nagios.example/status") == (204, {})
        with pytest.raises(ValueError, match="scheme"):
            _default_http_get("file:///status")

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (True, ("WARNING", 1.0)),
            (99, ("UNKNOWN", 99.0)),
            ("2", ("CRITICAL", 2.0)),
            ("warning", ("WARNING", 1.0)),
            ("novel", ("NOVEL", 0.0)),
            ("", ("UNKNOWN", 0.0)),
            (object(), ("UNKNOWN", 0.0)),
        ],
    )
    def test_state_coercion_is_total(self, raw: object, expected: tuple[str, float]) -> None:
        assert _coerce_state(raw, {0: "OK", 1: "WARNING", 2: "CRITICAL"}) == expected

    @pytest.mark.parametrize("url", ["", "https:///missing", "ftp://nagios.example"])
    def test_base_url_shape_rejected(self, url: str) -> None:
        with pytest.raises(ValueError):
            _validate_base_url(url)

    def test_state_record_normalizes_milliseconds_and_invalid_time(self) -> None:
        source = NagiosSource(
            {"base_url": "https://nagios.example"},
            http_get=lambda *args, **kwargs: (200, {}),
        )
        millis = source._state_record("host", "svc", 0, 1_700_000_000_000, {})
        invalid = source._state_record("host", None, 0, object(), {})
        assert millis["ts"] == 1_700_000_000.0
        assert invalid["ts"] == 0.0
        assert invalid["message"] == "host"

    def test_normalize_rejects_shapes_and_skips_non_service_maps(self) -> None:
        source = NagiosSource(
            {"base_url": "https://nagios.example"},
            http_get=lambda *args, **kwargs: (200, {}),
        )
        assert source._normalize("hostlist", 200, []) [0]["level_or_status"] == "error"
        assert source._normalize("hostlist", 503, {"error": "down"})[0]["level_or_status"] == "error"
        assert source._normalize("servicelist", 200, {"data": {"servicelist": {"host": []}}}) == []

    def test_health_distinguishes_dict_error_and_non_dict(self) -> None:
        results = iter([(503, {"error": "maintenance"}), (503, "down")])
        source = NagiosSource(
            {"base_url": "https://nagios.example"},
            http_get=lambda *args, **kwargs: next(results),
        )
        assert source.health()["detail"] == "maintenance"
        assert source.health()["detail"] == "unhealthy (status 503)"


class TestOsqueryHostedBranches:
    def test_health_handles_missing_binary_error_and_blank_stderr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda binary: None)
        assert OsquerySource().health()["ok"] is False
        failed = OsquerySource(runner=_Runner(_RunResult(2, stderr="")))
        assert failed.health() == {"ok": False, "detail": "exit code 2"}

        def explode(argv: Sequence[str]) -> RunResult:
            raise OSError(str(argv))

        detail = OsquerySource(runner=explode).health()["detail"]
        assert str(detail).startswith("OSError:")

    def test_query_validation_and_runner_failures(self) -> None:
        with pytest.raises(ValueError, match="string"):
            OsquerySource._validate_query(cast(Any, None))
        with pytest.raises(ValueError, match="empty"):
            OsquerySource._validate_query("  ")
        with pytest.raises(ValueError, match="start"):
            OsquerySource._validate_query("--flag")
        with pytest.raises(ValueError, match="metacharacters"):
            OsquerySource._validate_query("select $HOME")
        runner = _Runner(_RunResult(1, stderr=""))
        with pytest.raises(RuntimeError, match="exit code 1"):
            OsquerySource(runner=runner).query({"query": "select 1"})

    def test_row_parsing_and_numeric_coercion_are_total(self) -> None:
        assert OsquerySource._infer_table("select 1") == "osquery"
        assert OsquerySource._parse_rows("") == []
        assert OsquerySource._parse_rows('[{"x": 1}, "bad", 2]') == [{"x": 1}]
        with pytest.raises(RuntimeError, match="non-JSON"):
            OsquerySource._parse_rows("not json")
        with pytest.raises(RuntimeError, match="not a list"):
            OsquerySource._parse_rows("{}")
        assert OsquerySource._coerce_label(None) == ""
        values = [None, True, 3, "", "-4", "2.5", "word"]
        expected = [None, 1, 3, "", -4, 2.5, "word"]
        assert [OsquerySource._coerce_numeric(value) for value in values] == expected


class TestAzureBoardsHostedBranches:
    @pytest.mark.parametrize(
        "url",
        ["file://azure.example", "https:///missing", "https://224.0.0.1"],
    )
    def test_base_url_shape_and_multicast_rejected(self, url: str) -> None:
        with pytest.raises(ValueError):
            _reject_internal_base_url(url)
        _reject_internal_base_url("https://azure.example")

    def test_empty_fetch_short_circuits_and_scalar_assignee_normalizes(self) -> None:
        source = AzureBoardsIssueSource({"org": "org", "project": "project"})
        assert source._fetch_work_items(cast(httpx.Client, object()), []) == []
        issue = source._normalize_work_item(
            {
                "id": 7,
                "fields": {
                    "System.AssignedTo": "Owner",
                    "System.Tags": " one ; ;two ",
                },
            }
        )
        assert issue["assignee"] == "Owner"
        assert issue["labels"] == ["one", "two"]

    def test_health_transport_failure_is_observable(self) -> None:
        def fail_request(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("offline", request=request)

        source = AzureBoardsIssueSource(
            {"org": "org", "project": "project"},
            transport=httpx.MockTransport(fail_request),
        )
        result = source.health()
        assert result["ok"] is False
        assert str(result["detail"]).startswith("ConnectError:")
