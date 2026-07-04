"""Security batch 4 connector tests: D-06, D-29, D-30, D-31."""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import pytest


class TestD06AllowlistGuard:
    def test_d06_rejects_os_module(self):
        from general_ludd.connectors.registry import _assert_allowed_module

        with pytest.raises(ImportError, match="not in allowed prefixes"):
            _assert_allowed_module("os")

    def test_d06_rejects_os_system_style(self):
        from general_ludd.connectors.registry import _assert_allowed_module

        with pytest.raises(ImportError):
            _assert_allowed_module("os.system")

    def test_d06_rejects_subprocess(self):
        from general_ludd.connectors.registry import _assert_allowed_module

        with pytest.raises(ImportError):
            _assert_allowed_module("subprocess")

    def test_d06_allows_general_ludd_connectors(self):
        from general_ludd.connectors.registry import _assert_allowed_module

        # Should not raise
        _assert_allowed_module("general_ludd.connectors.foo")


class TestD29FamilyValidation:
    def test_d29_poisoned_family_falls_through_to_unknown(self):
        from general_ludd.connectors.normalize import _config_family

        # Bogus family value should fall through to "unknown" (not return the bogus value)
        result = _config_family({"family": "evil_injection; rm -rf /", "name": "something"})
        assert result == "unknown"


class TestD30FanoutCaps:
    def _make_source(self, name: str, kind: str, records: list) -> MagicMock:
        src = MagicMock()
        src.name = name
        src.KIND = kind
        src.query.return_value = records
        return src

    def test_d30_per_source_cap_enforced(self):
        from general_ludd.connectors.base import Observability, SourceRegistry

        reg = SourceRegistry()
        # source returns 20_000 records
        records = [{"ts": float(i), "source": "s1", "kind": "logs",
                    "level_or_status": "info", "message": "x", "value": None,
                    "labels": {}, "raw": None} for i in range(20_000)]
        src = self._make_source("s1", "logs", records)
        reg.register(src)
        obs = Observability(reg)
        result = obs.find({})
        assert len(result) <= 10_000, f"Expected <=10000, got {len(result)}"

    def test_d30_global_cap_enforced(self):
        from general_ludd.connectors.base import Observability, SourceRegistry

        reg = SourceRegistry()
        # 6 sources x 10_000 records each = 60_000 total
        for i in range(6):
            records = [{"ts": float(j), "source": f"s{i}", "kind": "logs",
                        "level_or_status": "info", "message": "x", "value": None,
                        "labels": {}, "raw": None} for j in range(10_000)]
            src = self._make_source(f"s{i}", "logs", records)
            reg.register(src)
        obs = Observability(reg)
        result = obs.find({})
        assert len(result) <= 50_000, f"Expected <=50000, got {len(result)}"


class TestD31NaNInfGuard:
    def test_d31_nan_ts_becomes_none(self):
        from general_ludd.connectors.base import normalized_record

        rec = normalized_record(source="s", kind="logs", ts=math.nan)
        assert rec["ts"] is None, f"NaN ts should be None, got {rec['ts']}"

    def test_d31_inf_value_becomes_none(self):
        from general_ludd.connectors.base import normalized_record

        rec = normalized_record(source="s", kind="metrics", value=math.inf)
        assert rec["value"] is None, f"Inf value should be None, got {rec['value']}"
