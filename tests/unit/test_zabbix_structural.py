"""Structural tests for connectors/zabbix.py — Zabbix monitoring connector."""

from __future__ import annotations

from general_ludd.connectors.zabbix import (
    _SEVERITY,
    ZabbixSource,
    _as_float,
    _as_int_ts,
    _validate_base_url,
)


class TestValidateBaseUrl:
    def test_valid_public_url(self):
        result = _validate_base_url("https://zabbix.example.com")
        assert result == "https://zabbix.example.com"

    def test_rejects_loopback(self):
        from general_ludd.connectors._errors import SSRFError
        try:
            _validate_base_url("http://127.0.0.1")
        except SSRFError:
            return
        raise AssertionError("expected SSRFError")

    def test_rejects_empty(self):
        from general_ludd.connectors._errors import SSRFError
        try:
            _validate_base_url("")
        except SSRFError:
            return
        raise AssertionError("expected SSRFError")

    def test_rejects_bad_scheme(self):
        from general_ludd.connectors._errors import SSRFError
        try:
            _validate_base_url("ftp://zabbix.example.com")
        except SSRFError:
            return
        raise AssertionError("expected SSRFError")

    def test_strips_trailing_slash(self):
        result = _validate_base_url("https://zabbix.example.com/")
        assert result == "https://zabbix.example.com"


class TestAsFloat:
    def test_numeric_string(self):
        assert _as_float("42.5") == 42.5

    def test_int(self):
        assert _as_float(42) == 42.0

    def test_none(self):
        assert _as_float(None) is None

    def test_invalid_string(self):
        assert _as_float("abc") is None


class TestAsIntTs:
    def test_numeric_string(self):
        assert _as_int_ts("1700000000") == 1700000000

    def test_none(self):
        assert _as_int_ts(None) is None

    def test_invalid(self):
        assert _as_int_ts("abc") is None


class TestSeverityMap:
    def test_has_expected_keys(self):
        assert _SEVERITY["0"] == "not_classified"
        assert _SEVERITY["5"] == "disaster"


class TestZabbixSource:
    def test_minimal_construction(self):
        source = ZabbixSource({"base_url": "https://zabbix.example.com"})
        assert source.KIND == "metrics"
        assert source.name == "zabbix"

    def test_custom_name(self):
        source = ZabbixSource(
            {"base_url": "https://zabbix.example.com", "name": "prod-zabbix"}
        )
        assert source.name == "prod-zabbix"

    def test_environment_token_injection(self):
        def fake_transport(method, url, *, headers=None, params=None, json=None, timeout=None):
            class Resp:
                status_code = 200
                def json(self):
                    return {"result": "6.0"}
            return Resp()

        source = ZabbixSource(
            {"base_url": "https://zabbix.example.com", "token_env": "ZBX_TOKEN"},
            transport=fake_transport,
            environ={"ZBX_TOKEN": "test-token"},
        )
        assert source._token == "test-token"

    def test_health_returns_dict(self):
        def fake_transport(method, url, *, headers=None, params=None, json=None, timeout=None):
            class Resp:
                status_code = 200
                def json(self):
                    return {"result": "6.0.1"}
            return Resp()

        source = ZabbixSource(
            {"base_url": "https://zabbix.example.com"},
            transport=fake_transport,
        )
        result = source.health()
        assert "ok" in result
        assert result["ok"] is True

    def test_health_never_raises(self):
        def broken_transport(method, url, *, headers=None, params=None, json=None, timeout=None):
            raise RuntimeError("gone")

        source = ZabbixSource(
            {"base_url": "https://zabbix.example.com"},
            transport=broken_transport,
        )
        result = source.health()
        assert result["ok"] is False

    def test_query_history_returns_list(self):
        def fake_transport(method, url, *, headers=None, params=None, json=None, timeout=None):
            class Resp:
                status_code = 200
                def json(self):
                    return {
                        "result": [{
                            "clock": "1700000000",
                            "value": "42.5",
                            "host": "srv1",
                            "itemid": "123",
                            "name": "cpu_load",
                        }],
                    }
            return Resp()

        source = ZabbixSource(
            {"base_url": "https://zabbix.example.com"},
            transport=fake_transport,
        )
        result = source.query({"method": "history.get"})
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["kind"] == "metrics"

    def test_query_problem_returns_list(self):
        def fake_transport(method, url, *, headers=None, params=None, json=None, timeout=None):
            class Resp:
                status_code = 200
                def json(self):
                    return {"result": [{"clock": "1700000000", "name": "High CPU", "severity": "5", "objectid": "45"}]}
            return Resp()

        source = ZabbixSource(
            {"base_url": "https://zabbix.example.com"},
            transport=fake_transport,
        )
        result = source.query({"method": "problem.get"})
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["kind"] == "metrics"
        assert result[0]["level_or_status"] == "disaster"
