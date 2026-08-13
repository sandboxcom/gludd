"""Unit tests for the self-contained Jenkins observability connector.

Transport is fully MOCKED via the injectable ``http_get`` callable — no real
network, no DNS, no Jenkins server. These tests pin the normalization contract,
the SUCCESS/FAILURE/UNSTABLE status mapping, the millisecond -> second timestamp
conversion, the health() 200-vs-403 behavior, and the literal-host SSRF block.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from general_ludd.connectors.jenkins import JenkinsSource

# --- canned Jenkins JSON API payloads ------------------------------------


def _builds_payload() -> dict:
    """Shape of GET {base}/job/{job}/api/json?tree=builds[...]."""
    return {
        "builds": [
            {
                "number": 42,
                "result": "SUCCESS",
                "timestamp": 1_700_000_000_000,
                "url": "https://ci.example.com/job/build/42/",
                "duration": 12_345,
            },
            {
                "number": 41,
                "result": "FAILURE",
                "timestamp": 1_699_999_000_000,
                "url": "https://ci.example.com/job/build/41/",
                "duration": 999,
            },
            {
                "number": 40,
                "result": "UNSTABLE",
                "timestamp": 1_699_998_000_000,
                "url": "https://ci.example.com/job/build/40/",
                "duration": 500,
            },
            {
                # in-progress build: result is null
                "number": 39,
                "result": None,
                "timestamp": 1_699_997_000_000,
                "url": "https://ci.example.com/job/build/39/",
                "duration": 0,
            },
        ]
    }


class _Transport:
    """Records calls and returns scripted (status, json) responses."""

    def __init__(self, status: int, payload: object) -> None:
        self._status = status
        self._payload = payload
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, object]:
        self.calls.append((url, headers))
        return self._status, self._payload


def _make_source(transport: _Transport, *, job: str | None = "build", **cfg: object) -> JenkinsSource:
    config: dict[str, object] = {
        "base_url": "https://ci.example.com",
        "user_env": "JENKINS_USER",
        "token_env": "JENKINS_TOKEN",
    }
    if job is not None:
        config["job"] = job
    config.update(cfg)
    return JenkinsSource(config, http_get=transport)


@pytest.fixture(autouse=True)
def _creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JENKINS_USER", "ci-bot")
    monkeypatch.setenv("JENKINS_TOKEN", "s3cr3t-token")


# --- contract / identity --------------------------------------------------


def test_kind_is_pipeline() -> None:
    assert JenkinsSource.KIND == "pipeline"


def test_name_attribute_present() -> None:
    src = _make_source(_Transport(200, _builds_payload()))
    assert isinstance(src.name, str)
    assert src.name


# --- normalization --------------------------------------------------------


def test_query_normalizes_builds() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    records = src.query({})

    assert len(records) == 4
    first = records[0]
    # required normalized keys
    assert set(first) == {
        "ts",
        "source",
        "kind",
        "level_or_status",
        "message",
        "value",
        "labels",
        "raw",
    }
    assert first["kind"] == "pipeline"
    assert first["value"] is None
    assert first["source"] == src.name


def test_timestamp_milliseconds_converted_to_seconds() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    records = src.query({})
    # 1_700_000_000_000 ms -> 1_700_000_000 s
    assert records[0]["ts"] == 1_700_000_000.0
    assert isinstance(records[0]["ts"], float)


def test_status_mapping_passthrough() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    statuses = [r["level_or_status"] for r in src.query({})]
    assert statuses[:3] == ["SUCCESS", "FAILURE", "UNSTABLE"]


def test_in_progress_build_status_is_unknown() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    last = src.query({})[3]
    # null result normalizes to a sentinel, never raises
    assert last["level_or_status"] == "UNKNOWN"


def test_message_is_job_hash_number() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    rec = src.query({})[0]
    assert rec["message"] == "build#42"


def test_labels_contain_number_url_duration() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    labels = src.query({})[0]["labels"]
    assert labels["number"] == 42
    assert labels["url"] == "https://ci.example.com/job/build/42/"
    assert labels["duration_ms"] == 12_345


def test_raw_is_original_build() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    rec = src.query({})[0]
    assert rec["raw"]["number"] == 42
    assert rec["raw"]["result"] == "SUCCESS"


# --- request shaping ------------------------------------------------------


def test_query_hits_job_endpoint_with_tree() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    src.query({})
    url = transport.calls[0][0]
    assert url.startswith("https://ci.example.com/job/build/api/json")
    assert "tree=builds" in url


def test_query_without_job_hits_root_api() -> None:
    transport = _Transport(200, {"builds": []})
    src = _make_source(transport, job=None)
    src.query({})
    url = transport.calls[0][0]
    assert url.startswith("https://ci.example.com/api/json")


def test_query_without_job_normalizes_controller_jobs() -> None:
    transport = _Transport(
        200,
        {
            "jobs": [
                {
                    "name": "release",
                    "url": "https://ci.example.com/job/release/",
                    "color": "blue",
                    "lastBuild": {"number": 7},
                }
            ]
        },
    )
    src = _make_source(transport, job=None)

    records = src.query({})

    assert len(records) == 1
    assert records[0]["message"] == "release#7"
    assert records[0]["level_or_status"] == "SUCCESS"
    assert records[0]["labels"]["url"] == "https://ci.example.com/job/release/7/"
    assert "tree=jobs" in transport.calls[0][0]


def test_basic_auth_header_built_from_env() -> None:
    import base64

    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    src.query({})
    headers = transport.calls[0][1]
    expected = base64.b64encode(b"ci-bot:s3cr3t-token").decode("ascii")
    assert headers["Authorization"] == f"Basic {expected}"


# --- spec filters ---------------------------------------------------------


def test_spec_limit_truncates() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    records = src.query({"limit": 2})
    assert len(records) == 2


def test_spec_result_filters_by_status() -> None:
    transport = _Transport(200, _builds_payload())
    src = _make_source(transport)
    records = src.query({"result": "FAILURE"})
    assert len(records) == 1
    assert records[0]["level_or_status"] == "FAILURE"


# --- health ---------------------------------------------------------------


def test_health_ok_on_200() -> None:
    src = _make_source(_Transport(200, _builds_payload()))
    h = src.health()
    assert h["ok"] is True
    assert h["status"] == 200


def test_health_not_ok_on_403() -> None:
    src = _make_source(_Transport(403, {"error": "forbidden"}))
    h = src.health()
    assert h["ok"] is False
    assert h["status"] == 403


def test_health_never_raises_on_transport_error() -> None:
    def boom(url: str, headers: dict[str, str]) -> tuple[int, object]:
        raise OSError("connection refused")

    src = cast(Any, _make_source)(boom)
    h = src.health()
    assert h["ok"] is False
    assert "error" in h


# --- SSRF literal-host block ----------------------------------------------


@pytest.mark.parametrize(
    "host",
    [
        "http://127.0.0.1",
        "http://localhost",
        "http://[::1]",
        "http://10.0.0.5",
        "http://192.168.1.10",
        "http://172.16.0.1",
        "http://169.254.169.254",  # cloud metadata
        "http://0.0.0.0",
    ],
)
def test_internal_base_url_rejected(host: str) -> None:
    with pytest.raises(ValueError, match=r"(?i)ssrf|internal|private|loopback|blocked"):
        JenkinsSource(
            {
                "base_url": host,
                "user_env": "JENKINS_USER",
                "token_env": "JENKINS_TOKEN",
            },
            http_get=_Transport(200, {}),
        )


def test_public_base_url_accepted() -> None:
    src = JenkinsSource(
        {
            "base_url": "https://ci.example.com",
            "user_env": "JENKINS_USER",
            "token_env": "JENKINS_TOKEN",
        },
        http_get=_Transport(200, {}),
    )
    assert src.base_url == "https://ci.example.com"
