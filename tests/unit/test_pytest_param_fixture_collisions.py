"""Static guardrails for pytest parameter names supplied by external plugins."""

from __future__ import annotations

import ast
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


def test_repository_class_scoped_fixtures_use_supported_binding(
    repository_fixture_audit: fixture_audit.PytestSourceAudit,
) -> None:
    bindings = repository_fixture_audit.bindings

    assert not bindings, "Class-scoped fixtures require staticmethod or classmethod binding:\n" + "\n".join(
        f"  {item.path.relative_to(REPO_ROOT)}:{item.line}: {item.class_name}.{item.name}" for item in bindings
    )
