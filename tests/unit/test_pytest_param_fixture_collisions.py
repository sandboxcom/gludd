"""Static guardrails for pytest parameter names supplied by external plugins."""

from __future__ import annotations

import ast
import gc
import weakref
from pathlib import Path
from textwrap import dedent
from types import SimpleNamespace

import pytest
from scripts import check_pytest_param_fixture_collisions as fixture_audit
from scripts.check_pytest_param_fixture_collisions import (
    external_pytest_fixture_names,
    find_parametrized_fixture_collisions,
    fixture_names_from_source,
    pytest_plugin_modules_from_source,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_combined_audit_parses_each_unique_path_once_and_releases_trees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = [tmp_path / "test_a.py", tmp_path / "test_b.py"]
    for path in paths:
        path.write_text(
            'import pytest\n'
            'class TestExample:\n'
            '    @pytest.fixture(scope="class")\n'
            '    def shared(self): pass\n'
            '@pytest.mark.parametrize("base_url", ["example"])\n'
            'def test_case(base_url): pass\n',
            encoding="utf-8",
        )
    original_parse = ast.parse
    parsed: list[str] = []
    previous: list[weakref.ReferenceType[ast.Module]] = []

    def tracked_parse(source: str, *, filename: str) -> ast.Module:
        assert all(reference() is None for reference in previous)
        tree = original_parse(source, filename=filename)
        parsed.append(filename)
        previous.append(weakref.ref(tree))
        return tree

    monkeypatch.setattr(fixture_audit, "ast", SimpleNamespace(**{**vars(ast), "parse": tracked_parse}))
    report = fixture_audit.audit_pytest_sources(
        [paths[1], paths[0], paths[0]], external_fixture_names={"base_url"},
    )

    assert parsed == [str(path) for path in paths]
    assert all(reference() is None for reference in previous)
    assert report.external_fixture_names == frozenset({"base_url"})
    assert [(item.path, item.name) for item in report.collisions] == [
        (path, "base_url") for path in paths
    ]
    assert [(item.path, item.class_name, item.name) for item in report.bindings] == [
        (path, "TestExample", "shared") for path in paths
    ]


def test_combined_audit_rebuilds_after_source_and_fixture_names_change(tmp_path: Path) -> None:
    first = tmp_path / "test_a.py"
    second = tmp_path / "test_b.py"
    first.write_text(
        'import pytest\n@pytest.mark.parametrize("base_url", [1])\n'
        'def test_case(base_url): pass\n', encoding="utf-8",
    )
    initial = fixture_audit.audit_pytest_sources([first], external_fixture_names={"base_url"})
    first.write_text(
        'import pytest\n@pytest.mark.parametrize("adapter", [1])\n'
        'def test_case(adapter): pass\n', encoding="utf-8",
    )
    second.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

    changed = fixture_audit.audit_pytest_sources(
        [first, second], external_fixture_names={"adapter"},
    )
    removed = fixture_audit.audit_pytest_sources(
        [first, second], external_fixture_names={"base_url"},
    )

    assert [(item.path, item.name) for item in initial.collisions] == [(first, "base_url")]
    assert [(item.path, item.name) for item in changed.collisions] == [
        (first, "adapter"), (second, "adapter"),
    ]
    assert not removed.collisions
    assert not changed.bindings


def test_combined_audit_preserves_late_aliases_nested_owners_and_provider_boundary(
    tmp_path: Path,
) -> None:
    path = tmp_path / "test_aliases.py"
    path.write_text(dedent('''
        class TestExample:
            if True:
                @provide(scope="class", name="public_async")
                async def shared(self): pass
            def test_local(self):
                @pt.fixture(scope="class")
                def local(): pass
            @pt.fixture(scope="class")
            @staticmethod
            def supported(): pass
        @cases.parametrize("base_url", [1])
        def test_case(base_url): pass
        @async_cases.parametrize("base_url", [1])
        def test_not_pytest(base_url): pass
        def import_late():
            import pytest as pt
            from pytest import mark as cases
            from pytest_asyncio import fixture as provide, mark as async_cases
    '''), encoding="utf-8")

    report = fixture_audit.audit_pytest_sources([path], external_fixture_names={"base_url"})

    assert [(item.line, item.name) for item in report.collisions] == [(12, "base_url")]
    assert [(item.line, item.class_name, item.name) for item in report.bindings] == [
        (4, "TestExample", "public_async"),
    ]
    assert list(report.collisions) == find_parametrized_fixture_collisions(
        [path], external_fixture_names={"base_url"},
    )
    assert list(report.bindings) == fixture_audit.find_class_scoped_instance_fixtures([path])


def test_plugin_discovery_parses_each_visited_module_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = {
        "plugin.one": 'import pytest\n@pytest.fixture\ndef base_url(): pass\npytest_plugins = "plugin.two"\n',
        "plugin.two": 'from pytest import fixture\n@fixture\ndef adapter(): pass\npytest_plugins = "plugin.one"\n',
    }
    monkeypatch.setattr(fixture_audit, "_pytest_entry_point_modules", lambda: {"plugin.one"})
    monkeypatch.setattr(fixture_audit, "_module_source", lambda name: (sources[name], name))
    original_parse = ast.parse
    parsed: list[str] = []

    def tracked_parse(source: str, *, filename: str) -> ast.Module:
        parsed.append(filename)
        return original_parse(source, filename=filename)

    monkeypatch.setattr(fixture_audit, "ast", SimpleNamespace(**{**vars(ast), "parse": tracked_parse}))

    assert external_pytest_fixture_names() == {"adapter", "base_url"}
    assert parsed == ["plugin.one", "plugin.two"]


def test_fixture_names_from_source_understands_public_pytest_decorators() -> None:
    source = dedent(
        """
        import pytest as pt
        from pytest import fixture as provide

        @pt.fixture
        def base_url():
            return "https://example.test"

        @pt.fixture(name="public_name")
        def internal_name():
            return object()

        @provide()
        def imported_alias():
            return object()
        """
    )

    assert fixture_names_from_source(source, filename="plugin.py") == {
        "base_url",
        "imported_alias",
        "public_name",
    }


def test_plugin_module_discovery_follows_literal_declarations() -> None:
    source = dedent(
        """
        pytest_plugins = ("plugin.one", ["plugin.two"])
        pytest_plugins: tuple[str, ...] = {"plugin.three"}
        """
    )

    assert pytest_plugin_modules_from_source(source, filename="plugin.py") == {
        "plugin.one",
        "plugin.three",
        "plugin.two",
    }


def test_scanner_reports_single_and_tuple_parametrize_collisions(tmp_path: Path) -> None:
    test_file = tmp_path / "test_collision.py"
    test_file.write_text(
        dedent(
            """
            import pytest

            @pytest.mark.parametrize("base_url", ["https://one.example"])
            def test_one(base_url):
                pass

            @pytest.mark.parametrize(("adapter", "base_url"), [(object(), "https://two.example")])
            def test_two(adapter, base_url):
                pass
            """
        ),
        encoding="utf-8",
    )

    collisions = find_parametrized_fixture_collisions(
        [test_file], external_fixture_names={"base_url"}
    )

    assert [(item.path, item.line, item.name) for item in collisions] == [
        (test_file, 4, "base_url"),
        (test_file, 8, "base_url"),
    ]


def test_scanner_ignores_parameters_not_owned_by_external_plugins(tmp_path: Path) -> None:
    test_file = tmp_path / "test_safe.py"
    test_file.write_text(
        dedent(
            """
            import pytest

            @pytest.mark.parametrize("candidate_url", ["https://example.test"])
            def test_safe(candidate_url):
                pass
            """
        ),
        encoding="utf-8",
    )

    assert not find_parametrized_fixture_collisions(
        [test_file], external_fixture_names={"base_url"}
    )


def test_scanner_supports_imported_marks_keywords_and_dynamic_fail_closed(
    tmp_path: Path,
) -> None:
    test_file = tmp_path / "test_decorator_shapes.py"
    test_file.write_text(
        dedent(
            """
            import pytest
            from pytest import mark as cases

            VALUES = ("base_url",)

            @cases.parametrize(argnames=["base_url"], argvalues=["https://example.test"])
            def test_keyword(base_url):
                pass

            @pytest.mark.parametrize(VALUES, [("https://example.test",)])
            def test_dynamic(base_url):
                pass

            @custom.parametrize("base_url", ["https://example.test"])
            def test_unrelated_decorator(base_url):
                pass
            """
        ),
        encoding="utf-8",
    )

    collisions = find_parametrized_fixture_collisions(
        [test_file], external_fixture_names={"base_url"}
    )

    assert [(item.line, item.name) for item in collisions] == [(7, "base_url")]


@pytest.fixture(scope="module")
def repository_fixture_audit() -> fixture_audit.PytestSourceAudit:
    return fixture_audit.audit_pytest_sources(
        (REPO_ROOT / "tests").rglob("*.py"),
        external_fixture_names=external_pytest_fixture_names(),
    )


def test_repository_inventory_consumers_share_one_xdist_fixture_owner() -> None:
    """The expensive module-scoped inventory must not be rebuilt on two workers."""
    for consumer in (
        test_repository_parameters_do_not_shadow_external_pytest_fixtures,
        test_repository_class_scoped_fixtures_use_supported_binding,
    ):
        groups = [
            mark.args
            for mark in getattr(consumer, "pytestmark", ())
            if mark.name == "xdist_group"
        ]
        assert groups == [("pytest-source-inventory",)]


@pytest.mark.xdist_group("pytest-source-inventory")
def test_repository_parameters_do_not_shadow_external_pytest_fixtures(
    repository_fixture_audit: fixture_audit.PytestSourceAudit,
) -> None:
    fixture_names = repository_fixture_audit.external_fixture_names
    assert "base_url" in fixture_names, "pytest-base-url fixture discovery regressed"

    collisions = repository_fixture_audit.collisions

    assert not collisions, "Parametrized arguments shadow external pytest fixtures:\n" + "\n".join(
        f"  {item.path.relative_to(REPO_ROOT)}:{item.line}: {item.name}" for item in collisions
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            '''
            import pytest
            class TestExample:
                @pytest.fixture(scope="class")
                def shared(self):
                    return [1, 2, 3]
            ''',
            [("TestExample", "shared")],
        ),
        (
            '''
            import pytest as pt
            class TestExample:
                @pt.fixture(name="public_name", scope="class")
                async def internal_name(self):
                    return [1, 2, 3]
            ''',
            [("TestExample", "public_name")],
        ),
        (
            '''
            from pytest import fixture as provide
            class BaseExample:
                @provide(autouse=True, scope="class")
                def shared(instance):
                    instance.value = 1
            ''',
            [("BaseExample", "shared")],
        ),
        (
            '''
            import pytest
            @pytest.fixture(scope="class")
            def shared():
                return [1, 2, 3]
            ''',
            [],
        ),
        (
            '''
            import pytest
            class TestExample:
                @pytest.fixture(scope="class")
                @staticmethod
                def shared():
                    return [1, 2, 3]
            ''',
            [],
        ),
        (
            '''
            import pytest
            class TestExample:
                @pytest.fixture(scope="class")
                @classmethod
                def shared(cls):
                    cls.value = 1
            ''',
            [],
        ),
        (
            '''
            import pytest
            class TestExample:
                @pytest.fixture
                def per_test(self):
                    return [1, 2, 3]
                @pytest.fixture(scope="function")
                def also_per_test(self):
                    return [1, 2, 3]
                @custom.fixture(scope="class")
                def unrelated(self):
                    return [1, 2, 3]
            ''',
            [],
        ),
        (
            '''
            import pytest
            class TestExample:
                def test_local_fixture(self):
                    @pytest.fixture(scope="class")
                    def local_fixture():
                        return [1, 2, 3]
            ''',
            [],
        ),
        (
            '''
            import pytest
            class TestExample:
                if True:
                    @pytest.fixture(scope="class")
                    def conditional(self):
                        return [1, 2, 3]
            ''',
            [("TestExample", "conditional")],
        ),
    ],
)
def test_class_fixture_binding_shapes(
    tmp_path: Path,
    source: str,
    expected: list[tuple[str, str]],
) -> None:
    test_file = tmp_path / "test_binding.py"
    test_file.write_text(dedent(source), encoding="utf-8")

    bindings = fixture_audit.find_class_scoped_instance_fixtures([test_file])

    assert [(item.class_name, item.name) for item in bindings] == expected


def test_class_fixture_bindings_have_stable_source_locations(tmp_path: Path) -> None:
    first = tmp_path / "test_a.py"
    second = tmp_path / "test_b.py"
    source = (
        'import pytest\n'
        'class TestExample:\n'
        '    @pytest.fixture(scope="class")\n'
        '    def shared(self):\n'
        '        return 1\n'
    )
    for path in (first, second):
        path.write_text(source, encoding="utf-8")

    bindings = fixture_audit.find_class_scoped_instance_fixtures([second, first, first])

    assert [(item.path, item.line, item.class_name, item.name) for item in bindings] == [
        (first, 3, "TestExample", "shared"),
        (second, 3, "TestExample", "shared"),
    ]


def test_class_fixture_bindings_understand_pytest_asyncio_aliases(tmp_path: Path) -> None:
    source = dedent(
        '''
        import pytest_asyncio as pa
        from pytest_asyncio import fixture as provide_async
        class TestExample:
            @pa.fixture(scope="class", loop_scope="class")
            async def module_alias(self):
                yield 1
            @provide_async(scope="class", name="public_async")
            async def imported_alias(self):
                return 2
            @pa.fixture(scope="class", loop_scope="class")
            @staticmethod
            async def supported_static():
                return 3
        '''
    )
    test_file = tmp_path / "test_async_binding.py"
    test_file.write_text(source, encoding="utf-8")

    # The existing plugin-fixture discovery contract remains pytest-only.
    assert fixture_names_from_source(source, filename=str(test_file)) == set()
    bindings = fixture_audit.find_class_scoped_instance_fixtures([test_file])

    assert [(item.class_name, item.name) for item in bindings] == [
        ("TestExample", "module_alias"),
        ("TestExample", "public_async"),
    ]


@pytest.mark.xdist_group("pytest-source-inventory")
def test_repository_class_scoped_fixtures_use_supported_binding(
    repository_fixture_audit: fixture_audit.PytestSourceAudit,
) -> None:
    bindings = repository_fixture_audit.bindings

    assert not bindings, "Class-scoped fixtures require staticmethod or classmethod binding:\n" + "\n".join(
        f"  {item.path.relative_to(REPO_ROOT)}:{item.line}: {item.class_name}.{item.name}" for item in bindings
    )


def test_declaration_traversal_visits_statements_once_without_payload_growth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Argument, alias and keyword payloads must not expand the statement walk."""
    from collections.abc import Iterator

    original_fields = ast.iter_fields
    visit_counts: list[int] = []
    for width in (1, 256):
        parameters = ", ".join(f"argument_{index}=0" for index in range(width))
        aliases = ", ".join(f"module_{index} as alias_{index}" for index in range(width))
        keywords = ", ".join(f"key_{index}=0" for index in range(width))
        source = (
            f"import pytest as pt, {aliases}\n"
            "pytest_plugins = ('plugin.one',)\n"
            f"@pt.mark.parametrize('base_url', [dict({keywords})])\n"
            f"def test_case(base_url, {parameters}): pass\n"
            f"class TestExample(Base, {keywords}):\n"
            "    @pt.fixture(scope='class')\n"
            f"    def shared(self, {parameters}): pass\n"
        )
        tree = ast.parse(source)
        expected_nodes = {
            id(node) for node in ast.walk(tree) if isinstance(node, (ast.Module, ast.stmt))
        }
        visited: list[int] = []

        def statement_fields(
            node: ast.AST, *, expected_nodes: set[int] = expected_nodes,
            visited: list[int] = visited,
        ) -> Iterator[tuple[str, object]]:
            assert id(node) in expected_nodes, (
                f"Declaration traversal inspected non-statement payload {type(node).__name__}"
            )
            visited.append(id(node))
            yield from original_fields(node)

        with monkeypatch.context() as patch:
            patch.setattr(ast, "iter_fields", statement_fields)
            declarations = fixture_audit._source_declarations(tree)

        assert visited
        assert set(visited) == expected_nodes
        assert len(visited) == len(expected_nodes)
        visit_counts.append(len(visited))
        assert len(declarations.imports) == 1
        assert len(declarations.imports[0].names) == width + 1
        assert [
            (node.name, getattr(owner, "name", "<module>"))
            for node, owner in declarations.functions
        ] == [("shared", "TestExample"), ("test_case", "<module>")]
        assert [ast.literal_eval(value) for value in declarations.plugin_values] == [
            ("plugin.one",),
        ]
        assert fixture_audit._declared_fixture_names(declarations) == {"shared"}

    assert len(visit_counts) == 2
    assert visit_counts[0] == visit_counts[1]


def _nested_statement_payload(label: str) -> str:
    """Put every declaration consumer beneath a native statement container."""
    return dedent(f"""
        pytest_plugins = "plugin.{label}.assigned"
        pytest_plugins: tuple[str, ...] = ("plugin.{label}.annotated",)
        class Test_{label}:
            if condition:
                @provide_{label}(scope="class", name="public_{label}")
                async def shared(self):
                    @pt_{label}.fixture(scope="class")
                    def nested(): pass
                @pt_{label}.fixture(scope="class")
                @staticmethod
                def supported(): pass
        @cases_{label}.parametrize("base_url", [1])
        def case_{label}(base_url): pass
        from pytest import fixture as provide_{label}, mark as cases_{label}
        import pytest as pt_{label}
    """).strip()


@pytest.mark.parametrize(
    ("statement_template", "statement_slots", "reverse_statement_slots"),
    [
        pytest.param(
            "if condition:\n{body}\nelif alternate:\n{alternate}\nelse:\n{orelse}\n",
            (("body", 4), ("alternate", 4), ("orelse", 4)),
            ("orelse", "alternate", "body"),
            id="if-elif-else",
        ),
        pytest.param(
            "for item in items:\n{body}\nelse:\n{orelse}\n",
            (("body", 4), ("orelse", 4)), ("orelse", "body"), id="for-else",
        ),
        pytest.param(
            "async for item in items:\n{body}\nelse:\n{orelse}\n",
            (("body", 4), ("orelse", 4)), ("orelse", "body"), id="async-for-else",
        ),
        pytest.param(
            "while condition:\n{body}\nelse:\n{orelse}\n",
            (("body", 4), ("orelse", 4)), ("orelse", "body"), id="while-else",
        ),
        pytest.param(
            "with manager() as resource:\n{body}\n",
            (("body", 4),), ("body",), id="with",
        ),
        pytest.param(
            "async with manager() as resource:\n{body}\n",
            (("body", 4),), ("body",), id="async-with",
        ),
        pytest.param(
            "try:\n{body}\nexcept ValueError as first:\n{first_handler}\n"
            "except TypeError as second:\n{second_handler}\n"
            "else:\n{orelse}\nfinally:\n{finalbody}\n",
            (("body", 4), ("first_handler", 4), ("second_handler", 4),
             ("orelse", 4), ("finalbody", 4)),
            ("finalbody", "orelse", "second_handler", "first_handler", "body"),
            id="try-except-else-finally",
        ),
        pytest.param(
            "try:\n{body}\nexcept* ValueError as first:\n{first_handler}\n"
            "except* TypeError as second:\n{second_handler}\n"
            "else:\n{orelse}\nfinally:\n{finalbody}\n",
            (("body", 4), ("first_handler", 4), ("second_handler", 4),
             ("orelse", 4), ("finalbody", 4)),
            ("finalbody", "orelse", "second_handler", "first_handler", "body"),
            id="try-star-except-else-finally",
        ),
        pytest.param(
            'match subject:\n    case {"value": value} if accepted(value):\n{body}\n'
            "    case _:\n{otherwise}\n",
            (("body", 8), ("otherwise", 8)), ("otherwise", "body"), id="match-cases",
        ),
    ],
)
def test_declarations_preserve_every_statement_container_order_and_owner(
    tmp_path: Path,
    statement_template: str,
    statement_slots: tuple[tuple[str, int], ...],
    reverse_statement_slots: tuple[str, ...],
) -> None:
    from textwrap import indent

    statement = statement_template
    for label, depth in statement_slots:
        statement = statement.replace(
            "{" + label + "}", indent(_nested_statement_payload(label), " " * depth),
        )
    source = "async def container():\n" + indent(statement, "    ")
    declarations = fixture_audit._source_declarations(ast.parse(source))

    expected_functions = [("container", "<module>")]
    expected_imports: list[str] = []
    expected_plugins: list[object] = []
    for label in reverse_statement_slots:
        expected_functions.extend([
            (f"case_{label}", "container"),
            ("supported", f"Test_{label}"),
            ("shared", f"Test_{label}"),
            ("nested", "shared"),
        ])
        expected_imports.extend([
            f"import pytest as pt_{label}",
            f"from pytest import fixture as provide_{label}, mark as cases_{label}",
        ])
        expected_plugins.extend([
            (f"plugin.{label}.annotated",), f"plugin.{label}.assigned",
        ])

    assert [
        (node.name, getattr(owner, "name", "<module>"))
        for node, owner in declarations.functions
    ] == expected_functions
    assert [ast.unparse(node) for node in declarations.imports] == expected_imports
    assert [ast.literal_eval(value) for value in declarations.plugin_values] == expected_plugins

    path = tmp_path / "test_nested_statements.py"
    path.write_text(source, encoding="utf-8")
    report = fixture_audit.audit_pytest_sources([path], external_fixture_names={"base_url"})
    assert [(item.path, item.name) for item in report.collisions] == [
        (path, "base_url") for _label, _depth in statement_slots
    ]
    assert [(item.class_name, item.name) for item in report.bindings] == [
        (f"Test_{label}", f"public_{label}") for label, _depth in statement_slots
    ]


@pytest.mark.parametrize("initial_enabled", [True, False])
@pytest.mark.parametrize("outcome", ["return", "runtime-error", "interrupt", "syntax-error"])
def test_source_audit_restores_exact_gc_state_after_native_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial_enabled: bool, outcome: str,
) -> None:
    path = tmp_path / "test_gc_scope.py"
    path.write_text(
        "def invalid(:\n" if outcome == "syntax-error" else (
            'import pytest\n'
            'class TestExample:\n'
            '    @pytest.fixture(scope="class")\n'
            '    def shared(self): pass\n'
            '@pytest.mark.parametrize("base_url", [1])\n'
            'def test_case(base_url): pass\n'
        ),
        encoding="utf-8",
    )
    original_parse = ast.parse
    original_read = Path.read_text
    original_declarations = fixture_audit._source_declarations
    read_states: list[bool] = []
    parse_states: list[bool] = []
    declaration_states: list[bool] = []
    expected_errors: dict[str, type[BaseException]] = {
        "runtime-error": RuntimeError, "interrupt": KeyboardInterrupt, "syntax-error": SyntaxError,
    }

    def tracked_read(source_path: Path, *args: object, **kwargs: object) -> str:
        if source_path == path:
            read_states.append(gc.isenabled())
        return original_read(source_path, encoding="utf-8")

    def tracked_parse(source: str, *, filename: str) -> ast.Module:
        parse_states.append(gc.isenabled())
        assert not gc.isenabled()
        assert filename == str(path)
        return original_parse(source, filename=filename)

    def tracked_declarations(tree: ast.Module) -> fixture_audit._SourceDeclarations:
        declaration_states.append(gc.isenabled())
        assert not gc.isenabled()
        declarations = original_declarations(tree)
        # Make restoration of an initially disabled state a real cleanup obligation.
        gc.enable()
        if outcome in {"runtime-error", "interrupt"}:
            raise expected_errors[outcome]("guarded body failure")
        return declarations

    monkeypatch.setattr(fixture_audit, "_BOUND_SOURCE_GC", True, raising=False)
    monkeypatch.setattr(Path, "read_text", tracked_read)
    monkeypatch.setattr(fixture_audit, "ast", SimpleNamespace(**{**vars(ast), "parse": tracked_parse}))
    monkeypatch.setattr(fixture_audit, "_source_declarations", tracked_declarations)
    process_enabled = gc.isenabled()
    thresholds = gc.get_threshold()
    try:
        (gc.enable if initial_enabled else gc.disable)()
        if outcome == "return":
            report = fixture_audit.audit_pytest_sources([path], external_fixture_names={"base_url"})
            assert [(item.path, item.name) for item in report.collisions] == [(path, "base_url")]
            assert [(item.class_name, item.name) for item in report.bindings] == [
                ("TestExample", "shared"),
            ]
        else:
            with pytest.raises(expected_errors[outcome]):
                fixture_audit.audit_pytest_sources([path], external_fixture_names={"base_url"})
        assert gc.isenabled() is initial_enabled
        assert gc.get_threshold() == thresholds
        assert read_states == [initial_enabled]
        assert parse_states == [False]
        assert declaration_states == ([] if outcome == "syntax-error" else [False])
    finally:
        (gc.enable if process_enabled else gc.disable)()


@pytest.mark.parametrize("initial_enabled", [True, False])
def test_source_audit_preserves_gc_behavior_on_unsupported_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial_enabled: bool,
) -> None:
    path = tmp_path / "test_fallback.py"
    path.write_text(
        'import pytest\n@pytest.mark.parametrize("base_url", [1])\n'
        'def test_case(base_url): pass\n', encoding="utf-8",
    )
    original_parse = ast.parse
    parsed: list[str] = []
    policy_calls: list[str] = []

    def tracked_parse(source: str, *, filename: str) -> ast.Module:
        assert gc.isenabled() is initial_enabled
        parsed.append(filename)
        return original_parse(source, filename=filename)

    monkeypatch.setattr(fixture_audit, "_BOUND_SOURCE_GC", False, raising=False)
    monkeypatch.setattr(
        fixture_audit, "gc", SimpleNamespace(
            isenabled=gc.isenabled,
            disable=lambda: policy_calls.append("disable"),
            enable=lambda: policy_calls.append("enable"),
        ), raising=False,
    )
    monkeypatch.setattr(fixture_audit, "ast", SimpleNamespace(**{**vars(ast), "parse": tracked_parse}))
    process_enabled = gc.isenabled()
    try:
        (gc.enable if initial_enabled else gc.disable)()
        report = fixture_audit.audit_pytest_sources([path], external_fixture_names={"base_url"})
        assert [(item.path, item.name) for item in report.collisions] == [(path, "base_url")]
        assert parsed == [str(path)]
        assert policy_calls == []
        assert gc.isenabled() is initial_enabled
    finally:
        (gc.enable if process_enabled else gc.disable)()


@pytest.mark.parametrize("initial_enabled", [True, False])
@pytest.mark.parametrize("bad_source", ["missing", "invalid-utf8"])
def test_source_audit_reads_before_changing_gc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, initial_enabled: bool, bad_source: str,
) -> None:
    path = tmp_path / "test_unreadable.py"
    if bad_source == "invalid-utf8":
        path.write_bytes(b"\xff")
    policy_calls: list[str] = []
    monkeypatch.setattr(fixture_audit, "_BOUND_SOURCE_GC", True, raising=False)
    monkeypatch.setattr(
        fixture_audit, "gc", SimpleNamespace(
            isenabled=gc.isenabled,
            disable=lambda: policy_calls.append("disable"),
            enable=lambda: policy_calls.append("enable"),
        ), raising=False,
    )
    process_enabled = gc.isenabled()
    try:
        (gc.enable if initial_enabled else gc.disable)()
        expected_error = FileNotFoundError if bad_source == "missing" else UnicodeDecodeError
        with pytest.raises(expected_error):
            fixture_audit.audit_pytest_sources([path], external_fixture_names=set())
        assert policy_calls == []
        assert gc.isenabled() is initial_enabled
    finally:
        (gc.enable if process_enabled else gc.disable)()


@pytest.mark.parametrize("retain_first_tree", [False, True])
def test_source_audit_releases_file_owned_nodes_with_retention_negative_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retain_first_tree: bool,
) -> None:
    paths = [tmp_path / "test_a.py", tmp_path / "test_b.py"]
    for path in paths:
        path.write_text(
            'import pytest\n'
            'class TestExample:\n'
            '    @pytest.fixture(scope="class")\n'
            '    def shared(self): pass\n'
            '@pytest.mark.parametrize("base_url", [1])\n'
            'def test_case(base_url): pass\n', encoding="utf-8",
        )
    original_parse = ast.parse
    previous: list[weakref.ReferenceType[ast.AST]] = []
    retained: list[ast.Module] = []
    parsed: list[str] = []

    def tracked_parse(source: str, *, filename: str) -> ast.Module:
        assert not gc.isenabled()
        assert all(reference() is None for reference in previous), "previous source retained"
        tree = original_parse(source, filename=filename)
        previous.extend(
            weakref.ref(node) for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        )
        parsed.append(filename)
        if retain_first_tree and len(parsed) == 1:
            retained.append(tree)
        return tree

    monkeypatch.setattr(fixture_audit, "_BOUND_SOURCE_GC", True, raising=False)
    monkeypatch.setattr(fixture_audit, "ast", SimpleNamespace(**{**vars(ast), "parse": tracked_parse}))
    process_enabled = gc.isenabled()
    thresholds = gc.get_threshold()
    try:
        gc.enable()
        if retain_first_tree:
            with pytest.raises(AssertionError, match="previous source retained"):
                fixture_audit.audit_pytest_sources(paths, external_fixture_names={"base_url"})
            assert parsed == [str(paths[0])]
            assert any(reference() is not None for reference in previous)
        else:
            report = fixture_audit.audit_pytest_sources(paths, external_fixture_names={"base_url"})
            assert parsed == [str(path) for path in paths]
            assert all(reference() is None for reference in previous)
            assert [(item.path, item.name) for item in report.collisions] == [
                (path, "base_url") for path in paths
            ]
            assert [(item.path, item.class_name, item.name) for item in report.bindings] == [
                (path, "TestExample", "shared") for path in paths
            ]
        assert gc.isenabled()
        assert gc.get_threshold() == thresholds
    finally:
        retained.clear()
        (gc.enable if process_enabled else gc.disable)()
