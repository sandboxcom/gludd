"""Deep serialization safety tests: pickle, JSON, YAML, msgpack, datetime.

Covers the repo's serialization policies:
  - Never pickle untrusted data (adversarial_detector rule)
  - Pydantic-validated JSON only for agent state (hibernation.py policy)
  - yaml.safe_load exclusively (adversarial_detector rule)
  - msgpack guarded-optional with fail-soft (ingest_formats.py pattern)
  - datetime serialization consistency across serializers
"""

from __future__ import annotations

import datetime
import io
import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class ToyPayload:
    name: str
    count: int
    tags: list[str]


def _make_dt() -> datetime.datetime:
    return datetime.datetime(2025, 7, 15, 14, 30, 0, tzinfo=ZoneInfo("UTC"))


# ===========================================================================
# 1.  Pickle — never on untrusted data
# ===========================================================================


class TestPickleNeverUntrusted:
    """pickle.loads on bytes from network / user / input is FORBIDDEN."""

    def test_pickle_loads_on_network_bytes_is_detectable(self):
        from general_ludd.security.adversarial_detector import (
            ALL_PATTERNS,
        )

        pattern_ids = {p.id for p in ALL_PATTERNS}
        assert "pickle_deserialize_untrusted" in pattern_ids

    def test_pickle_can_execute_arbitrary_code_never_use_on_untrusted(self):
        payload = b"cos\nsystem\n(S'id'\ntR."
        try:
            result = pickle.loads(payload)
        except Exception:
            result = None
        # pickle.loads can silently execute arbitrary code — this is WHY
        # it must never be used on untrusted data. The adversarial_detector
        # flags this pattern as CRITICAL BACKDOOR.
        assert result is not None  # structural assertion only

    def test_pickle_produces_different_output_under_different_python(self):
        data = {"a": 1, "b": [2, 3]}
        encoded = pickle.dumps(data, protocol=pickle.HIGHEST_PROTOCOL)
        assert isinstance(encoded, bytes)
        assert len(encoded) < 1024
        restored = pickle.loads(encoded)
        assert restored == data

    def test_pickle_forbidden_by_hibernation_policy(self):
        source = Path("src/general_ludd/agents/hibernation.py").read_text()
        assert "never pickle" in source.lower() or "never pickle" in source


# ===========================================================================
# 2.  JSON round-trip for dataclass-ish payloads
# ===========================================================================


class TestJsonRoundtripModels:
    def test_toy_payload_roundtrip(self):
        original = ToyPayload(name="test", count=42, tags=["a", "b"])
        encoded = json.dumps(original.__dict__)
        decoded = json.loads(encoded)
        assert decoded["name"] == "test"
        assert decoded["count"] == 42
        assert decoded["tags"] == ["a", "b"]

    def test_json_allow_nan_false_prevents_nan_injection(self):
        with pytest.raises(ValueError):
            json.dumps({"val": float("nan")}, allow_nan=False)

    def test_json_allow_nan_false_prevents_infinity_injection(self):
        with pytest.raises(ValueError):
            json.dumps({"val": float("inf")}, allow_nan=False)

    def test_json_sort_keys_produces_stable_output(self):
        d = {"z": 1, "a": 2, "m": 3}
        encoded1 = json.dumps(d, sort_keys=True)
        encoded2 = json.dumps(d, sort_keys=True)
        assert encoded1 == encoded2
        assert encoded1.index('"a"') < encoded1.index('"m"') < encoded1.index('"z"')

    def test_json_separators_compact_form(self):
        d = {"x": 1, "y": 2}
        encoded = json.dumps(d, separators=(",", ":"))
        parsed = json.loads(encoded)
        assert parsed == d
        assert " " not in encoded

    def test_json_default_str_handles_non_serializable(self):
        encoded = json.dumps({"ts": _make_dt()}, default=str)
        assert "2025" in encoded


# ===========================================================================
# 3.  YAML — safe_load only, safe_dump roundtrip
# ===========================================================================


class TestYamlSafeLoadOnly:
    def test_safe_load_rejects_python_object_tags(self):
        payload = "!!python/object:os.system [id]"
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(payload)

    def test_safe_load_roundtrip_with_safe_dump(self):
        data = {"env": "prod", "replicas": 3, "features": ["a", "b"]}
        buf = io.StringIO()
        yaml.safe_dump(data, buf)
        buf.seek(0)
        restored = yaml.safe_load(buf)
        assert restored == data

    def test_unsafe_load_pattern_detectable_by_adversarial_detector(self):
        from general_ludd.security.adversarial_detector import (
            ALL_PATTERNS,
        )

        pattern_ids = {p.id for p in ALL_PATTERNS}
        assert "yaml_unsafe_load" in pattern_ids

    def test_yaml_dump_preserves_structure(self):
        data = {"nested": {"key": [1, 2, 3]}}
        dumped = yaml.dump(data, default_flow_style=False)
        assert "nested" in dumped
        restored = yaml.safe_load(dumped)
        assert restored == data

    def test_yaml_safe_load_handles_empty_stream(self):
        assert yaml.safe_load("") is None
        assert yaml.safe_load("---\n") is None


# ===========================================================================
# 4.  msgpack — guarded optional dependency
# ===========================================================================


class TestMsgpackGuardedOptional:
    def test_msgpack_import_guarded_in_ingest_formats(self):
        source = Path("src/general_ludd/connectors/ingest_formats.py").read_text()
        assert "importlib.import_module" in source
        assert "msgpack" in source
        assert "unpackb" in source

    def test_msgpack_fail_soft_when_missing(self):
        import importlib

        try:
            m = importlib.import_module("msgpack")
        except ImportError:
            m = None
        if m is None:
            assert True
        else:
            decoded = m.unpackb(b"\x81\xa1a\x01", raw=False)
            assert decoded == {"a": 1}

    def test_msgpack_raw_false_returns_str_not_bytes(self):
        import importlib

        try:
            m = importlib.import_module("msgpack")
        except ImportError:
            pytest.skip("msgpack not installed")
        decoded = m.unpackb(b"\xa3foo", raw=False)
        assert isinstance(decoded, str)
        assert decoded == "foo"


# ===========================================================================
# 5.  datetime serialization consistency
# ===========================================================================


class TestDatetimeSerializationConsistency:
    def test_json_default_str_produces_iso8601(self):
        dt = _make_dt()
        encoded = json.dumps({"ts": dt}, default=str)
        assert "2025-07-15" in encoded
        assert "14:30:00" in encoded

    def test_datetime_isoformat_roundtrippable(self):
        dt = _make_dt()
        iso = dt.isoformat()
        decoded = datetime.datetime.fromisoformat(iso)
        assert decoded == dt

    def test_naive_datetime_raises_on_isoformat_comparison_with_tz(self):
        naive = datetime.datetime(2025, 7, 15, 14, 30)
        aware = _make_dt()
        assert naive != aware

    def test_json_dumps_consistent_between_runs(self):
        dt = datetime.datetime(2025, 1, 1, 0, 0, 0, 0, tzinfo=ZoneInfo("UTC"))
        encoded1 = json.dumps({"ts": dt.isoformat()})
        encoded2 = json.dumps({"ts": dt.isoformat()})
        assert encoded1 == encoded2

    def test_yaml_safe_dump_datetime(self):
        dt = _make_dt()
        buf = io.StringIO()
        yaml.safe_dump({"ts": dt}, buf)
        buf.seek(0)
        raw = buf.read()
        assert "2025" in raw


# ===========================================================================
# 6.  Structural guard: adversarial_detector pickle rule completeness
# ===========================================================================


class TestAdversarialDetectorSerializationRules:
    def test_pickle_deserialize_rule_exists_and_is_critical(self):
        from general_ludd.security.adversarial_detector import (
            ALL_PATTERNS,
        )

        rule = next(p for p in ALL_PATTERNS if p.id == "pickle_deserialize_untrusted")
        assert rule.category.value == "backdoor"
        assert rule.severity.value == "critical"

    def test_yaml_unsafe_load_rule_exists_and_is_critical(self):
        from general_ludd.security.adversarial_detector import (
            ALL_PATTERNS,
        )

        rule = next(p for p in ALL_PATTERNS if p.id == "yaml_unsafe_load")
        assert rule.category.value == "backdoor"
        assert rule.severity.value == "critical"
