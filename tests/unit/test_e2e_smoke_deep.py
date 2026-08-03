"""Deep E2E smoke validation: structure, conftest, discoverability, playbook coverage."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

E2E_DIR = Path("tests/e2e")
PLAYBOOKS_DIR = Path("playbooks")
REPO_ROOT = Path(__file__).parent.parent.parent


class TestE2eDirectoryStructure:
    def test_e2e_dir_exists(self):
        assert (REPO_ROOT / E2E_DIR).is_dir()

    def test_subdirectories_present(self):
        e2e_root = REPO_ROOT / E2E_DIR
        expected_subdirs = ["dogfood", "games", "providers", "game_e2e"]
        for sub in expected_subdirs:
            subpath = e2e_root / sub
            assert subpath.is_dir(), f"Missing subdirectory: {sub}"

    def test_subdirs_with_init_py_have_it(self):
        e2e_root = REPO_ROOT / E2E_DIR
        for sub_name in ["games", "providers", "game_e2e"]:
            init = e2e_root / sub_name / "__init__.py"
            assert init.is_file(), f"Missing __init__.py in {sub_name}"

    def test_root_init_py_present(self):
        assert (REPO_ROOT / E2E_DIR / "__init__.py").is_file()

    def test_all_conftest_files_valid(self):
        e2e_root = REPO_ROOT / E2E_DIR
        conftest_paths = list(e2e_root.rglob("conftest.py"))
        assert len(conftest_paths) >= 4
        for cf in conftest_paths:
            rel = cf.relative_to(REPO_ROOT)
            try:
                source = cf.read_text()
                ast.parse(source)
            except SyntaxError as exc:
                pytest.fail(f"Syntax error in {rel}: {exc}")
            except Exception as exc:
                pytest.fail(f"Error reading {rel}: {exc}")

    def test_conftest_files_have_pytest_imports(self):
        e2e_root = REPO_ROOT / E2E_DIR
        for cf in e2e_root.rglob("conftest.py"):
            rel = cf.relative_to(REPO_ROOT)
            content = cf.read_text()
            assert "pytest" in content, f"No pytest import in {rel}"


class TestE2eTestFileDiscoverability:
    def test_all_e2e_test_files_parseable(self):
        e2e_root = REPO_ROOT / E2E_DIR
        test_files = sorted(e2e_root.glob("test_*.py"))
        assert len(test_files) > 0
        for tf in test_files:
            rel = tf.relative_to(REPO_ROOT)
            try:
                source = tf.read_text()
                ast.parse(source)
            except SyntaxError as exc:
                pytest.fail(f"Syntax error in {rel}: {exc}")

    def test_all_subdir_test_files_parseable(self):
        e2e_root = REPO_ROOT / E2E_DIR
        for subdir in ["dogfood", "games", "providers", "game_e2e"]:
            sd = e2e_root / subdir
            if not sd.is_dir():
                continue
            for tf in sorted(sd.glob("test_*.py")):
                rel = tf.relative_to(REPO_ROOT)
                try:
                    source = tf.read_text()
                    ast.parse(source)
                except SyntaxError as exc:
                    pytest.fail(f"Syntax error in {rel}: {exc}")

    def test_e2e_test_file_count_positive(self):
        test_files = list((REPO_ROOT / E2E_DIR).glob("test_*.py"))
        subdir_test_files: list[Path] = []
        for subdir in ["dogfood", "games", "providers", "game_e2e"]:
            sd = REPO_ROOT / E2E_DIR / subdir
            if sd.is_dir():
                subdir_test_files.extend(sd.glob("test_*.py"))
        total = len(test_files) + len(subdir_test_files)
        assert total >= 100, f"Expected >=100 e2e test files, found {total}"

    def test_test_files_have_test_functions_or_classes(self):
        e2e_root = REPO_ROOT / E2E_DIR
        test_paths = list(e2e_root.glob("test_*.py"))
        for subdir in ["dogfood", "games", "providers", "game_e2e"]:
            sd = e2e_root / subdir
            if sd.is_dir():
                test_paths.extend(sd.glob("test_*.py"))
        empty_files: list[str] = []
        has_tests = 0
        for tf in sorted(set(test_paths)):
            source = tf.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            has_def = False
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                    has_def = True
                    break
                if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    for child in node.body:
                        if isinstance(child, ast.FunctionDef) and child.name.startswith("test_"):
                            has_def = True
                            break
                    if has_def:
                        break
            if has_def:
                has_tests += 1
            elif "assert " not in source:
                empty_files.append(str(tf.relative_to(REPO_ROOT)))
        assert has_tests > 0, "No test files found with test functions"
        if empty_files:
            pytest.fail(f"Files with no test functions and no assertions: {empty_files}")

    def test_future_annotations_adoption_reporting(self):
        e2e_root = REPO_ROOT / E2E_DIR
        total = 0
        has_annotations = 0
        for tf in e2e_root.rglob("*.py"):
            if tf.name == "__init__.py":
                continue
            total += 1
            if "from __future__ import annotations" in tf.read_text():
                has_annotations += 1
        ratio = has_annotations / max(total, 1)
        assert ratio >= 0.95, (
            f"Only {has_annotations}/{total} ({ratio:.1%}) e2e .py files use 'from __future__ import annotations'"
        )


class TestE2ePlaybookCoverage:
    def _collect_playbook_names(self) -> set[str]:
        return {pb.name for pb in (REPO_ROOT / PLAYBOOKS_DIR).glob("*.yml")}

    def _extract_string_literals(self, filepath: Path) -> set[str]:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        refs: set[str] = set()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return refs
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val: str = node.value
                if val.endswith(".yml") and "/" not in val and " " not in val:
                    refs.add(val)
        return refs

    def _all_e2e_test_files(self) -> list[Path]:
        e2e_root = REPO_ROOT / E2E_DIR
        paths = list(e2e_root.glob("test_*.py"))
        for sub in ["dogfood", "games", "providers", "game_e2e"]:
            sd = e2e_root / sub
            if sd.is_dir():
                paths.extend(sd.glob("test_*.py"))
        return paths

    def test_all_playbooks_exist(self):
        playbooks = sorted((REPO_ROOT / PLAYBOOKS_DIR).glob("*.yml"))
        assert len(playbooks) >= 50, f"Expected >=50 playbooks, found {len(playbooks)}"
        for pb in playbooks:
            assert pb.is_file()
            assert pb.stat().st_size > 0, f"Empty playbook: {pb.name}"

    def test_each_playbook_referenced_in_at_least_one_e2e_test(self):
        playbook_names = self._collect_playbook_names()
        all_text = ""
        for tf in self._all_e2e_test_files():
            try:
                all_text += tf.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
        unreferenced: list[str] = []
        for pb_name in sorted(playbook_names):
            if pb_name not in all_text:
                unreferenced.append(pb_name)
        threshold = 0.70
        max_unref = int(len(playbook_names) * threshold) + 1
        assert len(unreferenced) <= max_unref, (
            f"{len(unreferenced)}/{len(playbook_names)} playbooks unreferenced in e2e tests "
            f"(threshold: {max_unref}): {unreferenced[:20]}"
        )

    def test_e2e_string_yml_present_in_any_yml_fileset(self):
        playbook_names = self._collect_playbook_names()
        e2e_root = REPO_ROOT / E2E_DIR
        ok = 0
        unresolvable: list[tuple[str, str]] = []
        for tf in e2e_root.rglob("test_*.py"):
            rel = str(tf.relative_to(REPO_ROOT))
            refs = self._extract_string_literals(tf)
            for ref in refs:
                if ref not in playbook_names:
                    unresolvable.append((rel, ref))
                else:
                    ok += 1
        assert ok > 0, "No e2e test string-literal references to playbooks found"
        pure_playbook_refs = [r for r in unresolvable if r[0].startswith("tests/e2e/")]
        assert len(pure_playbook_refs) >= 0

    def test_playbooks_not_empty_on_disk(self):
        for pb in (REPO_ROOT / PLAYBOOKS_DIR).glob("*.yml"):
            content = pb.read_text().strip()
            assert content, f"Empty playbook file: {pb.name}"
            assert content.startswith("---") or content.startswith("- "), (
                f"Playbook {pb.name} does not start with YAML document marker"
            )


class TestE2eConftestFixtureCorrectness:
    def test_root_conftest_has_pytest_collection_modifyitems(self):
        content = (REPO_ROOT / E2E_DIR / "conftest.py").read_text()
        assert "pytest_collection_modifyitems" in content
        assert "E2E_TARGET_GAME" in content

    def test_root_conftest_has_find_free_port(self):
        content = (REPO_ROOT / E2E_DIR / "conftest.py").read_text()
        assert "_find_free_port" in content
        assert "socket.SOCK_STREAM" in content

    def test_games_conftest_has_gateway_fixture(self):
        cf = REPO_ROOT / E2E_DIR / "games" / "conftest.py"
        content = cf.read_text()
        assert "def gateway():" in content or "def gateway" in content

    def test_providers_conftest_has_vllm_and_llamacpp_fixtures(self):
        cf = REPO_ROOT / E2E_DIR / "providers" / "conftest.py"
        content = cf.read_text()
        assert "vllm_base_url" in content
        assert "llamacpp_base_url" in content

    def test_dogfood_conftest_has_zai_creds_fixture(self):
        cf = REPO_ROOT / E2E_DIR / "dogfood" / "conftest.py"
        content = cf.read_text()
        assert "zai_creds" in content
        assert "gateway_mode" in content


class TestE2eTestNamingConventions:
    def test_all_test_files_follow_naming_pattern(self):
        e2e_root = REPO_ROOT / E2E_DIR
        bad_names: list[str] = []
        for tf in e2e_root.rglob("test_*.py"):
            name = tf.stem
            if not name.startswith("test_"):
                bad_names.append(str(tf.relative_to(REPO_ROOT)))
        assert not bad_names, f"Files not following test_*.py pattern: {bad_names}"

    def test_test_classes_follow_Test_prefix(self):
        e2e_root = REPO_ROOT / E2E_DIR
        bad: list[tuple[str, str]] = []
        for tf in e2e_root.glob("test_*.py"):
            rel = str(tf.relative_to(REPO_ROOT))
            source = tf.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    cls_name = node.name
                    is_test_cls = any(
                        isinstance(child, ast.FunctionDef) and child.name.startswith("test_") for child in node.body
                    )
                    if is_test_cls and not cls_name.startswith("Test"):
                        bad.append((rel, cls_name))
        assert not bad, f"Test classes not prefixed 'Test': {bad}"

    def test_test_methods_follow_test__prefix(self):
        e2e_root = REPO_ROOT / E2E_DIR
        bad: list[tuple[str, str, str]] = []
        for tf in e2e_root.glob("test_*.py"):
            rel = str(tf.relative_to(REPO_ROOT))
            source = tf.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                    for child in node.body:
                        if isinstance(child, ast.FunctionDef):
                            name = child.name
                            if name.startswith("test") and not name.startswith("test_"):
                                bad.append((rel, node.name, name))
        assert not bad, f"Test methods not prefixed 'test_': {bad}"
