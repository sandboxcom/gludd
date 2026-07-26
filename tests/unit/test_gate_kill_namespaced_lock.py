from pathlib import Path


def test_gate_kill_cleans_namespaced_async_gate_lock() -> None:
    makefile = (Path(__file__).parents[2] / "Makefile").read_text()
    start = makefile.index("gate-kill:")
    recipe = makefile[start : makefile.find("\n\n", start)]
    assert "gludd-resources" in recipe
    assert "async-gate.lock" in recipe
