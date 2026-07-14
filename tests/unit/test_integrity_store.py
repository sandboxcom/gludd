"""Unit tests for C.5 IntegrityStore — HMAC canonical-JSON baseline, fail-closed.

Tests the general-purpose integrity store that signs arbitrary JSON-serializable
payloads with HMAC-SHA256 over canonical (deterministically-key-ordered) JSON and
fails closed on any integrity issue.
"""

from __future__ import annotations

import pytest

from general_ludd.integrity.store import IntegrityError, IntegrityStore, canonical_json


# ---------------------------------------------------------------------------
# canonical_json
# ---------------------------------------------------------------------------
class TestCanonicalJson:
    def test_deterministic_key_ordering(self):
        data = {"z": 1, "a": 2, "m": 3}
        result = canonical_json(data)
        assert result == '{"a":2,"m":3,"z":1}'

    def test_deterministic_nested_key_ordering(self):
        data = {"outer": {"z": 1, "a": 2}, "inner": [{"b": 3, "a": 4}]}
        result = canonical_json(data)
        assert result.startswith('{"inner":[{"a":4,"b":3}],"outer":{"a":2,"z":1}}')
        assert '"z":1' not in result.split('"outer"')[0]

    def test_separators_compact(self):
        data = {"key": "value"}
        result = canonical_json(data)
        assert " " not in result

    def test_idempotent(self):
        data = {"c": 3, "b": 2, "a": [5, 4, 3]}
        first = canonical_json(data)
        second = canonical_json(data)
        assert first == second

    def test_lists_preserve_order(self):
        data = {"items": [3, 1, 2]}
        result = canonical_json(data)
        assert result == '{"items":[3,1,2]}'


# ---------------------------------------------------------------------------
# HMAC signing and verification
# ---------------------------------------------------------------------------
class TestHmacSignVerify:
    def test_sign_produces_hex_digest(self):
        store = IntegrityStore(key=b"0123456789abcdef")
        mac = store.sign({"foo": "bar"})
        assert len(mac) == 64
        assert all(c in "0123456789abcdef" for c in mac)

    def test_verify_succeeds_for_untampered_data(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        data = {"hello": "world", "count": 42}
        store.save("test_item", data)
        loaded = store.load("test_item")
        assert loaded == data

    def test_verify_succeeds_for_empty_dict(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("empty", {})
        assert store.load("empty") == {}

    def test_verify_succeeds_for_nested_structures(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        data = {"a": [1, 2, {"b": "c"}], "d": None}
        store.save("nested", data)
        assert store.load("nested") == data

    def test_deterministic_mac_across_calls(self):
        store = IntegrityStore(key=b"0123456789abcdef")
        data = {"a": 1, "b": 2}
        mac1 = store.sign(data)
        mac2 = store.sign(data)
        assert mac1 == mac2

    def test_deterministic_mac_across_instances(self):
        store1 = IntegrityStore(key=b"0123456789abcdef")
        store2 = IntegrityStore(key=b"0123456789abcdef")
        mac1 = store1.sign({"x": "y"})
        mac2 = store2.sign({"x": "y"})
        assert mac1 == mac2


# ---------------------------------------------------------------------------
# Tampering detection (fail-closed)
# ---------------------------------------------------------------------------
class TestTamperDetection:
    def test_tampered_payload_raises(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        payload_path = tmp_path / "item.json"
        payload_path.write_text('{"value":999}')
        with pytest.raises(IntegrityError, match=r"tampered|cannot verify|MAC"):
            store.load("item")

    def test_tampered_mac_raises(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        mac_path = tmp_path / "item.json.mac"
        mac_path.write_text("a" * 64)
        with pytest.raises(IntegrityError, match=r"tampered|cannot verify|MAC"):
            store.load("item")

    def test_missing_mac_raises(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        (tmp_path / "item.json.mac").unlink()
        with pytest.raises(IntegrityError, match=r"missing|no MAC|cannot verify"):
            store.load("item")

    def test_missing_payload_raises(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        (tmp_path / "item.json").unlink()
        with pytest.raises(IntegrityError, match=r"missing|not found|cannot verify"):
            store.load("item")

    def test_wrong_key_produces_different_mac(self):
        store1 = IntegrityStore(key=b"key1_key1_key1_1")
        store2 = IntegrityStore(key=b"key2_key2_key2_2")
        data = {"secret": 42}
        mac1 = store1.sign(data)
        mac2 = store2.sign(data)
        assert mac1 != mac2

    def test_wrong_key_fails_load(self, tmp_path):
        store_a = IntegrityStore(base_dir=str(tmp_path), key=b"key_a_key_a_key_")
        store_a.save("item", {"value": 1})
        store_b = IntegrityStore(base_dir=str(tmp_path), key=b"key_b_key_b_key_")
        with pytest.raises(IntegrityError, match=r"tampered|cannot verify|MAC"):
            store_b.load("item")

    def test_truncated_payload_raises(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": "hello world"})
        payload_path = tmp_path / "item.json"
        content = payload_path.read_text()
        payload_path.write_text(content[:5])
        with pytest.raises(IntegrityError):
            store.load("item")

    def test_short_mac_raises(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        (tmp_path / "item.json.mac").write_text("short")
        with pytest.raises(IntegrityError):
            store.load("item")


# ---------------------------------------------------------------------------
# Round-trip: store -> load with valid MAC
# ---------------------------------------------------------------------------
class TestRoundTrip:
    def test_round_trip_simple_dict(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        original = {"name": "test", "version": 1}
        store.save("config", original)
        assert store.load("config") == original

    def test_round_trip_list_of_dicts(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        original = [{"id": 1}, {"id": 2}, {"id": 3}]
        store.save("records", original)
        assert store.load("records") == original

    def test_round_trip_none_values(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        original = {"key": None, "other": "present"}
        store.save("nullable", original)
        assert store.load("nullable") == original

    def test_round_trip_boolean_values(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        original = {"enabled": True, "debug": False}
        store.save("flags", original)
        assert store.load("flags") == original

    def test_round_trip_numeric_types(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        original = {"int": -7, "float": 3.14}
        stored = store.load(store.save("numeric", original))
        assert stored == original

    def test_overwrite_and_reload(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"a": 1})
        store.save("item", {"a": 2})
        assert store.load("item") == {"a": 2}

    def test_multiple_names_independent(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("one", {"value": 1})
        store.save("two", {"value": 2})
        assert store.load("one") == {"value": 1}
        assert store.load("two") == {"value": 2}


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------
class TestAtomicWrites:
    def test_no_temp_file_left_behind(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        temp_files = list(tmp_path.glob("*.tmp"))
        assert len(temp_files) == 0

    def test_payload_file_exists_after_save(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        assert (tmp_path / "item.json").exists()

    def test_mac_file_exists_after_save(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path), key=b"0123456789abcdef")
        store.save("item", {"value": 1})
        assert (tmp_path / "item.json.mac").exists()


# ---------------------------------------------------------------------------
# Domain separation
# ---------------------------------------------------------------------------
class TestDomainSeparation:
    def test_different_domains_produce_different_macs(self):
        store_a = IntegrityStore(key=b"0123456789abcdef", domain=b"app_a.v1")
        store_b = IntegrityStore(key=b"0123456789abcdef", domain=b"app_b.v1")
        data = {"shared": "payload"}
        assert store_a.sign(data) != store_b.sign(data)

    def test_same_domain_same_mac(self):
        store1 = IntegrityStore(key=b"0123456789abcdef", domain=b"v1")
        store2 = IntegrityStore(key=b"0123456789abcdef", domain=b"v1")
        assert store1.sign({"x": 1}) == store2.sign({"x": 1})


# ---------------------------------------------------------------------------
# Unkeyed mode (degraded — no integrity guarantee)
# ---------------------------------------------------------------------------
class TestUnkeyedMode:
    def test_unkeyed_save_load_no_mac(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path))
        store.save("item", {"value": 1})
        assert store.load("item") == {"value": 1}
        assert not (tmp_path / "item.json.mac").exists()

    def test_unkeyed_no_integrity_guarantee(self, tmp_path):
        store = IntegrityStore(base_dir=str(tmp_path))
        store.save("item", {"value": "original"})
        (tmp_path / "item.json").write_text('{"value":"tampered"}')
        assert store.load("item") == {"value": "tampered"}
