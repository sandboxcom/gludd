from pathlib import Path

from scripts.makefile_layout import compose_makefile


def test_gate_kill_cleans_namespaced_async_gate_lock() -> None:
    script = (
        Path(__file__).parents[2] / "scripts" / "kill_owned_gate.py"
    ).read_text()
    assert "resource_root" in script
    assert '"async-gate.lock"' in script


def test_gate_kill_invokes_namespace_safe_adaptive_gate_reaper() -> None:
    makefile = compose_makefile(Path(__file__).parents[2] / "Makefile")
    start = makefile.index("gate-kill:")
    recipe = makefile[start : makefile.find("\n\n", start)]
    assert "kill_owned_gate.py" in recipe
    assert "APPLY=1" in recipe
