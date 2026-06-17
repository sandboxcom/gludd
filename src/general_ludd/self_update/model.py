"""Dataclasses for the self-update router (issue #81).

A *self-update* is a user request of the form ``"update gludd: <NL request>"``.
The router's job is to turn that natural-language request into a concrete,
guarded plan: which subsystem it targets, what kind of change it is, which files
it would touch, and — critically — *how* the change may be applied (the apply
tier) and whether a human must approve it first.

These types are deliberately pure data (no I/O, no side effects) so the
classifier, apply ladder, and priority/backlog hooks can all share one
vocabulary and be unit-tested in isolation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Subsystem(enum.StrEnum):
    """The target subsystem taxonomy a self-update can route to.

    Mirrors gludd's real configuration + code surface so the classifier can map
    a natural-language request onto the part of the system that owns it. The
    ``UNKNOWN`` member is the default-deny sink: an unroutable request never
    silently lands somewhere plausible-but-wrong.
    """

    ROLES = "roles"
    CONNECTORS = "connectors"
    PROFILES = "profiles"
    CONFIG = "config"
    PROMPTS = "prompts"
    SCORING = "scoring"
    GATEWAY = "gateway"
    SPEND = "spend"
    OBSERVABILITY = "observability"
    SCHEDULING = "scheduling"
    SECURITY = "security"
    UNKNOWN = "unknown"


class ChangeKind(enum.StrEnum):
    """What *kind* of mutation the request implies.

    This is orthogonal to the subsystem: e.g. you can tune a value in config
    (``VALUE_EDIT``) or add a whole new role (``SCAFFOLD``). The kind, together
    with the subsystem, drives apply-tier selection in :mod:`.apply`.
    """

    #: Edit an existing config/var/template value (the preferred, hot-reloadable
    #: path — no Python is touched).
    VALUE_EDIT = "value_edit"
    #: Scaffold a brand-new role / connector / profile from a template.
    SCAFFOLD = "scaffold"
    #: A guarded change to real Python source (the heavyweight, approval-gated
    #: path).
    CODE_CHANGE = "code_change"
    #: Could not be determined.
    UNKNOWN = "unknown"


class ApplyTier(enum.StrEnum):
    """How a planned change is allowed to be applied.

    The tier is the safety dial: ``CONFIG`` changes are hot-reloadable and may
    auto-apply; ``CODE`` changes touch Python and always require approval +
    validation; ``REFUSED`` is the fail-closed terminal state for a request that
    targets a protected guardrail/policy/security surface.
    """

    #: config/var/template edit — hot-reloadable, may auto-apply.
    CONFIG = "config"
    #: scaffold a new role/connector/profile from a template (new files only).
    SCAFFOLD = "scaffold"
    #: guarded real-code change — always requires approval + lint/type validate.
    CODE = "code"
    #: refused outright (protected guardrail/policy/security/settings surface).
    REFUSED = "refused"


@dataclass(frozen=True)
class SelfUpdateRequest:
    """A parsed ``"update gludd: <NL request>"`` request.

    Attributes:
        raw_text: The natural-language request exactly as filed by the user
            (after the ``update gludd:`` prefix is stripped, if present).
        requested_by: Who filed it (an operator id / role); used in the audit
            trail and to decide whether approval is implicitly held.
        approval_token: When set, an out-of-band approval the operator supplied
            up front (lets a code-tier or protected change proceed). ``None``
            means "no approval yet".
    """

    raw_text: str
    requested_by: str = "user"
    approval_token: str | None = None

    @property
    def normalised(self) -> str:
        """The request text lower-cased + stripped, for keyword routing."""
        return self.raw_text.strip().lower()


@dataclass(frozen=True)
class SelfUpdatePlan:
    """The router's verdict on how to satisfy a :class:`SelfUpdateRequest`.

    Attributes:
        subsystem: Which subsystem the change routes to.
        change_kind: What kind of mutation it is.
        target_files: Concrete repo-relative (or absolute) paths the change
            would touch / create. May be empty when the classifier cannot pin
            exact files (the apply ladder then refuses to auto-apply).
        apply_tier: How the change may be applied (the safety dial).
        requires_approval: True iff a human must approve before it can land.
            Always True for CODE / REFUSED tiers and for any protected path.
        rationale: Human-readable explanation of the routing decision (audit +
            UI surface).
        confidence: Classifier confidence in [0.0, 1.0]; low confidence biases
            the apply ladder toward requiring approval.
    """

    subsystem: Subsystem
    change_kind: ChangeKind
    target_files: tuple[str, ...] = field(default_factory=tuple)
    apply_tier: ApplyTier = ApplyTier.CONFIG
    requires_approval: bool = False
    rationale: str = ""
    confidence: float = 0.0
