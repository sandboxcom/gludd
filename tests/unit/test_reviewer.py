"""Unit tests for ReturnReviewer static/parse methods."""

from __future__ import annotations

import json

from general_ludd.review.reviewer import ReturnReviewer


class TestExtractJsonFromOutput:
    def test_plain_json_passthrough(self):
        text = '{"decision": "complete", "confidence": 0.9}'
        result = ReturnReviewer._extract_json_from_output(text)
        parsed = json.loads(result)
        assert parsed["decision"] == "complete"
        assert parsed["confidence"] == 0.9

    def test_strips_markdown_fence(self):
        text = '```json\n{"decision": "complete"}\n```'
        result = ReturnReviewer._extract_json_from_output(text)
        parsed = json.loads(result)
        assert parsed["decision"] == "complete"

    def test_strips_markdown_fence_no_lang(self):
        text = '```\n{"x": 1}\n```'
        result = ReturnReviewer._extract_json_from_output(text)
        parsed = json.loads(result)
        assert parsed["x"] == 1

    def test_handles_leading_prose(self):
        text = 'Here is my review:\n{"decision": "complete"}'
        result = ReturnReviewer._extract_json_from_output(text)
        parsed = json.loads(result)
        assert parsed["decision"] == "complete"

    def test_handles_trailing_prose(self):
        text = '{"decision": "complete"}\nThat is my review.'
        result = ReturnReviewer._extract_json_from_output(text)
        parsed = json.loads(result)
        assert parsed["decision"] == "complete"

    def test_handles_braces_in_strings(self):
        text = '{"decision": "complete", "reason": "loop }{ ok"}'
        result = ReturnReviewer._extract_json_from_output(text)
        parsed = json.loads(result)
        assert parsed["reason"] == "loop }{ ok"

    def test_nested_objects(self):
        text = '{"outer": {"inner": true, "nums": [1, 2, 3]}}'
        result = ReturnReviewer._extract_json_from_output(text)
        parsed = json.loads(result)
        assert parsed["outer"]["inner"] is True
        assert parsed["outer"]["nums"] == [1, 2, 3]

    def test_no_braces_returns_text(self):
        text = "no json here"
        result = ReturnReviewer._extract_json_from_output(text)
        assert result == text
