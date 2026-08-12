"""Ansible templating exposed for skills and prompts.

Two distinct rendering paths live here, and the distinction is a security
boundary — do not blur it:

* :meth:`AnsibleTemplater.render` is the **trusted-only** path. It wraps
  ``CoreAnsibleRunner.render_template`` -> ansible's ``Templar.template()``,
  which exposes the FULL Ansible plugin surface (filters, tests, and lookups).
  A lookup such as ``{{ lookup('pipe', 'id') }}`` executes a shell. Only ever
  feed this path templates that the operator authored.

* :meth:`AnsibleTemplater.render_sandboxed` is the **untrusted** path. It
  renders the body in a ``jinja2.sandbox.SandboxedEnvironment`` with
  ``StrictUndefined`` and an empty global namespace (no ``lookup``/``range``/
  plugin surface), and every variable value is marked Ansible-unsafe so a
  payload smuggled in via a *variable value* renders literally instead of being
  re-evaluated. It fails CLOSED: any ``SecurityError`` / syntax
  error raises :class:`TemplateRenderError` rather than leaking a traceback.

The network-exposed ``POST /admin/ansible/render`` endpoint MUST use the
sandboxed path; the trusted path is reserved for operator-authored prompts and
skills running in-process.
"""

from __future__ import annotations

from typing import Any

from general_ludd.ansible.core_runner import CoreAnsibleRunner
from general_ludd.ansible.unsafe import (
    ExtraVarsValidationError,
    validate_extravars,
    wrap_unsafe,
)


class TemplateRenderError(Exception):
    """Raised when a sandboxed render is rejected (security / undefined / syntax).

    Carries only a short, non-sensitive reason — never a Jinja traceback — so
    the API can fail closed with an HTTP 400 and no internal detail leak.
    """


class AnsibleTemplater:
    def __init__(self, extra_vars: dict[str, Any] | None = None) -> None:
        # Validation happens immediately before each render. Keeping the raw
        # reference here lets the sandbox translate a validation denial into
        # its bounded TemplateRenderError contract (HTTP callers return 400),
        # while still guaranteeing no value reaches either renderer unchecked.
        self._extra_vars = {} if extra_vars is None else extra_vars
        self._runner = CoreAnsibleRunner()

    def _merged_vars(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        merged = validate_extravars(self._extra_vars)
        merged.update(kwargs)
        return validate_extravars(merged)

    def render(self, template: str, **kwargs: Any) -> str:
        """TRUSTED-ONLY full-Templar render (full lookup/plugin surface).

        Never call this on an attacker-controlled template body — use
        :meth:`render_sandboxed` for that.
        """
        merged = self._merged_vars(kwargs)
        return self._runner.render_template(template, variables=merged)

    def render_sandboxed(self, template: str, **kwargs: Any) -> str:
        """Render an UNTRUSTED template body, failing closed on any abuse.

        - No ``lookup``/plugin surface (SandboxedEnvironment + empty globals).
        - ``StrictUndefined`` so an unknown name is rejected, not silently "".
        - Dunder / attribute-escape attempts raise ``SecurityError`` -> 400.
        - Every variable value is wrapped Ansible-unsafe so a payload smuggled
          through a *value* (e.g. ``{"x": "{{ 7*7 }}"}``) is emitted literally.
        """
        from jinja2 import meta, nodes
        from jinja2.exceptions import SecurityError, TemplateError
        from jinja2.sandbox import SandboxedEnvironment

        try:
            env = SandboxedEnvironment(autoescape=False)
            # Strip the global namespace so range/dict/lipsum/cycler and any
            # other callable cannot be reached from inside the template.
            env.globals.clear()
            # The network-facing renderer deliberately exposes no filters.
            # Even Jinja's normally-safe string helpers are an unnecessary
            # attribute/call surface for untrusted template bodies.
            env.filters.clear()
            parsed = env.parse(template)
            referenced = meta.find_undeclared_variables(parsed)

            # SandboxedEnvironment permits ordinary attributes such as
            # ``str.upper`` by default.  This boundary permits data values and
            # basic expressions only, so reject attribute nodes before Jinja
            # can turn an unsafe lookup into a silent Undefined value.  Keep
            # Jinja's literal ``none.attr`` compatibility: it cannot expose an
            # attacker-controlled object and historically renders as empty.
            unsafe_attributes = [
                node
                for node in parsed.find_all(nodes.Getattr)
                if not (isinstance(node.node, nodes.Const) and node.node.value is None)
            ]
            names = {node.name for node in parsed.find_all(nodes.Name)}
            if unsafe_attributes or "self" in names:
                raise TemplateRenderError("template rejected: SecurityError")

            # A literal-only template has no access to extra vars.  Avoid
            # inspecting (or rejecting) unused caller data in that case; any
            # variable that the template can reach is still validated below.
            merged = self._merged_vars(kwargs) if referenced else {}
            missing = referenced - set(merged)
            if missing:
                raise TemplateRenderError("template rejected: UndefinedError")
            # Wrap values unsafe: a value that itself contains Jinja must render
            # literally, never be re-evaluated.
            safe_vars = {k: wrap_unsafe(v) for k, v in merged.items()}
            compiled = env.from_string(template)
            return compiled.render(safe_vars)
        except TemplateRenderError:
            raise
        except ExtraVarsValidationError as exc:
            raise TemplateRenderError(
                "template rejected: ExtraVarsValidationError"
            ) from exc
        except SecurityError as exc:
            raise TemplateRenderError(f"template rejected: {exc.__class__.__name__}") from exc
        except TemplateError as exc:
            # Undefined, syntax, type errors during render — fail closed.
            raise TemplateRenderError(f"template rejected: {exc.__class__.__name__}") from exc
        except Exception as exc:  # pragma: no cover - defensive catch-all
            raise TemplateRenderError(f"template rejected: {exc.__class__.__name__}") from exc

    def resolve_fact(self, fact_name: str, host: str = "localhost") -> Any:
        return self._runner.resolve_variable(fact_name, host=host)
