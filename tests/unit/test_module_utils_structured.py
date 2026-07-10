"""Behavioral unit tests for the Ansible module_utils structured-output helpers.

Targets ``strip_code_fences`` and ``parse_structured`` from the collection's
``module_utils/gludd.py``. That file lives OUTSIDE the importable ``general_ludd``
package (it ships inside the Ansible collection tree), so we load it directly off
its filesystem path with importlib rather than via a normal import.

These are the parse/fence-strip primitives every one of the 3 new modules
(gludd_langchain_generate / gludd_langgraph_workflow / gludd_langgraph_decision)
relies on, so their contract is covered here once:

  * parse_structured ALWAYS returns a (obj, reason) tuple and NEVER raises.
  * On success: (obj, None).  On failure: (None, <non-empty reason str>).
  * strip_code_fences removes ```json / ``` fences and leaves bare text alone,
    and never raises on odd input.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

# tests/unit/<file> -> parents[2] == repo root
ROOT = Path(__file__).resolve().parents[2]
MODULE_UTILS_PATH = (
    ROOT
    / "collections"
    / "ansible_collections"
    / "general_ludd"
    / "agent"
    / "plugins"
    / "module_utils"
    / "gludd.py"
)


def _load_module_utils() -> ModuleType:
    """Import the collection module_utils/gludd.py off its filesystem path.

    It is not on sys.path (it lives in the collection tree), so a plain
    ``import`` would not resolve it. importlib.util gives us the module object
    without polluting the package namespace.
    """
    spec = importlib.util.spec_from_file_location(
        "_gludd_module_utils_under_test", MODULE_UTILS_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gludd_mu() -> ModuleType:
    return _load_module_utils()


# --------------------------------------------------------------------------- #
# strip_code_fences
# --------------------------------------------------------------------------- #


class TestStripCodeFences:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # ```json fenced block -> inner body, stripped.
            ('```json\n{"a": 1}\n```', '{"a": 1}'),
            # bare ``` fence (no language hint).
            ('```\n{"a": 1}\n```', '{"a": 1}'),
            # language hint other than json.
            ("```python\nprint(1)\n```", "print(1)"),
            # CRLF line endings inside the fence.
            ('```json\r\n{"a": 1}\r\n```', '{"a": 1}'),
            # trailing whitespace / newlines around the whole block.
            ('  ```json\n{"a": 1}\n```  \n', '{"a": 1}'),
            # multi-line body is preserved (only the fences are removed).
            ("```\nline1\nline2\n```", "line1\nline2"),
        ],
    )
    def test_strips_fences(self, gludd_mu: ModuleType, raw: str, expected: str):
        assert gludd_mu.strip_code_fences(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # Bare text with no surrounding fence -> returned stripped, unchanged.
            ('{"a": 1}', '{"a": 1}'),
            ("  hello world  ", "hello world"),
            ("", ""),
            # A lone/odd backtick line that is not a full fence pair is left alone
            # (just stripped of outer whitespace).
            ("```not a real fence", "```not a real fence"),
            # Inline backticks (not a fenced block) are untouched.
            ("use `code` here", "use `code` here"),
        ],
    )
    def test_leaves_bare_text_alone(
        self, gludd_mu: ModuleType, raw: str, expected: str
    ):
        assert gludd_mu.strip_code_fences(raw) == expected

    def test_non_str_input_does_not_raise(self, gludd_mu: ModuleType):
        # Documented contract: never raises; non-str is returned as-is.
        sentinel = object()
        assert cast(Any, gludd_mu).strip_code_fences(sentinel) is sentinel
        assert cast(Any, gludd_mu).strip_code_fences(None) is None


# --------------------------------------------------------------------------- #
# parse_structured
# --------------------------------------------------------------------------- #


class TestParseStructuredSuccess:
    @pytest.mark.parametrize(
        ("raw", "expected_obj"),
        [
            # Fenced JSON object.
            ('```json\n{"decision": "canary"}\n```', {"decision": "canary"}),
            # Bare JSON object.
            ('{"decision": "canary", "rationale": "small"}',
             {"decision": "canary", "rationale": "small"}),
            # Bare JSON array.
            ("[1, 2, 3]", [1, 2, 3]),
            # JSON scalar (string).
            ('"hello"', "hello"),
            # JSON scalar (number / bool / null).
            ("42", 42),
            ("true", True),
            ("null", None),
            # Fenced bare ``` (no language hint).
            ('```\n{"x": true}\n```', {"x": True}),
        ],
    )
    def test_valid_inputs_return_obj_and_no_reason(
        self, gludd_mu: ModuleType, raw: str, expected_obj
    ):
        obj, reason = gludd_mu.parse_structured(raw)
        assert reason is None
        assert obj == expected_obj


class TestParseStructuredFailure:
    @pytest.mark.parametrize(
        "raw",
        [
            # Prose wrapped around JSON with no fence -> not valid JSON as a whole.
            'Here is the answer: {"decision": "canary"} hope that helps',
            # Trailing prose after a JSON object.
            '{"a": 1} and some trailing text',
            # Plainly invalid JSON.
            "{not json at all}",
            "{'single': 'quotes'}",  # python-style, not JSON
            # Truncated / unbalanced.
            '{"a": 1',
            # A fenced block whose *body* is not JSON.
            "```json\nthis is not json\n```",
        ],
    )
    def test_invalid_json_returns_none_and_reason(
        self, gludd_mu: ModuleType, raw: str
    ):
        obj, reason = gludd_mu.parse_structured(raw)
        assert obj is None
        assert isinstance(reason, str) and reason  # non-empty reason

    def test_none_input(self, gludd_mu: ModuleType):
        obj, reason = cast(Any, gludd_mu).parse_structured(None)
        assert obj is None
        assert isinstance(reason, str) and "None" in reason

    @pytest.mark.parametrize("raw", ["", "   ", "\n\t  \n", "```\n\n```"])
    def test_empty_inputs(self, gludd_mu: ModuleType, raw: str):
        obj, reason = gludd_mu.parse_structured(raw)
        assert obj is None
        assert isinstance(reason, str) and "empty" in reason.lower()


class TestParseStructuredNeverRaises:
    """Contract guard: parse_structured must not propagate exceptions."""

    @pytest.mark.parametrize(
        "raw",
        [
            None,
            "",
            "garbage",
            '{"ok": 1}',
            "```json\n{}\n```",
            "\x00\x01binary\xff-ish",
        ],
    )
    def test_always_returns_two_tuple(self, gludd_mu: ModuleType, raw):
        result = gludd_mu.parse_structured(raw)
        assert isinstance(result, tuple) and len(result) == 2
        obj, reason = result
        # Exactly one of (obj-present) / (reason-present): success xor failure.
        if reason is None:
            # success path is allowed to produce any obj (incl. None for `null`)
            pass
        else:
            assert obj is None
            assert isinstance(reason, str)

    def test_schema_arg_is_accepted_and_ignored(self, gludd_mu: ModuleType):
        # `schema` is accepted for forward-compat; passing it must not change
        # the parse result nor raise.
        obj, reason = gludd_mu.parse_structured('{"a": 1}', {"a": "int"})
        assert reason is None
        assert obj == {"a": 1}
