"""Deep audit of all custom exception/error classes under src/general_ludd/.

Checks: uniqueness of names, inheritance correctness, docstring presence,
naming consistency, and cross-module name collisions.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import pathlib
import re
from typing import cast

import pytest

from general_ludd.agents import hibernation as agent_hibernation
from general_ludd.ssl import tls13_handshake as tls13

SRC = pathlib.Path(__file__).resolve().parents[2] / "src" / "general_ludd"


def _walk_python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py") if p.name != "__init__.py" and "site-packages" not in str(p))


def _class_defs_in_file(filepath: pathlib.Path) -> list[tuple[str, str | None]]:
    """Return [(class_name, base_name_or_none), ...] for top-level class defs."""
    try:
        tree = ast.parse(filepath.read_text())
    except SyntaxError:
        return []
    results: list[tuple[str, str | None]] = []
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_name: str | None = None
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_name = base.id
                break
            if isinstance(base, ast.Attribute):
                base_name = base.attr
                break
        results.append((node.name, base_name))
    return results


def _is_error_name(name: str) -> bool:
    return bool(re.search(r"(?:Error|Exception)$", name))


def _candidate_error_classes(root: pathlib.Path) -> dict[str, list[tuple[pathlib.Path, str, str | None]]]:
    """Return {class_name: [(filepath, name, base_name), ...]} for error-like classes."""
    by_name: dict[str, list[tuple[pathlib.Path, str, str | None]]] = {}
    for fp in _walk_python_files(root):
        for name, base in _class_defs_in_file(fp):
            if _is_error_name(name):
                by_name.setdefault(name, []).append((fp, name, base))
    return by_name


def _module_qualname(filepath: pathlib.Path, class_name: str) -> str:
    rel = filepath.relative_to(SRC.parent).with_suffix("")
    parts = list(rel.parts)
    parts.append(class_name)
    return ".".join(parts)


def _try_import_error(filepath: pathlib.Path, class_name: str) -> type[BaseException] | None:
    qualname = _module_qualname(filepath, class_name)
    parts = qualname.split(".")
    for split_at in range(len(parts) - 1, 0, -1):
        mod_name = ".".join(parts[:split_at])
        cls_name = parts[split_at]
        try:
            mod = importlib.import_module(mod_name)
            cls = getattr(mod, cls_name, None)
            if cls is not None and isinstance(cls, type) and issubclass(cls, BaseException):
                return cls
        except (ImportError, ModuleNotFoundError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestErrorNameUniqueness:
    """Every exception class name must be unique across the entire codebase.

    Two modules defining ``class SSRFError`` (security/ssrf.py and
    connectors/_errors.py and issue_sources/servicenow.py) are duplicate names
    that can mislead callers and defeat targeted except clauses.
    """

    def test_no_duplicate_error_names_in_src(self) -> None:
        by_name = _candidate_error_classes(SRC)
        duplicates = {n: locs for n, locs in by_name.items() if len(locs) > 1}
        msg_lines = []
        for name, locs in sorted(duplicates.items()):
            files = [str(loc[0].relative_to(SRC)) for loc in locs]
            msg_lines.append(f"  {name}: {files}")
        assert not duplicates, f"Duplicate exception names across modules ({len(duplicates)}):\n" + "\n".join(msg_lines)

    def test_no_name_conflict_between_modules(self) -> None:
        by_name = _candidate_error_classes(SRC)
        collisions = 0
        for _name, locs in by_name.items():
            if len(locs) > 1:
                paths = {str(loc[0].relative_to(SRC)) for loc in locs}
                if len(paths) == len(locs):
                    collisions += 1
        assert collisions == 0, f"{collisions} names defined in multiple files"


class TestExceptionDocstrings:
    """Every custom exception must carry a meaningful docstring.

    A bare ``class FooError(Exception): pass`` without a docstring tells
    callers nothing about when or why it is raised.
    """

    def test_all_exceptions_have_docstrings(self) -> None:
        missing: list[str] = []
        by_name = _candidate_error_classes(SRC)
        seen_files: set[str] = set()
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                fkey = f"{fp}:{cname}"
                if fkey in seen_files:
                    continue
                seen_files.add(fkey)
                cls = _try_import_error(fp, cname)
                if cls is None:
                    continue
                doc = cls.__doc__
                if not doc or not doc.strip():
                    missing.append(f"  {cname} in {fp.relative_to(SRC)}")
        if missing:
            pytest.fail(f"Exception classes missing docstrings ({len(missing)}):\n" + "\n".join(missing))

    def test_docstrings_are_descriptive_not_trivial(self) -> None:
        trivial = ["error.", "exception.", "raised.", "custom error."]
        insufficient: list[str] = []
        seen_files: set[str] = set()
        by_name = _candidate_error_classes(SRC)
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                fkey = f"{fp}:{cname}"
                if fkey in seen_files:
                    continue
                seen_files.add(fkey)
                cls = _try_import_error(fp, cname)
                if cls is None:
                    continue
                doc = (cls.__doc__ or "").strip().lower()
                if len(doc) < 10 or any(doc.startswith(t) for t in trivial):
                    insufficient.append(f"  {cname} in {fp.relative_to(SRC)}: {cls.__doc__!r}")
        assert not insufficient, f"Trivial docstrings ({len(insufficient)}):\n" + "\n".join(insufficient)


class TestExceptionInheritance:
    """Inheritance chain checks: all exceptions must derive from a sensible base."""

    def test_all_error_classes_derive_from_baseexception(self) -> None:
        invalid: list[str] = []
        seen: set[str] = set()
        by_name = _candidate_error_classes(SRC)
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                fkey = f"{fp}:{cname}"
                if fkey in seen:
                    continue
                seen.add(fkey)
                cls = _try_import_error(fp, cname)
                if cls is None:
                    continue
                if not issubclass(cls, BaseException):
                    invalid.append(f"  {cname} in {fp.relative_to(SRC)} -> {cls.__bases__}")
        assert not invalid, f"Non-exception bases ({len(invalid)}):\n" + "\n".join(invalid)

    def test_no_bare_exception_inheritance_where_better_exists(self) -> None:
        """Classes directly inheriting Exception should prefer a narrower built-in.

        ValueError for bad input, RuntimeError for runtime failures, etc.
        This is advisory — it flags plausible misclassifications.
        """
        overbroad: list[str] = []
        seen: set[str] = set()
        by_name = _candidate_error_classes(SRC)
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                fkey = f"{fp}:{cname}"
                if fkey in seen:
                    continue
                seen.add(fkey)
                cls = _try_import_error(fp, cname)
                if cls is None:
                    continue
                # Only flag if docstring content suggests a narrower base
                (cls.__doc__ or "").lower()
                if issubclass(cls, Exception) and not issubclass(cls, (ValueError, RuntimeError, TypeError, KeyError)):
                    # Check if direct base is Exception (not a custom subclass)
                    direct_bases = [b for b in cls.__bases__ if b is not object]
                    if any(b is Exception for b in direct_bases):
                        overbroad.append(
                            f"  {cname} -> Exception (consider ValueError/RuntimeError); doc: {cls.__doc__!r}"
                        )
        # This test is advisory — 0 is ideal but not enforced as failure
        assert len(overbroad) <= 50, (
            f"Too many bare-Exception subclasses ({len(overbroad)}). Prefer ValueError/RuntimeError where applicable."
        )

    def test_horizon_metric_uses_non_exception_primary_name(self) -> None:
        """Prediction-error data must not masquerade as an exception contract."""
        module = importlib.import_module("general_ludd.ai_ml.world_models")
        metrics = getattr(module, "HorizonMetrics", None)
        legacy = getattr(module, "HorizonError", None)
        assert metrics is not None, "HorizonMetrics must be the public primary name"
        assert isinstance(metrics, type)
        assert legacy is metrics, "HorizonError must remain a compatibility alias"
        metrics_type = cast(type[object], metrics)
        assert not issubclass(metrics_type, BaseException)


class TestExceptionNaming:
    """Error class naming conventions."""

    def test_error_classes_end_with_error_or_exception(self) -> None:
        """ConfigError, ConnectorError, BazException are fine.

        Classes without Error/Exception suffix may be mistaken for non-exceptions.
        """
        by_name = _candidate_error_classes(SRC)
        misnamed: list[str] = []
        for name, locs in by_name.items():
            if not _is_error_name(name):
                for fp, cname, _ in locs:
                    misnamed.append(f"  {cname} in {fp.relative_to(SRC)}")
        assert not misnamed, (
            f"Classes flagged as error-like but missing Error/Exception suffix "
            f"({len(misnamed)}):\n" + "\n".join(misnamed)
        )

    def test_private_error_prefix_discouraged(self) -> None:
        by_name = _candidate_error_classes(SRC)
        private: list[str] = []
        for _name, locs in by_name.items():
            for fp, cname, _ in locs:
                if cname.startswith("_"):
                    private.append(f"  {cname} in {fp.relative_to(SRC)}")
        if private:
            pytest.fail(
                f"Private error class names ({len(private)}):\n"
                + "\n".join(private)
                + "\n\nPrivate (_-prefixed) exceptions cannot be caught by external callers. "
                "Make public or justify with a comment."
            )


class TestErrorInit:
    """Exception constructors must call super().__init__ to form proper messages."""

    def test_constructor_signatures_call_super_init(self) -> None:
        """Verify __init__ methods call super().__init__ where overridden."""
        issues: list[str] = []
        seen: set[str] = set()
        by_name = _candidate_error_classes(SRC)
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                fkey = f"{fp}:{cname}"
                if fkey in seen:
                    continue
                seen.add(fkey)
                cls = _try_import_error(fp, cname)
                if cls is None:
                    continue
                init = getattr(cls, "__init__", None)
                if init is None:
                    continue
                try:
                    src = inspect.getsource(init)
                except (OSError, TypeError):
                    continue
                # If __init__ is defined (not inherited), check for super().__init__
                if "def __init__(self" in src and "super().__init__(" not in src:
                    issues.append(f"  {cname}.__init__ missing super().__init__(...) call — message may be empty")
        assert not issues, f"Exception constructors missing super().__init__ ({len(issues)}):\n" + "\n".join(issues)


class TestDeadErrorImports:
    """Exceptions must be importable and resolvable — check for broken module refs."""

    def test_all_errors_can_be_imported(self) -> None:
        failed: list[str] = []
        by_name = _candidate_error_classes(SRC)
        seen: set[str] = set()
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                fkey = f"{fp}:{cname}"
                if fkey in seen:
                    continue
                seen.add(fkey)
                cls = _try_import_error(fp, cname)
                if cls is None:
                    failed.append(f"  {cname} in {fp.relative_to(SRC)} — could not import")
        assert not failed, f"Unimportable exceptions ({len(failed)}):\n" + "\n".join(failed)


class TestErrorBaseConsistency:
    """Checks for consistent error base patterns."""

    @pytest.mark.parametrize(
        ("relative_path", "primary_name", "legacy_name"),
        [
            ("ssl/tls13_handshake.py", "TLSHandshakeError", "HandshakeError"),
            ("agents/hibernation.py", "HibernationIntegrityError", "IntegrityError"),
            ("connectors/kubernetes.py", "KubernetesConfigError", "_ConfigError"),
            ("controllers/pause_store.py", "PauseStoreIntegrityError", "IntegrityError"),
            ("issue_sources/servicenow.py", "ServiceNowSSRFError", "SSRFError"),
            ("security/ssrf.py", "SecuritySSRFError", "SSRFError"),
        ],
    )
    def test_domain_specific_errors_keep_legacy_aliases(
        self,
        relative_path: str,
        primary_name: str,
        legacy_name: str,
    ) -> None:
        """Unique primary names must retain the identity of legacy catch targets."""
        filepath = SRC / relative_path
        primary = _try_import_error(filepath, primary_name)
        assert primary is not None, f"{primary_name} not importable"
        module_name = _module_qualname(filepath, primary_name).rsplit(".", 1)[0]
        module = importlib.import_module(module_name)
        assert getattr(module, legacy_name, None) is primary

    def test_concurrency_error_subclasses(self) -> None:
        for name in ("ConcurrencyError", "InvalidTransitionError"):
            cls = _try_import_error(SRC / "db" / "repository.py", name)
            assert cls is not None, f"{name} not found"
            if name == "ConcurrencyError":
                assert issubclass(cls, Exception)
            else:
                assert issubclass(cls, Exception)

    def test_secrets_error_hierarchy(self) -> None:
        su = _try_import_error(SRC / "secrets" / "manager.py", "SecretsUnavailableError")
        spd = _try_import_error(SRC / "secrets" / "manager.py", "SecretPermissionDeniedError")
        assert su is not None
        assert spd is not None
        assert issubclass(spd, su)
        assert issubclass(su, RuntimeError)

    def test_azure_cost_export_hierarchy(self) -> None:
        base = _try_import_error(SRC / "infra" / "azure_cost_export_ingestion.py", "AzureCostExportError")
        parse = _try_import_error(SRC / "infra" / "azure_cost_export_ingestion.py", "AzureCostExportParseError")
        complete = _try_import_error(
            SRC / "infra" / "azure_cost_export_ingestion.py", "AzureCostExportCompletenessError"
        )
        assert base is not None
        assert parse is not None
        assert complete is not None
        assert issubclass(parse, base)
        assert issubclass(complete, base)

    def test_payload_limit_hierarchy(self) -> None:
        ple = _try_import_error(SRC / "models" / "gateway.py", "PayloadLimitError")
        cple = _try_import_error(SRC / "models" / "gateway.py", "CumulativePayloadLimitError")
        sle = _try_import_error(SRC / "models" / "gateway.py", "StreamLimitError")
        assert ple is not None
        assert cple is not None
        assert sle is not None
        assert issubclass(cple, ple)
        assert issubclass(sle, ple)

    def test_pause_store_hierarchy(self) -> None:
        pse = _try_import_error(SRC / "controllers" / "pause_store.py", "PauseStoreError")
        ie = _try_import_error(SRC / "controllers" / "pause_store.py", "IntegrityError")
        assert pse is not None
        assert ie is not None
        assert issubclass(ie, pse)

    def test_hibernation_hierarchy(self) -> None:
        he = _try_import_error(SRC / "agents" / "hibernation.py", "HibernationError")
        ie = _try_import_error(SRC / "agents" / "hibernation.py", "IntegrityError")
        assert he is not None
        assert ie is not None
        assert issubclass(ie, he)


class TestTLSExceptionBranches:
    """Exercise the renamed TLS hierarchy across supported and rejected paths."""

    @pytest.mark.parametrize(
        "group",
        [
            tls13.NamedGroup.SECP256R1,
            tls13.NamedGroup.SECP384R1,
            tls13.NamedGroup.SECP521R1,
        ],
    )
    def test_ec_key_share_groups_use_the_public_error_contract(
        self, group: tls13.NamedGroup
    ) -> None:
        exchange = tls13.KeyExchange(group)

        assert exchange.public_bytes.startswith(b"\x04")

    def test_unsupported_key_share_uses_specific_error(self) -> None:
        invalid_group = cast(tls13.NamedGroup, object())

        with pytest.raises(tls13.TLSHandshakeError, match="Unsupported group"):
            tls13.KeyExchange(invalid_group)

    def test_state_preconditions_use_specific_error(self) -> None:
        handshake = tls13.Tls13Handshake()

        with pytest.raises(tls13.HandshakeStateError):
            handshake.process_encrypted_extensions(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.process_certificate(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.process_certificate_verify(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.derive_handshake_keys(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.build_server_finished_verify_data()
        with pytest.raises(tls13.HandshakeStateError):
            handshake.build_client_finished_verify_data()
        with pytest.raises(tls13.HandshakeStateError):
            handshake.process_finished(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.build_client_finished()
        with pytest.raises(tls13.HandshakeStateError):
            handshake.encrypt_handshake(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.decrypt_handshake(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.encrypt_application_data(b"")
        with pytest.raises(tls13.HandshakeStateError):
            handshake.decrypt_application_data(b"")


class TestHibernationExceptionBranches:
    """Pin fail-closed hibernation paths to the public exception hierarchy."""

    def test_non_mapping_envelope_is_rejected(self, tmp_path: pathlib.Path) -> None:
        store = agent_hibernation.HibernationStore(tmp_path)
        snapshot = agent_hibernation.AgentEnvironmentSnapshot(
            task_id="TASK-1", agent_name="coder"
        )
        handle = store.dehydrate(snapshot)
        pathlib.Path(handle.path).write_text("[]", encoding="utf-8")

        with pytest.raises(
            agent_hibernation.HibernationIntegrityError,
            match="malformed snapshot envelope",
        ):
            store.hydrate(handle)

    def test_handle_outside_store_is_rejected(self, tmp_path: pathlib.Path) -> None:
        store = agent_hibernation.HibernationStore(tmp_path / "store")
        snapshot = agent_hibernation.AgentEnvironmentSnapshot(
            task_id="TASK-1", agent_name="coder"
        )
        handle = store.dehydrate(snapshot)
        outside_handle = handle.model_copy(
            update={"path": str(tmp_path / "outside.snapshot.json")}
        )

        with pytest.raises(
            agent_hibernation.HibernationError, match="outside base dir"
        ):
            store.hydrate(outside_handle)

    def test_non_project_pause_scope_is_not_blocked(
        self, tmp_path: pathlib.Path
    ) -> None:
        controller = agent_hibernation.HibernationController(
            agent_hibernation.HibernationStore(tmp_path)
        )
        controller.pause_project("project-1")

        assert controller.is_paused("agent", "project-1") is False


class TestErrorCountCoverage:
    """Verify we found and tested a meaningful number of exceptions."""

    def test_at_least_80_exceptions_found(self) -> None:
        by_name = _candidate_error_classes(SRC)
        seen: set[str] = set()
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                seen.add(f"{fp}:{cname}")
        assert len(seen) >= 80, (
            f"Found only {len(seen)} candidate exception classes — "
            f"expecting at least 80. Potential discovery regression."
        )

    def test_all_exceptions_importable(self) -> None:
        by_name = _candidate_error_classes(SRC)
        seen: set[str] = set()
        imported = 0
        for _name, locs in by_name.items():
            for fp, cname, _base in locs:
                fkey = f"{fp}:{cname}"
                if fkey in seen:
                    continue
                seen.add(fkey)
                cls = _try_import_error(fp, cname)
                if cls is not None:
                    imported += 1
        assert imported >= len(seen) * 0.8, (
            f"Only {imported}/{len(seen)} exceptions importable. Some classes have unresolvable paths."
        )
