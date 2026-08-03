"""Language expert CLI moved to collections/ansible_collections/general_ludd/language/.

Core no longer registers a ``gludd language`` subcommand.
Import and call add_language_subparser for backward compatibility only.
"""

from __future__ import annotations

import argparse


def add_language_subparser(
    sub: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    pass


__all__ = [
    "add_language_subparser",
]
