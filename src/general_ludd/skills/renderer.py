"""Shared skill body renderer — Jinja2 with StrictUndefined.

Used by:
  - ``gludd_skill`` Ansible module (playbook path)
  - ``execution/engine.py`` (prompt-injection path)

One renderer, two consumers — W6.5 requirement.
"""

from __future__ import annotations

from typing import Any

try:
    from jinja2 import StrictUndefined, UndefinedError
    from jinja2.exceptions import SecurityError
    from jinja2.sandbox import SandboxedEnvironment

    _HAS_JINJA2 = True
except ImportError:  # pragma: no cover
    _HAS_JINJA2 = False


class SkillRenderError(ValueError):
    """Raised when a skill template references an undefined variable or
    attempts a sandbox-forbidden (SSTI/RCE) operation."""


def render_skill(body: str, variables: dict[str, Any] | None = None) -> str:
    """Render a skill body with Jinja2 StrictUndefined.

    Parameters
    ----------
    body:
        The raw skill body text (may contain ``{{ variable }}`` syntax).
    variables:
        Dict of template variables to inject.

    Returns
    -------
    str
        The rendered text.

    Raises
    ------
    SkillRenderError
        If a variable referenced in the template is not in ``variables``, or
        the template attempts a sandbox-forbidden operation (SSTI/RCE attempt
        such as ``{{ ''.__class__.__mro__ }}`` or
        ``{{ cycler.__init__.__globals__ }}``).
    ImportError
        If ``jinja2`` is not available.
    """
    if not _HAS_JINJA2:
        raise ImportError(
            "jinja2 is required for skill rendering but is not installed. "
            "Add it to your project dependencies."
        )

    vars_dict = dict(variables or {})

    # SandboxedEnvironment (not the bare Environment) because skill bodies can be
    # untrusted/remote (fetched via execution/engine.py + the remote fetcher).
    # A bare Environment lets a malicious template reach Python internals
    # (``{{ ''.__class__.__mro__[1].__subclasses__() }}``,
    # ``{{ cycler.__init__.__globals__['os'].popen(...) }}``) => SSTI -> RCE.
    # The sandbox raises SecurityError on those unsafe attribute/global accesses.
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    try:
        template = env.from_string(body)
        return template.render(**vars_dict)
    except SecurityError as exc:
        raise SkillRenderError(
            f"Skill template attempted a sandbox-forbidden operation: {exc}"
        ) from exc
    except UndefinedError as exc:
        raise SkillRenderError(f"Skill template references undefined variable: {exc}") from exc
