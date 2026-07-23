"""Fix-not-disable policy: enforce that actions repair rather than disable."""
from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


@dataclass
class ActionIntent:
    action_type: str
    target: str
    reason: str


DISABLE_PATTERNS: frozenset[str] = frozenset({
    "skip",
    "disable",
    "stub",
    "remove",
    "bypass",
    "comment out",
    "xfail",
    "pytest.mark.skip",
    "# noqa",
    "pass # todo",
    "delete",
    "deleting",
    "deletion",
    "deactivate",
    "turn off",
    "workaround",
    "mock out",
    "no-op",
    "noop",
})


def is_disabling_action(action_description: str) -> bool:
    """Return True if action_description contains any disable pattern (case-insensitive)."""
    lower = action_description.lower()
    return any(pattern in lower for pattern in DISABLE_PATTERNS)


class FixNotDisablePolicy(BaseModel):
    fail_closed: bool = True
    allowed_repair_keywords: list[str] = [
        "fix", "repair", "implement", "refactor", "improve",
        "correct", "restore", "enable", "add", "update",
    ]

    def check_action(self, action_description: str, context: str = "") -> tuple[bool, str]:
        """Check whether an action is allowed under this policy.

        Returns (True, "allowed") if permitted, (False, reason) if denied.
        """
        lower = action_description.lower()
        has_disable = any(pattern in lower for pattern in DISABLE_PATTERNS)
        has_repair = any(kw in lower for kw in self.allowed_repair_keywords)

        if has_disable:
            if self.fail_closed:
                return False, (
                    f"Action contains disabling pattern. "
                    f"Policy requires repair, not disable. "
                    f"Description: {action_description!r}"
                )
            # fail_open: only block if no repair keyword
            if not has_repair:
                return False, (
                    f"Action contains disabling pattern and no repair keyword. "
                    f"Description: {action_description!r}"
                )

        return True, "allowed"


def default_fix_not_disable_policy() -> FixNotDisablePolicy:
    """Return the default FixNotDisablePolicy instance."""
    return FixNotDisablePolicy()
