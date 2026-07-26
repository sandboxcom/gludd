"""Tests for push-side receiver-format parsers (Fluent Forward / Beats Lumberjack / GELF).

These are pure, fail-soft parsers: malformed input -> [] (never raise), oversized
input -> []. Each emits plain dicts with the normalized record shape
(ts, source, kind, level_or_status, message, value, labels, raw).
"""

from __future__ import annotations

import json
from typing import Any, cast

from general_ludd.connectors.ingest_formats import (
    MAX_PAYLOAD_BYTES,
    _detect_format,
    parse_beats_lumberjack,
    parse_fluent_forward,
    parse_gelf,
)

RECORD_KEYS = {
    "ts",
    "source",
    "kind",
    "level_or_status",
    "message",
    "value",
    "labels",
    "raw",
}


def _assert_shape(rec: dict[str, object]) -> None:
    assert set(rec.keys()) == RECORD_KEYS, rec.keys()
    assert isinstance(rec["labels"], dict)
    assert rec["kind"] == "log"


def test_detect_format_classifies_json_csv_and_plain_payloads() -> None:
    assert _detect_format('{"key": "value"}') == "json"
    assert _detect_format("col1,col2\nval1,val2") == "csv"
    assert _detect_format("just plain text here") == "plain"


def test_detect_format_accepts_bytes_and_fails_soft_for_empty_payload() -> None:
    assert _detect_format(b"[1, 2, 3]") == "json"
    assert _detect_format(b"") == "plain"


# --------------------------------------------------------------------------- #
# Fluent Forward (JSON mode)                                                   #
# --------------------------------------------------------------------------- #


class TestParseFluentForward:
    def test_json_mode_array_form(self) -> None:
        payload = json.dumps(
            [
                "myapp.access",
                [
                    [1700000000, {"message": "GET /", "level": "info", "host": "web1"}],
                    [1700000001, {"message": "GET /x", "level": "warn"}],
                ],
            ]
        ).encode()
        records = parse_fluent_forward(payload)
        assert len(records) == 2
        for r in records:
            _assert_shape(r)
            assert r["source"] == "myapp.access"
        assert records[0]["ts"] == 1700000000
        assert records[0]["message"] == "GET /"
        assert records[0]["level_or_status"] == "info"
        # tag + record fields carried into labels
        assert records[0]["labels"]["tag"] == "myapp.access"
        assert records[0]["labels"]["host"] == "web1"
        # the original record is preserved in raw
        assert records[0]["raw"]["message"] == "GET /"

    def test_record_without_message_uses_empty_string(self) -> None:
        payload = json.dumps(["t", [[1, {"k": "v"}]]]).encode()
        records = parse_fluent_forward(payload)
        assert len(records) == 1
        assert records[0]["message"] == ""
        assert records[0]["labels"]["k"] == "v"

    def test_string_timestamp_passthrough(self) -> None:
        payload = json.dumps(["t", [["2023-01-01T00:00:00Z", {"message": "hi"}]]]).encode()
        records = parse_fluent_forward(payload)
        assert len(records) == 1
        assert records[0]["ts"] == "2023-01-01T00:00:00Z"

    def test_malformed_not_json_returns_empty(self) -> None:
        assert parse_fluent_forward(b"\x00\x01 not json") == []

    def test_malformed_wrong_shape_returns_empty(self) -> None:
        # object instead of [tag, entries] array
        assert parse_fluent_forward(json.dumps({"tag": "x"}).encode()) == []
        # entries not a list
        assert parse_fluent_forward(json.dumps(["x", "nope"]).encode()) == []
        # entry not a [time, record] pair
        assert parse_fluent_forward(json.dumps(["x", [[1]]]).encode()) == []
        # record not an object
        assert parse_fluent_forward(json.dumps(["x", [[1, "str"]]]).encode()) == []

    def test_empty_entries_returns_empty(self) -> None:
        assert parse_fluent_forward(json.dumps(["x", []]).encode()) == []

    def test_oversized_returns_empty(self) -> None:
        big = b"[" + b"a" * (MAX_PAYLOAD_BYTES + 1)
        assert parse_fluent_forward(big) == []

    def test_skips_bad_entries_keeps_good(self) -> None:
        payload = json.dumps(
            [
                "t",
                [
                    [1, {"message": "ok"}],
                    [2],  # bad
                    [3, "nope"],  # bad
                    [4, {"message": "ok2"}],
                ],
            ]
        ).encode()
        records = parse_fluent_forward(payload)
        assert [r["message"] for r in records] == ["ok", "ok2"]

    def test_non_bytes_returns_empty(self) -> None:
        assert cast(Any, parse_fluent_forward)("a string") == []


# --------------------------------------------------------------------------- #
# Beats / Lumberjack v2 (decoded JSON events)                                 #
# --------------------------------------------------------------------------- #


class TestParseBeatsLumberjack:
    def test_basic_events(self) -> None:
        frames = [
            {
                "message": "started",
                "@timestamp": "2023-01-01T00:00:00.000Z",
                "log": {"level": "info"},
                "host": {"name": "node-a"},
                "agent": {"type": "filebeat"},
            },
            {"message": "stopped", "host": {"name": "node-b"}},
        ]
        records = parse_beats_lumberjack(frames)
        assert len(records) == 2
        for r in records:
            _assert_shape(r)
            assert r["source"] == "beats"
        assert records[0]["message"] == "started"
        assert records[0]["ts"] == "2023-01-01T00:00:00.000Z"
        assert records[0]["level_or_status"] == "info"
        # beat/host fields carried into labels
        assert records[0]["labels"]["host"] == "node-a"
        assert records[0]["labels"]["beat"] == "filebeat"

    def test_flat_host_and_level(self) -> None:
        frames = [{"message": "m", "host": "h1", "level": "warn"}]
        records = parse_beats_lumberjack(frames)
        assert records[0]["labels"]["host"] == "h1"
        assert records[0]["level_or_status"] == "warn"

    def test_message_fallback_to_log_field(self) -> None:
        frames = [{"log": {"file": {"path": "/var/log/x"}}, "event": {"original": "raw line"}}]
        records = parse_beats_lumberjack(frames)
        assert records[0]["message"] == "raw line"

    def test_skips_non_dict_events(self) -> None:
        frames = ["nope", 123, {"message": "ok"}, None]
        records = cast(Any, parse_beats_lumberjack)(frames)
        assert len(records) == 1
        assert records[0]["message"] == "ok"

    def test_empty_returns_empty(self) -> None:
        assert parse_beats_lumberjack([]) == []

    def test_non_list_returns_empty(self) -> None:
        assert cast(Any, parse_beats_lumberjack)("not a list") == []
        assert cast(Any, parse_beats_lumberjack)(None) == []

    def test_oversized_event_count_returns_empty(self) -> None:
        frames = [{"message": "m"}] * 100001
        assert parse_beats_lumberjack(frames) == []


# --------------------------------------------------------------------------- #
# GELF (Graylog)                                                              #
# --------------------------------------------------------------------------- #


class TestParseGelf:
    def test_basic_gelf_json(self) -> None:
        payload = json.dumps(
            {
                "version": "1.1",
                "host": "example.org",
                "short_message": "A short message",
                "full_message": "Backtrace here\n\nmore",
                "timestamp": 1385053862.3072,
                "level": 1,
                "_user_id": 9001,
            }
        ).encode()
        records = parse_gelf(payload)
        assert len(records) == 1
        r = records[0]
        _assert_shape(r)
        assert r["source"] == "example.org"
        assert r["message"] == "A short message"
        assert r["ts"] == 1385053862.3072
        # syslog level 1 -> "alert"
        assert r["level_or_status"] == "alert"
        # additional underscore fields -> labels (stripped leading underscore)
        assert r["labels"]["user_id"] == 9001
        assert r["labels"]["host"] == "example.org"

    def test_level_name_mapping(self) -> None:
        for num, name in [
            (0, "emergency"),
            (3, "error"),
            (6, "info"),
            (7, "debug"),
        ]:
            payload = json.dumps({"short_message": "x", "level": num}).encode()
            records = parse_gelf(payload)
            assert records[0]["level_or_status"] == name

    def test_unknown_level_passthrough(self) -> None:
        payload = json.dumps({"short_message": "x", "level": 99}).encode()
        records = parse_gelf(payload)
        assert records[0]["level_or_status"] == 99

    def test_default_level_when_absent(self) -> None:
        payload = json.dumps({"short_message": "x"}).encode()
        records = parse_gelf(payload)
        # GELF default level is 1 (alert) per spec
        assert records[0]["level_or_status"] == "alert"

    def test_chunked_magic_single_caller_supplied_chunks_assembled(self) -> None:
        # GELF chunked magic bytes 0x1e 0x0f. We assemble only if all chunks present.
        inner = json.dumps({"short_message": "assembled", "host": "h"}).encode()
        message_id = b"12345678"
        total = 2
        half = len(inner) // 2
        chunk0 = b"\x1e\x0f" + message_id + bytes([0, total]) + inner[:half]
        chunk1 = b"\x1e\x0f" + message_id + bytes([1, total]) + inner[half:]
        records = parse_gelf(chunk0 + chunk1)
        assert len(records) == 1
        assert records[0]["message"] == "assembled"

    def test_chunked_incomplete_returns_empty(self) -> None:
        inner = json.dumps({"short_message": "x"}).encode()
        message_id = b"12345678"
        total = 3
        chunk0 = b"\x1e\x0f" + message_id + bytes([0, total]) + inner
        # only 1 of 3 chunks -> cannot assemble -> []
        assert parse_gelf(chunk0) == []

    def test_malformed_not_json_returns_empty(self) -> None:
        assert parse_gelf(b"\x00not json{") == []

    def test_non_object_json_returns_empty(self) -> None:
        assert parse_gelf(json.dumps([1, 2, 3]).encode()) == []

    def test_missing_short_message_returns_empty(self) -> None:
        assert parse_gelf(json.dumps({"host": "h"}).encode()) == []

    def test_oversized_returns_empty(self) -> None:
        assert parse_gelf(b"{" + b"a" * (MAX_PAYLOAD_BYTES + 1)) == []

    def test_non_bytes_returns_empty(self) -> None:
        assert cast(Any, parse_gelf)("a string") == []
