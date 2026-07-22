"""Structural tests for connectors/servicenow.py — ServiceNowSource."""

from __future__ import annotations

from general_ludd.connectors.servicenow import (
    _LINK_NEXT_RE,
    _STATE_MAP,
    ServiceNowSource,
    _parse_link_next,
    _state_code_to_label,
)


class TestServiceNowConstants:
    def test_state_map_keys(self) -> None:
        assert "1" in _STATE_MAP
        assert "2" in _STATE_MAP
        assert "3" in _STATE_MAP
        assert "6" in _STATE_MAP

    def test_state_map_values(self) -> None:
        assert _STATE_MAP["1"] == "new"
        assert _STATE_MAP["2"] == "in_progress"
        assert _STATE_MAP["3"] == "on_hold"

    def test_state_code_lookup_known(self) -> None:
        assert _state_code_to_label("1") == "new"

    def test_state_code_lookup_unknown(self) -> None:
        assert _state_code_to_label("99") == "unknown"

    def test_link_next_regex_match(self) -> None:
        m = _LINK_NEXT_RE.search('<https://example.com?offset=50>; rel="next"')
        assert m is not None

    def test_link_next_regex_no_match(self) -> None:
        m = _LINK_NEXT_RE.search("no link header here")
        assert m is None

    def test_parse_link_next_extracts_url(self) -> None:
        url = _parse_link_next('<https://demo.service-now.com/api?limit=50>; rel="next"')
        assert url == "https://demo.service-now.com/api?limit=50"

    def test_parse_link_next_none(self) -> None:
        assert _parse_link_next(None) is None


class TestServiceNowSource:
    def test_source_importable(self) -> None:
        assert ServiceNowSource is not None

    def test_kind_is_ticket(self) -> None:
        assert ServiceNowSource.KIND == "tickets"
