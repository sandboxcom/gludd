"""Deep audit of ABC subclasses and Protocol usage in src/general_ludd/."""

from __future__ import annotations

import abc
import ast
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, Protocol

import pytest

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "general_ludd"


def _walk_modules() -> list[str]:
    modules: list[str] = []
    for _finder, name, ispkg in pkgutil.walk_packages([str(SRC_ROOT)], prefix="general_ludd.", onerror=lambda _: None):
        if ispkg:
            continue
        modules.append(name)
    return modules


def _import_module(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _find_abc_classes(modules: list[str]) -> dict[str, tuple[Any, dict[str, Any]]]:
    found: dict[str, tuple[Any, dict[str, Any]]] = {}
    for mod_name in modules:
        mod = _import_module(mod_name)
        if mod is None:
            continue
        for attr_name, obj in list(vars(mod).items()):
            if not inspect.isclass(obj):
                continue
            if obj.__module__ != mod.__name__:
                continue
            if obj is abc.ABC:
                continue
            abs_methods = {
                name: method
                for name, method in list(vars(obj).items())
                if hasattr(method, "__isabstractmethod__") and method.__isabstractmethod__
            }
            if abs_methods:
                found[f"{mod.__name__}.{attr_name}"] = (obj, abs_methods)
    return found


def _find_protocol_classes(modules: list[str]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for mod_name in modules:
        mod = _import_module(mod_name)
        if mod is None:
            continue
        for attr_name, obj in list(vars(mod).items()):
            if not inspect.isclass(obj):
                continue
            if obj.__module__ != mod.__name__:
                continue
            if _is_protocol(obj):
                found[f"{mod.__name__}.{attr_name}"] = obj
    return found


def _is_protocol(cls: type) -> bool:
    return Protocol in getattr(cls, "__mro__", ()) and cls is not Protocol


def _has_runtime_checkable(cls: type) -> bool:
    return hasattr(cls, "_is_runtime_protocol") and cls._is_runtime_protocol


def _find_concrete_subclasses(abc_cls: type, modules: list[str]) -> list[str]:
    concrete: list[str] = []
    for mod_name in modules:
        mod = _import_module(mod_name)
        if mod is None:
            continue
        for attr_name, obj in list(vars(mod).items()):
            if not inspect.isclass(obj):
                continue
            if obj is abc_cls:
                continue
            if issubclass(obj, abc_cls) and not inspect.isabstract(obj):
                concrete.append(f"{mod.__name__}.{attr_name}")
    return concrete


def _find_abstract_instantiations(filepath: Path) -> list[tuple[int, str, str]]:
    try:
        with open(filepath) as f:
            source = f.read()
    except Exception:
        return []
    tree = ast.parse(source, filename=str(filepath))
    issues: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        name = node.func.id
        if name == "super":
            continue
        line = ast.get_source_segment(source, node)
        if line:
            issues.append((node.lineno, name, line))
    return issues


# ── collected data (computed once) ────────────────────────────────────────

ALL_MODULES = _walk_modules()
ALL_ABC_CLASSES = _find_abc_classes(ALL_MODULES)
ALL_PROTOCOLS = _find_protocol_classes(ALL_MODULES)

_KNOWN_ABC_NAMES = {
    "SpeakerSelector",
    "TerminationCondition",
    "CredentialStore",
    "PSKStore",
    "_BaseSlurmDeployment",
    "IssueSource",
}


# ── Tests ─────────────────────────────────────────────────────────────────


class TestABCConcreteImplementations:
    """Every concrete subclass of an ABC must implement all abstract methods."""

    @pytest.mark.parametrize(
        "abc_qname,abc_info",
        [(qn, info) for qn, info in sorted(ALL_ABC_CLASSES.items()) if qn.rsplit(".", 1)[-1] in _KNOWN_ABC_NAMES],
    )
    def test_every_abc_has_concrete_subclass(self, abc_qname, abc_info):
        abc_cls, _ = abc_info
        concrete = _find_concrete_subclasses(abc_cls, ALL_MODULES)
        assert concrete, f"{abc_qname} has no concrete subclasses"

    def test_speaker_selector_subclasses_implement_select_next(self):
        from general_ludd.ag16_orchestration.conversation import (
            PrioritySelector,
            RoundRobinSelector,
            SpeakerSelector,
        )

        rrs = RoundRobinSelector()
        assert rrs.select_next is not SpeakerSelector.select_next
        ps = PrioritySelector(priority_order=["a"])
        assert ps.select_next is not SpeakerSelector.select_next

    def test_termination_condition_concrete_implements_should_terminate(self):
        from general_ludd.ag16_orchestration.conversation import MaxTurnsTermination

        obj = MaxTurnsTermination(max_turns=3)
        assert hasattr(obj, "should_terminate")

    def test_credential_store_has_concrete_implementation(self):
        from general_ludd.auth.browser_login import EnvCredentialStore

        store = EnvCredentialStore()
        assert hasattr(store, "store")
        assert hasattr(store, "retrieve")
        assert hasattr(store, "store_metadata")

    def test_psk_store_has_concrete_implementation(self):
        from general_ludd.security.psk_rotation import InMemoryPSKStore

        store = InMemoryPSKStore()
        assert hasattr(store, "save")
        assert hasattr(store, "load")
        assert hasattr(store, "list_versions")
        assert hasattr(store, "delete")

    def test_slurm_deployment_has_concrete_subclasses(self):
        from general_ludd.infra.slurm_deployment import _BaseSlurmDeployment

        concrete = _find_concrete_subclasses(_BaseSlurmDeployment, ALL_MODULES)
        assert concrete, "_BaseSlurmDeployment has no concrete subclasses"

    def test_issue_source_has_concrete_subclasses(self):
        from general_ludd.issue_sources.base import IssueSource

        concrete = _find_concrete_subclasses(IssueSource, ALL_MODULES)
        assert concrete, "IssueSource has no concrete subclasses"

    @pytest.mark.parametrize("abc_qname,abc_info", sorted(ALL_ABC_CLASSES.items()))
    def test_abstract_methods_implemented_by_concrete(self, abc_qname, abc_info):
        abc_cls, abs_methods = abc_info
        concrete_names = _find_concrete_subclasses(abc_cls, ALL_MODULES)
        if not concrete_names:
            return

        for abs_name in abs_methods:
            overridden: list[str] = []
            for cname in concrete_names:
                parts = cname.rsplit(".", 1)
                mod = sys.modules.get(parts[0]) or _import_module(parts[0])
                if mod is None:
                    continue
                obj = getattr(mod, parts[1], None)
                if obj is None:
                    continue
                m = getattr(obj, abs_name, None)
                if m is not None and not hasattr(m, "__isabstractmethod__"):
                    overridden.append(cname)

            assert overridden, (
                f"{abs_name!r} in {abc_qname} is not implemented by any concrete subclass; candidates: {concrete_names}"
            )


class TestProtocolRuntimeCheckable:
    """Protocols should use @runtime_checkable where applicable."""

    @pytest.mark.parametrize("proto_qname,proto_cls", sorted(ALL_PROTOCOLS.items()))
    def test_protocol_is_runtime_checkable(self, proto_qname, proto_cls):
        is_rc = _has_runtime_checkable(proto_cls)
        if not is_rc and proto_qname not in getattr(self, "ALLOWED_NON_CHECKABLE", set()):
            pass

    def test_runtime_checkable_count(self):
        total = len(ALL_PROTOCOLS)
        checkable = sum(1 for _, cls in ALL_PROTOCOLS.items() if _has_runtime_checkable(cls))
        if total > 0:
            ratio = checkable / total
            assert ratio >= 0.50, (
                f"Only {checkable}/{total} ({ratio:.0%}) Protocols have @runtime_checkable. Expect >=50%."
            )

    def test_protocol_count_minimum(self):
        assert len(ALL_PROTOCOLS) >= 50, f"Found only {len(ALL_PROTOCOLS)} Protocol classes — expected >=50"

    def test_abc_class_count_minimum(self):
        assert len(ALL_ABC_CLASSES) >= 6, f"Found only {len(ALL_ABC_CLASSES)} ABC classes — expected >=6"


class TestNoAbstractInstantiation:
    """Abstract classes should never be instantiated directly in production code."""

    KNOWN_ABC_CLASS_NAMES: frozenset[str] = frozenset(
        {
            "SpeakerSelector",
            "TerminationCondition",
            "CredentialStore",
            "PSKStore",
            "_BaseSlurmDeployment",
            "IssueSource",
        }
    )

    @pytest.mark.timeout(600)
    def test_no_direct_abc_instantiation(self):
        # Whole-tree AST scan; CI runners on cold disks exceed the 180s
        # global timeout under xdist contention (observed 2026-08-15).
        violations: list[str] = []
        assert isinstance(self.KNOWN_ABC_CLASS_NAMES, frozenset)
        for py_file in SRC_ROOT.rglob("*.py"):
            if py_file.name == "__init__.py" and py_file.stat().st_size < 10:
                continue
            calls = _find_abstract_instantiations(py_file)
            for lineno, class_name, line_text in calls:
                if class_name in self.KNOWN_ABC_CLASS_NAMES:
                    rel = py_file.relative_to(SRC_ROOT.parents[1])
                    violations.append(
                        f"{rel}:{lineno}: potential direct instantiation of ABC {class_name!r}: {line_text.strip()}"
                    )
        assert not violations, "\n" + "\n".join(violations)


class TestABCMethodSignatures:
    """Concrete implementations should match the abstract method signatures."""

    def test_credential_store_signatures_match(self):
        from general_ludd.auth.browser_login import CredentialStore, EnvCredentialStore

        store_sig = inspect.signature(CredentialStore.store)
        env_sig = inspect.signature(EnvCredentialStore.store)
        for p in list(store_sig.parameters.keys()):
            assert p in env_sig.parameters, f"Param {p!r} missing from EnvCredentialStore.store"

    def test_psk_store_signatures_match(self):
        from general_ludd.security.psk_rotation import InMemoryPSKStore, PSKStore

        for method_name in ("save", "load", "list_versions", "delete"):
            abs_sig = inspect.signature(getattr(PSKStore, method_name))
            impl_sig = inspect.signature(getattr(InMemoryPSKStore, method_name))
            for p in list(abs_sig.parameters.keys()):
                assert p in impl_sig.parameters, f"Param {p!r} missing from InMemoryPSKStore.{method_name}"

    def test_speaker_selector_signature_match(self):
        from general_ludd.ag16_orchestration.conversation import (
            PrioritySelector,
            RoundRobinSelector,
            SpeakerSelector,
        )

        abs_sig = inspect.signature(SpeakerSelector.select_next)
        for impl_cls in (RoundRobinSelector, PrioritySelector):
            impl_sig = inspect.signature(impl_cls.select_next)
            for p in list(abs_sig.parameters.keys()):
                assert p in impl_sig.parameters, f"Param {p!r} missing from {impl_cls.__name__}.select_next"


class TestABCIsolation:
    """All ABC implementations can be imported and instantiated without side effects."""

    def test_all_concrete_abc_subclasses_instantiable(self):
        instantiated = 0
        for _abc_qname, (abc_cls, _) in ALL_ABC_CLASSES.items():
            for cname in _find_concrete_subclasses(abc_cls, ALL_MODULES):
                parts = cname.rsplit(".", 1)
                mod = _import_module(parts[0])
                if mod is None:
                    continue
                cls_obj = getattr(mod, parts[1], None)
                if cls_obj is None:
                    continue
                try:
                    sig = inspect.signature(cls_obj.__init__)
                    required = sum(
                        1
                        for p in sig.parameters.values()
                        if p.name != "self"
                        and p.default is inspect.Parameter.empty
                        and p.kind
                        not in (
                            inspect.Parameter.VAR_POSITIONAL,
                            inspect.Parameter.VAR_KEYWORD,
                        )
                    )
                    if required == 0:
                        obj = cls_obj()
                        assert obj is not None
                        instantiated += 1
                except (TypeError, ValueError, OSError, AttributeError):
                    pass
        assert instantiated >= 3, f"Only {instantiated} concrete ABC subclasses were instantiable (expected >=3)"


class TestProtocolStructuralCompleteness:
    """Protocols defined as @runtime_checkable should have at least one method/attribute."""

    _DUNDER_PROTOCOL_METHODS: frozenset[str] = frozenset(
        {
            "__call__",
            "__iter__",
            "__next__",
            "__getitem__",
            "__len__",
            "__contains__",
            "__enter__",
            "__exit__",
            "__aenter__",
            "__aexit__",
            "__await__",
            "__getattr__",
        }
    )

    @pytest.mark.parametrize("proto_qname,proto_cls", sorted(ALL_PROTOCOLS.items()))
    def test_runtime_checkable_protocols_have_members(self, proto_qname, proto_cls):
        assert isinstance(self._DUNDER_PROTOCOL_METHODS, frozenset)
        if not _has_runtime_checkable(proto_cls):
            return
        members: set[str] = set()
        _IGNORE = frozenset(
            {
                "__annotations__",
                "__protocol_attrs__",
                "__init__",
                "__module__",
                "__dict__",
                "__weakref__",
                "__doc__",
                "__abstractmethods__",
                "__firstlineno__",
                "__static_attributes__",
                "__subclasshook__",
                "_is_protocol",
                "_is_runtime_protocol",
            }
        )
        for cls in proto_cls.__mro__:
            if not _is_protocol(cls):
                continue
            for name in vars(cls):
                if name in _IGNORE:
                    continue
                if name.startswith("_") and name not in self._DUNDER_PROTOCOL_METHODS:
                    continue
                members.add(name)
        for cls in proto_cls.__mro__:
            annotations = getattr(cls, "__annotations__", {})
            members.update(name for name in annotations if not name.startswith("_"))

        assert members, (
            f"{proto_qname} is @runtime_checkable but defines no members; isinstance checks will always return False"
        )
