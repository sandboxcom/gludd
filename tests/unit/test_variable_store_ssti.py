"""Regression: VariableStore.render must not be SSTI-exploitable.

A dispatch result's output can carry attacker-influenced text that lands in the
render context, so render() uses a SandboxedEnvironment. Sandboxed dunder/global
access raises, which render() catches (fail-open → returns the raw template), so
an escape payload never evaluates to Python internals.
"""
from __future__ import annotations

from general_ludd.dispatch.variable_store import VariableStore


def test_benign_template_still_renders() -> None:
    store = VariableStore()
    store.set("dispatch", "x", "ok")
    assert store.render("{{ dispatch__x }}") == "ok"


def test_render_with_extras_still_works() -> None:
    store = VariableStore()
    assert store.render("{{ a }}-{{ b }}", a="1", b="2") == "1-2"


def test_ssti_payloads_do_not_leak_python_internals() -> None:
    store = VariableStore()
    store.set("dispatch", "x", "ok")
    payloads = [
        "{{ ().__class__.__mro__ }}",
        "{{ ''.__class__.__mro__[1].__subclasses__() }}",
        "{{ ().__class__.__bases__[0].__subclasses__() }}",
        "{{ self.__init__.__globals__ }}",
    ]
    for payload in payloads:
        out = store.render(payload)
        # No EVALUATED object repr / type / address may appear. (On a blocked
        # payload render() fails open and returns the raw template, which also
        # contains none of these markers.)
        assert "<class" not in out, f"class repr leaked for {payload!r}: {out!r}"
        assert "0x" not in out, f"address leaked for {payload!r}: {out!r}"
        assert "built-in" not in out, f"builtin leaked for {payload!r}: {out!r}"
        assert "function " not in out, f"function leaked for {payload!r}: {out!r}"
