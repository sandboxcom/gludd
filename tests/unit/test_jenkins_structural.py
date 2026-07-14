"""Structural tests for connectors/jenkins.py — Jenkins pipeline connector."""

from __future__ import annotations

from general_ludd.connectors.jenkins import (
    JenkinsSource,
)


class TestJenkinsSource:
    def test_minimal_construction(self):
        source = JenkinsSource({"base_url": "https://jenkins.example.com"})
        assert source.KIND == "pipeline"
        assert source.name is not None

    def test_with_job(self):
        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com", "job": "my-job"}
        )
        assert source.job == "my-job"

    def test_construction_rejects_localhost(self):
        try:
            JenkinsSource({"base_url": "http://localhost:8080"})
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    def test_construction_rejects_no_base_url(self):
        try:
            JenkinsSource({})
        except ValueError:
            return
        raise AssertionError("expected ValueError")

    def test_health_returns_dict(self):
        def fake_http_get(url, headers):
            return 200, {"builds": []}

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"},
            http_get=fake_http_get,
        )
        result = source.health()
        assert "ok" in result
        assert isinstance(result["ok"], bool)

    def test_health_never_raises_on_transport_error(self):
        def broken_http_get(url, headers):
            raise OSError("connection refused")

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"},
            http_get=broken_http_get,
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_returns_list(self):
        def fake_http_get(url, headers):
            return 200, {
                "builds": [
                    {
                        "number": 42,
                        "result": "SUCCESS",
                        "timestamp": 1700000000000,
                        "url": "https://jenkins.example.com/job/my-job/42",
                    }
                ]
            }

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"},
            http_get=fake_http_get,
        )
        result = source.query()
        assert isinstance(result, list)
        assert len(result) == 1
        record = result[0]
        assert record["kind"] == "pipeline"
        assert record["level_or_status"] == "SUCCESS"

    def test_query_empty_on_non_200(self):
        def fake_http_get(url, headers):
            return 500, {}

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"},
            http_get=fake_http_get,
        )
        result = source.query()
        assert result == []

    def test_query_with_result_filter(self):
        def fake_http_get(url, headers):
            return 200, {
                "builds": [
                    {"number": 1, "result": "SUCCESS"},
                    {"number": 2, "result": "FAILURE"},
                ]
            }

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"},
            http_get=fake_http_get,
        )
        result = source.query({"result": "FAILURE"})
        assert len(result) == 1
        assert result[0]["level_or_status"] == "FAILURE"

    def test_query_with_limit(self):
        def fake_http_get(url, headers):
            return 200, {
                "builds": [
                    {"number": 1, "result": "SUCCESS"},
                    {"number": 2, "result": "SUCCESS"},
                    {"number": 3, "result": "SUCCESS"},
                ]
            }

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"},
            http_get=fake_http_get,
        )
        result = source.query({"limit": 2})
        assert len(result) == 2

    def test_record_shape(self):
        def fake_http_get(url, headers):
            return 200, {"builds": [{"number": 1, "result": "SUCCESS"}]}

        source = JenkinsSource(
            {"base_url": "https://jenkins.example.com"},
            http_get=fake_http_get,
        )
        result = source.query()
        record = result[0]
        assert "ts" in record
        assert "source" in record
        assert "kind" in record
        assert "level_or_status" in record
        assert "message" in record
        assert "labels" in record
        assert "raw" in record
