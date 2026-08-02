"""Tests for module_utils/output_parser.py — model output parsing utilities."""

from __future__ import annotations

import sys
from types import ModuleType

try:
    from pydantic import BaseModel
except ImportError:
    BaseModel = None  # type: ignore[assignment]


def _import_module() -> ModuleType:
    sys.path.insert(
        0,
        "collections/ansible_collections/general_ludd/agent/plugins",
    )
    try:
        from module_utils import output_parser

        return output_parser
    finally:
        sys.path.pop(0)


# ---------------------------------------------------------------------------
# OutputParser base class
# ---------------------------------------------------------------------------


class TestOutputParser:
    def test_base_class_exists(self) -> None:
        mod = _import_module()
        assert hasattr(mod, "OutputParser")
        assert callable(mod.OutputParser)

    def test_base_class_has_parse_method(self) -> None:
        mod = _import_module()
        parser = mod.OutputParser()
        assert hasattr(parser, "parse")

    def test_base_parse_raises_not_implemented(self) -> None:
        mod = _import_module()
        parser = mod.OutputParser()
        err: NotImplementedError | None = None
        try:
            parser.parse("some text")
        except NotImplementedError as e:
            err = e
        assert err is not None


# ---------------------------------------------------------------------------
# JsonOutputParser — JSON extraction
# ---------------------------------------------------------------------------


class TestJsonOutputParser:
    def test_extracts_json_from_code_block(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser()
        text = 'Here is the result:\n```json\n{"key": "value"}\n```'
        result = parser.parse(text)
        assert result == {"key": "value"}

    def test_extracts_json_object_without_code_block(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser()
        text = 'The answer is {"name": "test", "count": 42}'
        result = parser.parse(text)
        assert result == {"name": "test", "count": 42}

    def test_extracts_json_array(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser()
        text = "Numbers: [1, 2, 3, 4]"
        result = parser.parse(text)
        assert result == [1, 2, 3, 4]

    def test_extracts_nested_json(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser()
        text = '{"outer": {"inner": [{"a": 1}, {"b": 2}]}}'
        result = parser.parse(text)
        assert result == {"outer": {"inner": [{"a": 1}, {"b": 2}]}}

    def test_extracts_booleans_and_null(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser()
        text = '{"flag": true, "other": false, "nothing": null}'
        result = parser.parse(text)
        assert result == {"flag": True, "other": False, "nothing": None}

    def test_raises_on_non_json_text(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser()
        err: ValueError | None = None
        try:
            parser.parse("Just some plain text, no JSON here at all.")
        except ValueError as e:
            err = e
        assert err is not None

    def test_raises_on_malformed_json(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser()
        err: ValueError | None = None
        try:
            parser.parse('{"key": value}')
        except ValueError as e:
            err = e
        assert err is not None


# ---------------------------------------------------------------------------
# JsonOutputParser — schema validation
# ---------------------------------------------------------------------------


class TestJsonOutputParserSchemaValidation:
    def test_validates_against_schema(self) -> None:
        mod = _import_module()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name", "age"],
        }
        parser = mod.JsonOutputParser(schema=schema)
        result = parser.parse('{"name": "Alice", "age": 30}')
        assert result == {"name": "Alice", "age": 30}

    def test_raises_on_schema_violation(self) -> None:
        mod = _import_module()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        }
        parser = mod.JsonOutputParser(schema=schema)
        err: ValueError | None = None
        try:
            parser.parse('{"age": 30}')
        except ValueError as e:
            err = e
        assert err is not None

    def test_raises_on_type_mismatch(self) -> None:
        mod = _import_module()
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        parser = mod.JsonOutputParser(schema=schema)
        err: ValueError | None = None
        try:
            parser.parse('{"count": "not_a_number"}')
        except ValueError as e:
            err = e
        assert err is not None

    def test_schema_none_skips_validation(self) -> None:
        mod = _import_module()
        parser = mod.JsonOutputParser(schema=None)
        result = parser.parse('{"anything": "goes"}')
        assert result == {"anything": "goes"}


# ---------------------------------------------------------------------------
# PydanticOutputParser
# ---------------------------------------------------------------------------


class TestPydanticOutputParser:
    def test_extracts_and_validates_against_model(self) -> None:
        mod = _import_module()

        class Person(BaseModel):
            name: str
            age: int

        parser = mod.PydanticOutputParser(model=Person)
        result = parser.parse('{"name": "Bob", "age": 25}')
        assert isinstance(result, Person)
        assert result.name == "Bob"
        assert result.age == 25

    def test_raises_on_model_validation_failure(self) -> None:
        mod = _import_module()

        class Person(BaseModel):
            name: str
            age: int

        parser = mod.PydanticOutputParser(model=Person)
        err: ValueError | None = None
        try:
            parser.parse('{"name": "Bob"}')
        except ValueError as e:
            err = e
        assert err is not None

    def test_raises_on_non_pydantic_model(self) -> None:
        mod = _import_module()
        err: TypeError | None = None
        try:
            mod.PydanticOutputParser(model=dict)
        except TypeError as e:
            err = e
        assert err is not None

    def test_handles_nested_pydantic_models(self) -> None:
        mod = _import_module()

        class Address(BaseModel):
            city: str
            zip: str

        class User(BaseModel):
            name: str
            address: Address

        parser = mod.PydanticOutputParser(model=User)
        result = parser.parse('{"name": "Carol", "address": {"city": "NYC", "zip": "10001"}}')
        assert isinstance(result, User)
        assert result.address.city == "NYC"

    def test_handles_optional_fields(self) -> None:
        mod = _import_module()

        class Item(BaseModel):
            name: str
            description: str | None = None

        parser = mod.PydanticOutputParser(model=Item)
        result = parser.parse('{"name": "widget"}')
        assert result.name == "widget"
        assert result.description is None


# ---------------------------------------------------------------------------
# MarkdownOutputParser — code blocks
# ---------------------------------------------------------------------------


class TestMarkdownOutputParserCodeBlocks:
    def test_extracts_fenced_code_block(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "```python\nprint('hello')\n```"
        result = parser.extract_code_blocks(text)
        assert result == [("python", "print('hello')")]

    def test_extracts_multiple_code_blocks(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = '```python\nx = 1\n```\n\ntext\n\n```json\n{"a": 1}\n```'
        result = parser.extract_code_blocks(text)
        assert len(result) == 2
        assert result[0] == ("python", "x = 1")
        assert result[1] == ("json", '{"a": 1}')

    def test_code_block_no_language(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "```\nplain content\n```"
        result = parser.extract_code_blocks(text)
        assert result == [("", "plain content")]

    def test_no_code_blocks_returns_empty(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "Just some plain text."
        result = parser.extract_code_blocks(text)
        assert result == []

    def test_ignores_indented_blocks(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "    indented = code  # not fenced"
        result = parser.extract_code_blocks(text)
        assert result == []


# ---------------------------------------------------------------------------
# MarkdownOutputParser — lists
# ---------------------------------------------------------------------------


class TestMarkdownOutputParserLists:
    def test_extracts_unordered_list(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "- item one\n- item two\n- item three"
        result = parser.extract_lists(text)
        assert len(result) == 1
        assert result[0] == ["item one", "item two", "item three"]

    def test_extracts_ordered_list(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "1. first\n2. second\n3. third"
        result = parser.extract_lists(text)
        assert len(result) == 1
        assert result[0] == ["first", "second", "third"]

    def test_extracts_star_lists(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "* alpha\n* beta\n* gamma"
        result = parser.extract_lists(text)
        assert len(result) == 1
        assert result[0] == ["alpha", "beta", "gamma"]

    def test_extracts_multiple_lists(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "- a\n- b\n\nSome text\n\n1. first\n2. second"
        result = parser.extract_lists(text)
        assert len(result) == 2
        assert result[0] == ["a", "b"]
        assert result[1] == ["first", "second"]

    def test_no_lists_returns_empty(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "No list items here."
        result = parser.extract_lists(text)
        assert result == []


# ---------------------------------------------------------------------------
# MarkdownOutputParser — tables
# ---------------------------------------------------------------------------


class TestMarkdownOutputParserTables:
    def test_extracts_simple_table(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |"
        result = parser.extract_tables(text)
        assert len(result) == 1
        assert result[0]["headers"] == ["Name", "Age"]
        assert result[0]["rows"] == [["Alice", "30"], ["Bob", "25"]]

    def test_extracts_table_with_alignment(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "| Left | Center | Right |\n|:-----|:------:|------:|\n| a | b | c |"
        result = parser.extract_tables(text)
        assert len(result) == 1
        assert result[0]["headers"] == ["Left", "Center", "Right"]

    def test_no_table_returns_empty(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "No table here."
        result = parser.extract_tables(text)
        assert result == []

    def test_strips_whitespace_from_cells(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "| Key | Value |\n|-----|-------|\n|  foo  |  bar  |"
        result = parser.extract_tables(text)
        assert result[0]["rows"] == [["foo", "bar"]]


# ---------------------------------------------------------------------------
# MarkdownOutputParser — parse (combined)
# ---------------------------------------------------------------------------


class TestMarkdownOutputParserParse:
    def test_parse_returns_all_sections(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        text = "```python\nx = 1\n```\n\n- item1\n- item2\n\n| Col |\n|-----|\n| val |"
        result = parser.parse(text)
        assert "code_blocks" in result
        assert "lists" in result
        assert "tables" in result
        assert result["code_blocks"] == [("python", "x = 1")]
        assert result["lists"] == [["item1", "item2"]]
        assert len(result["tables"]) == 1

    def test_parse_empty_text(self) -> None:
        mod = _import_module()
        parser = mod.MarkdownOutputParser()
        result = parser.parse("")
        assert result == {"code_blocks": [], "lists": [], "tables": []}


# ---------------------------------------------------------------------------
# parse_model_response factory
# ---------------------------------------------------------------------------


class TestParseModelResponse:
    def test_returns_json_parser(self) -> None:
        mod = _import_module()
        result = mod.parse_model_response('{"key": "val"}', "json")
        assert result == {"key": "val"}

    def test_returns_pydantic_result(self) -> None:
        mod = _import_module()

        class Animal(BaseModel):
            species: str
            legs: int

        result = mod.parse_model_response(
            '{"species": "dog", "legs": 4}',
            "pydantic",
            model=Animal,
        )
        assert isinstance(result, Animal)
        assert result.species == "dog"

    def test_returns_markdown_result(self) -> None:
        mod = _import_module()
        result = mod.parse_model_response(
            "- one\n- two\n\n```python\nx=1\n```\n\n| H |\n|---|\n| v |",
            "markdown",
        )
        assert "code_blocks" in result
        assert "lists" in result
        assert "tables" in result

    def test_unknown_parser_type_raises(self) -> None:
        mod = _import_module()
        err: ValueError | None = None
        try:
            mod.parse_model_response("text", "unknown_type")
        except ValueError as e:
            err = e
        assert err is not None

    def test_default_is_json(self) -> None:
        mod = _import_module()
        result = mod.parse_model_response("[1, 2, 3]", "json")
        assert result == [1, 2, 3]

    def test_json_with_schema(self) -> None:
        mod = _import_module()
        schema = {"type": "array", "items": {"type": "integer"}}
        result = mod.parse_model_response("[1, 2, 3]", "json", schema=schema)
        assert result == [1, 2, 3]
