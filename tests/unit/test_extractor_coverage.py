"""Tests for ASTBlockExtractor covering tree-sitter and fallback paths."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from general_ludd.code_intelligence.extractor import ASTBlockExtractor, _get_parser


class TestGetParser:
    def setup_method(self):
        import general_ludd.code_intelligence.extractor as mod
        mod._LANGUAGE_PARSERS.clear()

    def test_returns_cached_parser(self):
        fake = MagicMock()
        import general_ludd.code_intelligence.extractor as mod
        mod._LANGUAGE_PARSERS["python"] = fake
        assert _get_parser("python") is fake

    @patch("general_ludd.code_intelligence.extractor.tree_sitter_python", create=True)
    @patch("general_ludd.code_intelligence.extractor.tree_sitter", create=True)
    def test_creates_and_caches_parser(self, mock_ts, mock_tspy):
        with patch.dict("sys.modules", {
            "tree_sitter": MagicMock(),
            "tree_sitter_python": MagicMock(),
        }):
            mock_parser_cls = MagicMock()
            mock_lang_cls = MagicMock()
            mock_ts_mod = MagicMock()
            mock_ts_mod.Parser = mock_parser_cls
            mock_ts_mod.Language = mock_lang_cls
            mock_tspy_mod = MagicMock()
            mock_tspy_mod.language.return_value = "pylang"

            with patch("general_ludd.code_intelligence.extractor._get_parser") as mock_gp:
                mock_gp.return_value = MagicMock()
                ext = ASTBlockExtractor()
                result = ext.extract_blocks("def foo(): pass", "python")
                assert isinstance(result, list)

    def test_returns_none_on_import_error(self):
        import general_ludd.code_intelligence.extractor as mod
        mod._LANGUAGE_PARSERS.clear()
        with patch.dict("sys.modules", {}), patch("builtins.__import__", side_effect=ImportError):
            result = _get_parser("python")
            assert result is None


class TestExtractBlocksFallback:
    def test_fallback_when_parser_none(self):
        ext = ASTBlockExtractor()
        with patch("general_ludd.code_intelligence.extractor._get_parser", return_value=None):
            result = ext.extract_blocks("class Foo:\n    def bar(self):\n        pass", "python")
        assert len(result) == 2
        assert result[0]["name"] == "Foo"
        assert result[0]["type"] == "class"
        assert result[1]["name"] == "bar"
        assert result[1]["type"] == "function"

    def test_fallback_no_matches(self):
        ext = ASTBlockExtractor()
        with patch("general_ludd.code_intelligence.extractor._get_parser", return_value=None):
            result = ext.extract_blocks("x = 1\ny = 2\n", "python")
        assert result == []

    def test_fallback_with_def_and_class(self):
        src = "def hello():\n    pass\n\nclass World:\n    pass\n"
        ext = ASTBlockExtractor()
        with patch("general_ludd.code_intelligence.extractor._get_parser", return_value=None):
            result = ext.extract_blocks(src, "python")
        assert len(result) == 2
        assert result[0]["name"] == "hello"
        assert result[0]["type"] == "function"
        assert result[1]["name"] == "World"
        assert result[1]["type"] == "class"

    def test_parse_exception_falls_back(self):
        ext = ASTBlockExtractor()
        fake_parser = MagicMock()
        fake_parser.parse.side_effect = RuntimeError("boom")
        with patch("general_ludd.code_intelligence.extractor._get_parser", return_value=fake_parser):
            result = ext.extract_blocks("def foo(): pass", "python")
        assert isinstance(result, list)


class TestWalkTree:
    def _make_node(self, node_type, children=None, fields=None, start_byte=0, end_byte=0,
                   start_point=(0, 0), end_point=(0, 0)):
        node = MagicMock()
        node.type = node_type
        node.children = children or []
        node.start_byte = start_byte
        node.end_byte = end_byte
        node.start_point = start_point
        node.end_point = end_point
        node.child_by_field_name = MagicMock(return_value=fields)
        return node

    def test_function_definition(self):
        source = "def my_func():\n    pass"
        name_node = self._make_node("identifier", start_byte=4, end_byte=11)
        func_node = self._make_node(
            "function_definition",
            fields=name_node,
            start_byte=0,
            end_byte=len(source),
            start_point=(0, 0),
            end_point=(1, 8),
        )
        root = self._make_node("module", children=[func_node])

        ext = ASTBlockExtractor()
        blocks: list = []
        ext._walk_tree(root, source, blocks, parent=None)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "my_func"
        assert blocks[0]["type"] == "function"
        assert blocks[0]["parent"] is None

    def test_function_definition_with_parent_is_method(self):
        source = "def method(self):\n    pass"
        name_node = self._make_node("identifier", start_byte=4, end_byte=10)
        func_node = self._make_node(
            "function_definition",
            fields=name_node,
            start_byte=0,
            end_byte=len(source),
            start_point=(0, 0),
            end_point=(1, 8),
        )
        root = self._make_node("module", children=[func_node])

        ext = ASTBlockExtractor()
        blocks: list = []
        ext._walk_tree(root, source, blocks, parent="MyClass")
        assert blocks[0]["type"] == "method"
        assert blocks[0]["parent"] == "MyClass"

    def test_class_definition_with_body(self):
        source = 'class Foo:\n    def bar(self):\n        """doc."""\n        pass'
        bar_name = self._make_node("identifier", start_byte=19, end_byte=22)
        bar_func = self._make_node(
            "function_definition",
            fields=bar_name,
            start_byte=14,
            end_byte=49,
            start_point=(1, 4),
            end_point=(3, 12),
        )
        body_node = self._make_node("block", children=[bar_func])

        class_name = self._make_node("identifier", start_byte=6, end_byte=9)
        class_node = self._make_node(
            "class_definition",
            children=[],
            fields={"name": class_name, "body": body_node},
            start_byte=0,
            end_byte=len(source),
            start_point=(0, 0),
            end_point=(3, 12),
        )
        class_node.child_by_field_name = lambda field: {
            "name": class_name,
            "body": body_node,
        }.get(field)

        root = self._make_node("module", children=[class_node])

        ext = ASTBlockExtractor()
        blocks: list = []
        ext._walk_tree(root, source, blocks, parent=None)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "class"
        assert blocks[0]["name"] == "Foo"
        assert blocks[1]["type"] == "method"
        assert blocks[1]["parent"] == "Foo"

    def test_decorated_definition_wraps_function(self):
        source = "@decorator\ndef my_func():\n    pass"
        name_node = self._make_node("identifier", start_byte=15, end_byte=22)
        func_inner = self._make_node(
            "function_definition",
            fields=name_node,
            start_byte=11,
            end_byte=len(source),
            start_point=(1, 0),
            end_point=(2, 8),
        )
        decorator = self._make_node("decorator")
        decorated = self._make_node("decorated_definition", children=[decorator, func_inner])

        root = self._make_node("module", children=[decorated])

        ext = ASTBlockExtractor()
        blocks: list = []
        ext._walk_tree(root, source, blocks, parent=None)
        assert len(blocks) == 1
        assert blocks[0]["name"] == "my_func"

    def test_block_and_body_nodes_recurse(self):
        inner = self._make_node("expression")
        block = self._make_node("block", children=[inner])
        body = self._make_node("body", children=[block])
        root = self._make_node("module", children=[body])

        ext = ASTBlockExtractor()
        blocks: list = []
        ext._walk_tree(root, "source", blocks, parent=None)
        assert blocks == []

    def test_node_without_type_attribute(self):
        child = MagicMock(spec=[])
        child.children = []
        root = MagicMock()
        root.children = [child]
        root.type = "module"

        ext = ASTBlockExtractor()
        blocks: list = []
        ext._walk_tree(root, "source", blocks, parent=None)
        assert blocks == []


class TestExtractDocstring:
    def test_with_docstring(self):
        string_node = MagicMock()
        string_node.type = "string"
        string_node.start_byte = 12
        string_node.end_byte = 24
        source = 'def foo():\n    """hello"""\n    pass'

        expr_stmt = MagicMock()
        expr_stmt.type = "expression_statement"
        expr_stmt.children = [string_node]

        body = MagicMock()
        body.children = [expr_stmt]

        node = MagicMock()
        node.child_by_field_name = MagicMock(return_value=body)

        ext = ASTBlockExtractor()
        result = ext._extract_docstring(node, source)
        assert result is not None
        assert "hello" in result

    def test_without_body(self):
        node = MagicMock()
        node.child_by_field_name = MagicMock(return_value=None)

        ext = ASTBlockExtractor()
        assert ext._extract_docstring(node, "source") is None

    def test_without_expression_statement(self):
        other_stmt = MagicMock()
        other_stmt.type = "assignment"
        other_stmt.children = []

        body = MagicMock()
        body.children = [other_stmt]

        node = MagicMock()
        node.child_by_field_name = MagicMock(return_value=body)

        ext = ASTBlockExtractor()
        assert ext._extract_docstring(node, "source") is None


class TestExtractBases:
    def test_with_bases(self):
        source = "class Foo(Base1, Base2):\n    pass"
        base1 = MagicMock()
        base1.type = "identifier"
        base1.start_byte = 10
        base1.end_byte = 15
        base2 = MagicMock()
        base2.type = "identifier"
        base2.start_byte = 17
        base2.end_byte = 22

        arg_list = MagicMock()
        arg_list.type = "argument_list"
        arg_list.children = [base1, base2, MagicMock(type="(", spec=["type"])]

        node = MagicMock()
        node.children = [arg_list]
        ext = ASTBlockExtractor()
        result = ext._extract_bases(node, source)
        assert result == ["Base1", "Base2"]

    def test_without_argument_list(self):
        node = MagicMock()
        node.children = [MagicMock(type="identifier")]

        ext = ASTBlockExtractor()
        result = ext._extract_bases(node, "source")
        assert result == []


class TestExtractFallback:
    def test_extracts_class_and_def(self):
        src = "class MyClass:\n    def my_method(self):\n        pass\n"
        result = ASTBlockExtractor._extract_fallback(src)
        assert len(result) == 2
        assert result[0]["name"] == "MyClass"
        assert result[0]["type"] == "class"
        assert result[1]["name"] == "my_method"
        assert result[1]["type"] == "function"

    def test_no_matches(self):
        result = ASTBlockExtractor._extract_fallback("# just a comment\nx = 42\n")
        assert result == []

    def test_class_has_correct_line_numbers(self):
        src = "\nclass Foo:\n    pass\n"
        result = ASTBlockExtractor._extract_fallback(src)
        assert result[0]["start_line"] == 2
        assert result[0]["end_line"] == 2

    def test_def_has_correct_line_numbers(self):
        src = "def bar():\n    return 1\n"
        result = ASTBlockExtractor._extract_fallback(src)
        assert result[0]["start_line"] == 1
        assert result[0]["source"] == "def bar():"


class TestExtractBlocksWithTreeSitter:
    def test_success_path(self):
        source = "def my_func():\n    pass"
        name_node = MagicMock()
        name_node.start_byte = 4
        name_node.end_byte = 11

        func_node = MagicMock()
        func_node.type = "function_definition"
        func_node.child_by_field_name = MagicMock(return_value=name_node)
        func_node.start_byte = 0
        func_node.end_byte = len(source)
        func_node.start_point = (0, 0)
        func_node.end_point = (1, 8)
        func_node.children = []

        root = MagicMock()
        root.children = [func_node]

        tree = MagicMock()
        tree.root_node = root

        fake_parser = MagicMock()
        fake_parser.parse.return_value = tree

        ext = ASTBlockExtractor()
        with patch("general_ludd.code_intelligence.extractor._get_parser", return_value=fake_parser):
            result = ext.extract_blocks(source, "python")
        assert len(result) == 1
        assert result[0]["name"] == "my_func"
