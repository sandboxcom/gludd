"""
tests/unit/test_type_strictness.py

TDD behavioral spec for scripts/check_type_strictness.py.

Run with:
    make test-specific TESTFILE='tests/unit/test_type_strictness.py'
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

# --------------------------------------------------------------------------- #
# Load the script as a module without invoking main()
# --------------------------------------------------------------------------- #

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts" / "check_type_strictness.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("check_type_strictness", _SCRIPT_PATH)
    mod = cast(Any, importlib.util).module_from_spec(spec)
    # dataclass (and other decorators) resolve the class's module via
    # sys.modules — register before exec so @dataclass(frozen=True) works.
    sys.modules["check_type_strictness"] = mod
    cast(Any, spec.loader).exec_module(mod)
    return mod


_mod = _load_module()


# --------------------------------------------------------------------------- #
# Annotation introspection helpers
# --------------------------------------------------------------------------- #


class TestNameIsAny:
    def test_bare_name_any(self):
        node = ast.parse("Any", mode="eval").body
        assert _mod._name_is_any(node) is True

    def test_attribute_typing_any(self):
        node = ast.parse("typing.Any", mode="eval").body
        assert _mod._name_is_any(node) is True

    def test_unrelated_name(self):
        node = ast.parse("AnyStr", mode="eval").body
        assert _mod._name_is_any(node) is False

    def test_different_type(self):
        node = ast.parse("int", mode="eval").body
        assert _mod._name_is_any(node) is False


class TestWalkAnnotation:
    def _walk(self, annotation_expr: str):
        return _mod._walk_annotation(ast.parse(annotation_expr, mode="eval").body)

    def test_bare_any(self):
        assert len(self._walk("Any")) == 1

    def test_dict_str_any(self):
        hits = self._walk("dict[str, Any]")
        assert len(hits) == 1

    def test_list_any(self):
        assert len(self._walk("list[Any]")) == 1

    def test_optional_any(self):
        assert len(self._walk("Optional[Any]")) == 1

    def test_union_with_any(self):
        assert len(self._walk("Union[int, Any]")) == 1

    def test_tuple_any_ellipsis(self):
        assert len(self._walk("tuple[Any, ...]")) == 1

    def test_typing_any_attribute(self):
        assert len(self._walk("typing.Any")) == 1

    def test_no_any(self):
        assert self._walk("dict[str, int]") == []
        assert self._walk("Optional[int]") == []

    def test_none_node(self):
        assert _mod._walk_annotation(None) == []

    def test_stringified_annotation(self):
        hits = _mod._walk_annotation(ast.parse('x: "dict[str, Any]"').body[0].annotation)
        assert len(hits) == 1

    def test_stringified_no_any(self):
        hits = _mod._walk_annotation(ast.parse('x: "dict[str, int]"').body[0].annotation)
        assert hits == []


class TestIterArgs:
    def test_collects_all_slots(self):
        func = ast.parse(
            "def f(a: int, /, b: int, *args: int, c: int, **kw: int) -> None: ..."
        ).body[0]
        assert isinstance(func, ast.FunctionDef)
        names = [a.arg for a in _mod._iter_args(func.args)]
        assert names == ["a", "b", "args", "c", "kw"]


# --------------------------------------------------------------------------- #
# _scan_source — the core detection matrix
# --------------------------------------------------------------------------- #


def _scan(src: str) -> list[_mod.Violation]:
    return _mod._scan_source(src, "sample.py")


class TestReturnAnnotation:
    def test_return_any_flagged(self):
        v = _scan("def f() -> Any:\n    return 1\n")
        assert len(v) == 1
        assert v[0].kind == "return"
        assert v[0].line == 1

    def test_return_optional_any_flagged(self):
        v = _scan("def f() -> Optional[Any]:\n    return None\n")
        assert len(v) == 1
        assert v[0].kind == "return"

    def test_clean_return_not_flagged(self):
        assert _scan("def f() -> int:\n    return 1\n") == []


class TestParameterAnnotation:
    def test_param_any_flagged(self):
        v = _scan("def f(x: Any) -> None:\n    pass\n")
        assert len(v) == 1
        assert v[0].kind == "param"

    def test_dict_param_any_flagged(self):
        v = _scan("def f(x: dict[str, Any]) -> None:\n    pass\n")
        assert len(v) == 1
        assert v[0].kind == "param"

    def test_kwonly_param_any_flagged(self):
        v = _scan("def f(*, x: Any) -> None:\n    pass\n")
        assert len(v) == 1
        assert v[0].kind == "param"

    def test_vararg_any_flagged(self):
        v = _scan("def f(*args: Any) -> None:\n    pass\n")
        assert len(v) == 1
        assert v[0].kind == "param"


class TestAnnotatedAssignment:
    def test_annassign_any_flagged(self):
        v = _scan("x: Any = 1\n")
        assert len(v) == 1
        assert v[0].kind == "annassign"

    def test_dict_field_any_flagged(self):
        v = _scan("x: dict[str, Any] = {}\n")
        assert len(v) == 1
        assert v[0].kind == "annassign"

    def test_list_field_any_flagged(self):
        v = _scan("x: list[Any] = []\n")
        assert len(v) == 1

    def test_typeddict_field_any_flagged(self):
        src = (
            "class MyDict(TypedDict):\n"
            "    name: str\n"
            "    extra: dict[str, Any]\n"
        )
        v = _scan(src)
        assert len(v) == 1
        assert v[0].line == 3

    def test_plain_assignment_not_flagged(self):
        # `x = Any` is a value expression, not an annotation — out of scope.
        assert _scan("x = Any\n") == []


class TestNestedAndEdgeCases:
    def test_no_any_in_container(self):
        assert _scan("x: dict[str, int] = {}\n") == []
        assert _scan("x: list[int] = []\n") == []

    def test_unrelated_anystr_not_flagged(self):
        assert _scan("def f(x: AnyStr) -> AnyStr:\n    return x\n") == []

    def test_import_of_any_not_flagged(self):
        src = "from typing import Any\n"
        assert _scan(src) == []

    def test_typing_any_attribute_form(self):
        v = _scan("def f() -> typing.Any:\n    return 1\n")
        assert len(v) == 1

    def test_stringified_annotation_flagged(self):
        v = _scan('config: "dict[str, Any]" = {}\n')
        assert len(v) == 1
        # Stringified forward-refs are still attributed to their annotation site.
        assert v[0].kind == "annassign"

    def test_multiple_violations_counted(self):
        src = (
            "def f(a: Any, b: Any) -> Any:\n"
            "    x: list[Any] = []\n"
            "    return 1\n"
        )
        v = _scan(src)
        # 2 params + 1 return + 1 annassign = 4
        assert len(v) == 4

    def test_syntax_error_returns_empty(self):
        # Broken source must not crash the scanner.
        assert _scan("def f(:\n") == []

    def test_empty_source(self):
        assert _scan("") == []


# --------------------------------------------------------------------------- #
# Filesystem scan + baseline filtering
# --------------------------------------------------------------------------- #


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


class TestScanDirectory:
    def test_scan_collects_violations(self, tmp_path):
        _write(
            tmp_path / "src" / "mod.py",
            "def f() -> Any:\n    return 1\n",
        )
        result = _mod.scan([tmp_path / "src"])
        assert result.files_scanned == 1
        assert result.count == 1
        assert result.violations[0].file == "mod.py"

    def test_scan_skips_non_py_files(self, tmp_path):
        _write(tmp_path / "src" / "README.md", "# hi\n")
        result = _mod.scan([tmp_path / "src"])
        assert result.files_scanned == 0
        assert result.count == 0

    def test_scan_recursive(self, tmp_path):
        _write(tmp_path / "src" / "a" / "x.py", "v: Any = 1\n")
        _write(tmp_path / "src" / "b" / "y.py", "w: int = 2\n")
        result = _mod.scan([tmp_path / "src"])
        assert result.files_scanned == 2
        assert result.count == 1
        assert result.violations[0].file.endswith("x.py")

    def test_relative_path_uses_given_root(self, tmp_path):
        _write(tmp_path / "src" / "deep" / "x.py", "v: Any = 1\n")
        result = _mod.scan([tmp_path / "src"])
        assert "deep/x.py" in result.violations[0].file or "deep\\x.py" in result.violations[0].file


class TestBaseline:
    def test_load_baseline_returns_set(self, tmp_path):
        bf = _write(
            tmp_path / "baseline.txt",
            "# comment\nsrc/foo.py:10\nsrc/bar.py:20\n",
        )
        keys = _mod.load_baseline(bf)
        assert keys == {"src/foo.py:10", "src/bar.py:20"}

    def test_load_baseline_none_returns_empty(self):
        assert _mod.load_baseline(None) == set()

    def test_load_baseline_missing_returns_empty(self, tmp_path):
        assert _mod.load_baseline(tmp_path / "nope.txt") == set()

    def test_filter_new_drops_baselined(self):
        v_total = [
            _mod.Violation("a.py", 1, 0, "ctx", "return"),
            _mod.Violation("a.py", 2, 0, "ctx", "return"),
            _mod.Violation("b.py", 9, 0, "ctx", "return"),
        ]
        baseline = {"a.py:1", "b.py:9"}
        new = _mod.filter_new(v_total, baseline)
        assert [v.baseline_key for v in new] == ["a.py:2"]

    def test_baseline_key_format(self):
        v = _mod.Violation("a.py", 12, 4, "ctx", "return")
        assert v.baseline_key == "a.py:12"


# --------------------------------------------------------------------------- #
# main() end-to-end — exit codes + output
# --------------------------------------------------------------------------- #


class TestMainExitCodes:
    def _run(self, tmp_path: Path, argv: list[str]) -> int:
        return _mod.main(argv)

    def test_exit_0_on_clean_dir(self, tmp_path, capsys):
        _write(tmp_path / "src" / "clean.py", "def f() -> int:\n    return 1\n")
        rc = self._run(tmp_path, ["--format", "json", str(tmp_path / "src")])
        captured = capsys.readouterr().out
        assert rc == 0
        payload = json.loads(captured)
        assert payload["total"] == 0

    def test_exit_1_on_violation(self, tmp_path):
        _write(tmp_path / "src" / "bad.py", "def f() -> Any:\n    return 1\n")
        rc = self._run(tmp_path, [str(tmp_path / "src")])
        assert rc == 1

    def test_exit_0_when_all_baselined(self, tmp_path):
        _write(tmp_path / "src" / "bad.py", "def f() -> Any:\n    return 1\n")
        baseline = _write(tmp_path / "baseline.txt", "bad.py:1\n")
        rc = self._run(tmp_path, ["--baseline", str(baseline), str(tmp_path / "src")])
        assert rc == 0

    def test_exit_1_when_new_violation_beyond_baseline(self, tmp_path):
        src = "def f() -> Any:\n    return 1\n\ndef g() -> Any:\n    return 2\n"
        _write(tmp_path / "src" / "bad.py", src)
        baseline = _write(tmp_path / "baseline.txt", "bad.py:1\n")
        rc = self._run(tmp_path, ["--baseline", str(baseline), str(tmp_path / "src")])
        assert rc == 1

    def test_exit_2_on_missing_path(self, tmp_path):
        rc = self._run(tmp_path, [str(tmp_path / "does-not-exist")])
        assert rc == 2

    def test_json_output_shape(self, tmp_path, capsys):
        _write(tmp_path / "src" / "bad.py", "def f() -> Any:\n    return 1\n")
        rc = self._run(tmp_path, ["--format", "json", str(tmp_path / "src")])
        captured = capsys.readouterr().out
        payload = json.loads(captured)
        assert rc == 1
        assert payload["total"] == 1
        assert payload["violations"][0]["kind"] == "return"
        assert payload["violations"][0]["line"] == 1

    def test_quiet_suppresses_detail(self, tmp_path, capsys):
        _write(tmp_path / "src" / "bad.py", "def f() -> Any:\n    return 1\n")
        self._run(tmp_path, ["--quiet", str(tmp_path / "src")])
        captured = capsys.readouterr().out
        # Summary line present, per-violation detail absent.
        assert "type-strictness:" in captured
        assert "kind=return" not in captured

    def test_text_output_shows_location(self, tmp_path, capsys):
        _write(tmp_path / "src" / "bad.py", "def f() -> Any:\n    return 1\n")
        self._run(tmp_path, [str(tmp_path / "src")])
        captured = capsys.readouterr().out
        assert "bad.py:1:" in captured
        assert "kind=return" in captured
