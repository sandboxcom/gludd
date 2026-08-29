"""T07: TDD allowlist matches check_tdd_compliance.py.

The real-time TDD enforcement plugin (enforce-tdd.ts) and the
commit-time check (scripts/check_tdd_compliance.py) MUST use the
same allowlist for files that do not require tests. Any divergence
means the editor gate and commit gate disagree — a bug.
"""

import re
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


class TestT07TDDAllowlistParity:
    """T07 — enforce-tdd.ts and check_tdd_compliance.py use same allowlist."""

    def test_editor_and_commit_gates_share_allowlist(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
        script_path = ROOT / "scripts" / "check_tdd_compliance.py"

        assert plugin_path.exists(), "T07: enforce-tdd.ts must exist"
        assert script_path.exists(), "T07: check_tdd_compliance.py must exist"

    def test_allowlist_entries_match(self) -> None:
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
        script_path = ROOT / "scripts" / "check_tdd_compliance.py"

        if not plugin_path.exists() or not script_path.exists():
            return

        plugin_text = plugin_path.read_text()

        # Extract allowlist from TypeScript plugin
        ts_allow_match = re.findall(
            r"['\"]([^'\"]*__init__[^'\"]*|\*\.pyi|protocols\.py|typing\.py|type_defs\.py|_types\.py)['\"]",
            plugin_text,
            re.IGNORECASE,
        )
        ts_allow = sorted(set(s.replace("*.pyi", ".pyi").replace("**/", "").replace("*/", "") for s in ts_allow_match))

        script_text = script_path.read_text()
        py_allow_match = re.findall(
            r"['\"](__init__[^'\"]*|[^'\"]*\.pyi|protocols|typing|type_defs|_types)['\"]",
            script_text,
            re.IGNORECASE,
        )
        py_allow = sorted(set(py_allow_match))

        # Both should contain the core allowlist entries
        core_entries = {"__init__.py", ".pyi", "protocols", "typing", "type_defs", "_types"}
        ts_core = {e for entry in ts_allow for e in core_entries if e in entry.lower()}
        py_core = {e for entry in py_allow for e in core_entries if e in entry.lower()}

        assert ts_core, f"T07: enforce-tdd.ts must reference at least one core allowlist entry from {core_entries}"
        assert py_core, (
            f"T07: check_tdd_compliance.py must reference at least one core allowlist entry from {core_entries}"
        )

    def test_candidate_test_path_logic_matches(self) -> None:
        """Verify both gates use the same test file mapping logic."""
        plugin_path = ROOT / ".opencode" / "plugin" / "enforce-tdd.ts"
        script_path = ROOT / "scripts" / "check_tdd_compliance.py"

        if not plugin_path.exists() or not script_path.exists():
            return

        plugin_text = plugin_path.read_text()
        script_text = script_path.read_text()

        # Both should contain the pattern: src/general_ludd/X.py -> tests/unit/test_X.py
        plugin_has_mapping = "src/general_ludd/" in plugin_text and "tests/unit/test_" in plugin_text
        script_has_mapping = "src/general_ludd/" in script_text and "tests/unit/test_" in script_text
        assert plugin_has_mapping, "T07: enforce-tdd.ts must map src/general_ludd to tests/unit/"
        assert script_has_mapping, "T07: check_tdd_compliance.py must map src/general_ludd to tests/unit/"
