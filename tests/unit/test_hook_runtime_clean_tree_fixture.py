"""Structural regression pin for the hook-runtime dirty-tree fixtures.

Background: CI failed twice because scripts/test_hook_runtime.py created
dirty-tree fixtures OUTSIDE the checkout (under tempfile.gettempdir()) while
enforce-clean-tree's hook reads `git status --porcelain` at the repo root.
A fixture outside the checkout is invisible to that check, so the deny path
could never fire and every deny-expecting runtime test passed vacuously.

The harness now writes fixtures INSIDE the checkout at
scripts/gludd-hook-test-dirty-<label>-<pid>.txt, and a session-scoped cleanup
fixture globs that pattern away so a crashed run cannot leave the real tree
dirty (which would self-lock the plugin). These tests pin that contract at
three layers: the source joins ROOT with "scripts" (never gettempdir), the
cleanup glob covers every path the fixture builder can produce, and the seven
existing call sites still route through the in-checkout builder.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import re
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = ROOT / "scripts" / "test_hook_runtime.py"
DIRTY_FIXTURE_GLOB = "gludd-hook-test-dirty-*.txt"
EXPECTED_CALL_SITES = 7


def _load_harness() -> ModuleType:
    """Load scripts/test_hook_runtime.py as a plain module by path.

    Avoids sys.path assumptions: pytest only guarantees scripts/ and src/
    are importable, not the repo root package.
    """
    assert HARNESS_PATH.exists(), f"Harness missing at {HARNESS_PATH}"
    spec = importlib.util.spec_from_file_location("test_hook_runtime_harness", HARNESS_PATH)
    assert spec is not None and spec.loader is not None, "Harness spec failed to load"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def _harness_source() -> str:
    return HARNESS_PATH.read_text()


def _extract_function(source: str, name: str) -> str:
    """Return the source block of a module-level function, docstring included."""
    lines = source.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"def {name}("))
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i] and not lines[i][0].isspace()),
        len(lines),
    )
    return "\n".join(lines[start:end])


class TestDirtyFixtureLivesInsideCheckout:
    """The fixture builder must return paths inside the checkout, not /tmp."""

    def test_dirty_test_path_returns_path_under_checkout_scripts(self):
        # === Arrange ===
        module_root = Path(harness.ROOT)

        # === Act ===
        fixture = Path(harness._dirty_test_path("regression"))

        # === Assert ===
        assert module_root == ROOT.resolve(), "Harness ROOT must be the repo root"
        assert fixture.parent == module_root / "scripts", f"Fixture must live in scripts/, got {fixture}"
        assert fixture.name.startswith("gludd-hook-test-dirty-regression-"), (
            f"Fixture name must carry the label prefix, got {fixture.name}"
        )
        assert fixture.is_relative_to(module_root), f"Fixture must be inside the checkout, got {fixture}"

    def test_dirty_test_path_never_returns_tempdir(self):
        # === Arrange ===
        import tempfile

        temp_root = Path(tempfile.gettempdir())

        # === Act ===
        fixture = Path(harness._dirty_test_path("tempdir"))

        # === Assert ===
        assert not fixture.is_relative_to(temp_root), (
            f"Fixture must not be under tempfile.gettempdir() ({temp_root}), got {fixture}"
        )

    def test_dirty_test_path_source_joins_root_with_scripts(self):
        # === Arrange ===
        source = _harness_source()

        # === Act ===
        body = _extract_function(source, "_dirty_test_path")

        # === Assert ===
        assert 'ROOT / "scripts"' in body, "The builder must join ROOT with the scripts/ directory"
        assert "gettempdir" not in body, "The builder must never reference tempfile.gettempdir()"


class TestCleanupGlobCoversFixtures:
    """The session-scoped cleanup must glob exactly what the builder creates."""

    def test_cleanup_globs_fixture_pattern_under_scripts(self):
        # === Arrange ===
        source = _harness_source()

        # === Act ===
        body = _extract_function(source, "_remove_legacy_workspace_artifacts")

        # === Assert ===
        assert f'"{DIRTY_FIXTURE_GLOB}"' in body, f"Cleanup must glob the {DIRTY_FIXTURE_GLOB} pattern"
        assert '(ROOT / "scripts").glob(' in body, "Cleanup glob must be bounded to ROOT/scripts"

    def test_cleanup_glob_matches_every_fixture_label(self):
        # === Arrange ===
        labels = ("dispatch", "disabled", "subagent", "runtime", "nondispatch")

        # === Act ===
        names = [Path(harness._dirty_test_path(label)).name for label in labels]

        # === Assert ===
        for name in names:
            assert fnmatch.fnmatch(name, DIRTY_FIXTURE_GLOB), (
                f"Cleanup glob {DIRTY_FIXTURE_GLOB} must match fixture {name} "
                f"— otherwise a crashed run leaves the real tree dirty"
            )


class TestCallSitesStillRouteThroughBuilder:
    """All seven deny/allow/subagent tests must use the in-checkout builder."""

    def test_dirty_test_path_has_seven_call_sites(self):
        # === Arrange ===
        source = _harness_source()

        # === Act ===
        call_lines = [
            line for line in source.splitlines() if "_dirty_test_path(" in line and not line.lstrip().startswith("def ")
        ]

        # === Assert ===
        assert len(call_lines) == EXPECTED_CALL_SITES, (
            f"Expected {EXPECTED_CALL_SITES} _dirty_test_path( call sites, "
            f"found {len(call_lines)} — a reverted call site silently re-breaks "
            f"the enforce-clean-tree deny path"
        )

    def test_every_call_site_is_an_assignment_under_test(self):
        # === Arrange ===
        source = _harness_source()

        # === Act ===
        assignments = re.findall(
            r"^\s{4}test_file = _dirty_test_path\(\"([^\"]+)\"\)$",
            source,
            re.MULTILINE,
        )

        # === Assert ===
        assert len(assignments) == EXPECTED_CALL_SITES, (
            "Every call site must assign to test_file inside a test function"
        )
        assert "dispatch" in assignments, "dispatch deny test must use the builder"
        assert "subagent" in assignments, "subagent skip test must use the builder"
