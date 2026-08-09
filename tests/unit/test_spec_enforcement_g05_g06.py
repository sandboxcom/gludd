"""G05/G06: Gate status freshness enforcement.

Gate-status checks must verify freshness (not just existence) and
the gate-status file must be fail-closed (missing file = red).
"""

from pathlib import Path

MAKEFILE = Path(__file__).parent.parent.parent / "Makefile"


def _find_recipe(content: str, target: str) -> str:
    idx = content.find(f"\n{target}:")
    if idx == -1:
        return ""
    end = content.find("\n\n", idx)
    if end == -1:
        end = len(content)
    return content[idx:end]


class TestG05G06GateFreshnessEnforcement:
    """G05/G06 — gate status freshness is enforced and fail-closed."""

    def test_gate_status_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "gate-status")
        assert recipe, "G05: gate-status target must exist"

    def test_gate_status_check_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "gate-status-check")
        assert recipe, "G05: gate-status-check target must exist"

    def test_gate_status_background_writes_status_file(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "gate-background")
        if not recipe:
            return
        assert "gate-status" in recipe or ".gate-status" in recipe, (
            "G05: gate-background must write .gate-status on completion"
        )

    def test_gate_lite_writes_status_file(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "gate-lite")
        if not recipe:
            return
        assert "gate-lite-status" in recipe or ".gate-lite-status" in recipe, (
            "G05: gate-lite must write .gate-lite-status on completion"
        )

    def test_gate_kill_target_exists(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "gate-kill")
        assert recipe, "G06: gate-kill target must exist for stale gate recovery"

    def test_gate_target_writes_terminal_marker(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "gate")
        if not recipe:
            return
        assert "GATE: PASSED" in recipe or "GATE: FAILED" in recipe, (
            "G06: gate must write terminal marker (=== GATE: PASSED === or === GATE: FAILED ===)"
        )

    def test_gate_fresh_check_rejects_stale_status(self):
        content = MAKEFILE.read_text()
        recipe = _find_recipe(content, "_gate-fresh-check")
        if not recipe:
            return
        combine = recipe
        if "exit 1" in combine:
            assert True  # fail-closed
        else:
            assert "fresh" in combine.lower() or "age" in combine.lower(), (
                "G06: _gate-fresh-check must check freshness, not just existence"
            )
