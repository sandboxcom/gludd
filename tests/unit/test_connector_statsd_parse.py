"""Unit tests for StatsdParseSource (pure line-protocol parser, no network)."""

from __future__ import annotations

import pytest

from general_ludd.connectors.statsd_parse import (
    StatsdParseError,
    StatsdParseSource,
    _dispatch,
    _strip_name,
)


def test_kind_and_name() -> None:
    src = StatsdParseSource({"name": "sp"})
    assert StatsdParseSource.KIND == "metrics"
    assert src.name == "sp"


def test_health_always_ok() -> None:
    src = StatsdParseSource()
    h = src.health()
    assert h["ok"] is True
    assert "parser" in h["detail"]


def test_parse_counter() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("page.views:1|c")
    assert rec["message"] == "page.views"
    assert rec["value"] == 1.0
    assert rec["level_or_status"] == "c"
    assert rec["kind"] == "metrics"
    assert rec["source"] == "statsd_parse"
    assert rec["ts"] is None
    assert rec["raw"] == "page.views:1|c"


def test_parse_gauge() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("temperature:40|g")
    assert rec["level_or_status"] == "g"
    assert rec["value"] == 40.0


def test_parse_gauge_delta_preserves_sign() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("queue.depth:-3|g")
    assert rec["value"] == -3.0
    assert rec["labels"]["delta"] == "-"


def test_parse_timer() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("svc.latency:320|ms")
    assert rec["level_or_status"] == "ms"
    assert rec["value"] == 320.0


def test_parse_sample_rate() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("api.hits:1|c|@0.1")
    assert rec["labels"]["sample_rate"] == pytest.approx(0.1)
    assert rec["value"] == 1.0


def test_parse_tags() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("req.count:5|c|#region:us-east,env:prod,canary")
    assert rec["labels"]["region"] == "us-east"
    assert rec["labels"]["env"] == "prod"
    assert rec["labels"]["canary"] == ""


def test_parse_tags_with_rate() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("req.count:5|c|@0.5|#host:web1")
    assert rec["labels"]["sample_rate"] == pytest.approx(0.5)
    assert rec["labels"]["host"] == "web1"


def test_parse_set_member() -> None:
    src = StatsdParseSource()
    rec = src.parse_line("users.unique:user-42|s")
    assert rec["level_or_status"] == "s"
    assert rec["value"] is None
    assert rec["labels"]["set_value"] == "user-42"


def test_normalized_keys() -> None:
    src = StatsdParseSource()
    required = {"ts", "source", "kind", "level_or_status", "message", "value", "labels", "raw"}
    rec = src.parse_line("m:1|c|#a:b")
    assert required <= set(rec)


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no_colon|c",
        ":1|c",
        "m:1",
        "m:1|",
        "m:1|x",
        "m:notnum|c",
        "m:1|c|@notnum",
    ],
)
def test_malformed_lines_raise(bad: str) -> None:
    src = StatsdParseSource()
    with pytest.raises(StatsdParseError):
        src.parse_line(bad)


def test_query_parses_list_of_lines() -> None:
    src = StatsdParseSource()
    lines = [
        "a:1|c",
        "b:2|g",
        "c:3|ms|#x:y",
    ]
    recs = src.query({"lines": lines})
    assert len(recs) == 3
    assert [r["message"] for r in recs] == ["a", "b", "c"]
    assert recs[2]["labels"]["x"] == "y"


def test_query_requires_lines() -> None:
    src = StatsdParseSource()
    with pytest.raises(ValueError):
        src.query({})


def test_query_skips_malformed_by_default() -> None:
    src = StatsdParseSource()
    recs = src.query({"lines": ["good:1|c", "garbage", "g2:2|g"]})
    assert [r["message"] for r in recs] == ["good", "g2"]


def test_query_keep_errors_emits_error_record() -> None:
    src = StatsdParseSource()
    recs = src.query({"lines": ["good:1|c", "garbage"], "keep_errors": True})
    assert len(recs) == 2
    assert recs[1]["level_or_status"] == "error"
    assert recs[1]["message"] == "parse_error"
    assert recs[1]["raw"] == "garbage"


def test_strict_mode_raises_on_malformed() -> None:
    src = StatsdParseSource({"strict": True})
    with pytest.raises(StatsdParseError):
        src.query({"lines": ["good:1|c", "garbage"]})


def test_query_limit() -> None:
    src = StatsdParseSource()
    recs = src.query({"lines": ["a:1|c", "b:2|c", "c:3|c"], "limit": 2})
    assert len(recs) == 2


def test_legacy_dispatch_wrapper_is_fail_soft_and_normalizes_name() -> None:
    records = _dispatch("stats.gauges.app.requests:1|c|@0.1|#host:web1")

    assert len(records) == 1
    assert records[0]["message"] == "app.requests"
    assert records[0]["value"] == 1.0
    assert records[0]["labels"]["metric_type"] == "counter"
    assert records[0]["labels"]["sample_rate"] == "0.1"
    assert records[0]["labels"]["host"] == "web1"
    assert _dispatch("not a statsd line") == []


def test_legacy_name_stripper_only_removes_known_statsd_prefixes() -> None:
    assert _strip_name("stats.gauges.app.requests") == "app.requests"
    assert _strip_name("service.requests") == "service.requests"
