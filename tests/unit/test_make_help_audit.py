from __future__ import annotations

from pathlib import Path

from scripts import check_make_help


def test_public_targets_are_listed_in_help() -> None:
    makefile = Path("Makefile")
    public = set(check_make_help.public_targets(makefile))
    listed = (
        check_make_help.help_targets_from_makefile(makefile)
        | check_make_help.help_targets_from_output()
    )

    assert public <= listed


def test_internal_targets_are_excluded_from_public_audit() -> None:
    makefile = Path("Makefile")
    public = set(check_make_help.public_targets(makefile))

    assert "_gate-fresh-check" not in public
    assert "commit-bootstrap" not in public
