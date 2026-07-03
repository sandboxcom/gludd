"""Tests for the ``gludd core-changes list`` CLI (FIM change-export SLICE 2).

Seed a :class:`ChangeRecordStore` in a tmp dir, then invoke the list command
against it and assert on the rendered unified diffs, the exclude filtering, and
the core/user classification.
"""

from __future__ import annotations

import argparse
import io
from contextlib import redirect_stdout

from general_ludd.cli_core_changes import (
    _cmd_core_changes_list,
    _diff,
    _excluded,
    classify,
)
from general_ludd.integrity.change_log import ChangeRecordStore


def _args(store_dir: str, **overrides: object) -> argparse.Namespace:
    ns = argparse.Namespace(
        store_dir=store_dir,
        json=False,
        core_only=False,
        user_only=False,
    )
    for k, v in overrides.items():
        setattr(ns, k, v)
    return ns


def _run(args: argparse.Namespace) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _cmd_core_changes_list(args)
    return buf.getvalue()


# --------------------------------------------------------------------------
# unified diff rendering for a modified record
# --------------------------------------------------------------------------

def test_modified_record_renders_unified_diff(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record(
        "src/app.py",
        "modified",
        "agent tidied imports",
        old_content="import os\n",
        new_content="import os\nimport sys\n",
    )

    out = _run(_args(str(tmp_path)))

    # Unified-diff headers reference the file path.
    assert "--- " in out
    assert "+++ " in out
    assert "src/app.py" in out
    # Both content lines appear (context + addition).
    assert "import os" in out
    assert "+import sys" in out


def test_reason_appears_in_output(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record(
        "src/app.py",
        "modified",
        "the-unique-reason-string",
        old_content="a\n",
        new_content="b\n",
    )

    out = _run(_args(str(tmp_path)))
    assert "the-unique-reason-string" in out


# --------------------------------------------------------------------------
# a created record (no old_content) renders as an all-added diff
# --------------------------------------------------------------------------

def test_created_record_renders_all_added_diff(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record(
        "src/brand_new.py",
        "created",
        "agent added a file",
        new_content="line one\nline two\n",
    )

    out = _run(_args(str(tmp_path)))
    assert "+line one" in out
    assert "+line two" in out
    # A pure creation has no removed lines.
    assert "-line one" not in out


# --------------------------------------------------------------------------
# excluded paths are filtered out of the listing
# --------------------------------------------------------------------------

def test_excluded_record_is_filtered_out(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record(
        "src/__pycache__/x.pyc",
        "modified",
        "should-not-appear-reason",
        old_content="old\n",
        new_content="new\n",
    )
    store.record(
        "src/keep.py",
        "modified",
        "should-appear-reason",
        old_content="old\n",
        new_content="new\n",
    )

    out = _run(_args(str(tmp_path)))
    assert "should-appear-reason" in out
    assert "should-not-appear-reason" not in out
    assert "__pycache__/x.pyc" not in out


def test_excluded_matches_exclude_patterns():
    assert _excluded("foo/__pycache__/x.pyc") is True
    assert _excluded("bar/module.pyc") is True
    assert _excluded("a/.git/config") is True
    assert _excluded("data/store.db") is True
    assert _excluded("src/general_ludd/cli.py") is False


# --------------------------------------------------------------------------
# classify: core (installed package) vs user paths
# --------------------------------------------------------------------------

def test_classify_package_path_is_core():
    import general_ludd

    pkg_file = general_ludd.__file__  # .../general_ludd/__init__.py
    assert classify(pkg_file) == "core"


def test_classify_src_checkout_path_is_core():
    assert classify("/home/dev/project/src/general_ludd/cli.py") == "core"


def test_classify_user_config_path_is_user(tmp_path):
    import os

    assert classify(os.path.expanduser("~/.config/gludd/overlay.py")) == "user"
    assert classify(str(tmp_path / "some_user_file.py")) == "user"


# --------------------------------------------------------------------------
# --core-only / --user-only filtering
# --------------------------------------------------------------------------

def test_user_only_filters_to_user_records(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record(
        "/home/dev/project/src/general_ludd/thing.py",
        "modified",
        "core-record-reason",
        old_content="a\n",
        new_content="b\n",
    )
    store.record(
        "/home/dev/.config/gludd/overlay.py",
        "modified",
        "user-record-reason",
        old_content="a\n",
        new_content="b\n",
    )

    out = _run(_args(str(tmp_path), user_only=True))
    assert "user-record-reason" in out
    assert "core-record-reason" not in out


def test_core_only_filters_to_core_records(tmp_path, monkeypatch):
    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record(
        "/home/dev/project/src/general_ludd/thing.py",
        "modified",
        "core-record-reason",
        old_content="a\n",
        new_content="b\n",
    )
    store.record(
        "/home/dev/.config/gludd/overlay.py",
        "modified",
        "user-record-reason",
        old_content="a\n",
        new_content="b\n",
    )

    out = _run(_args(str(tmp_path), core_only=True))
    assert "core-record-reason" in out
    assert "user-record-reason" not in out


# --------------------------------------------------------------------------
# --json dumps the records
# --------------------------------------------------------------------------

def test_json_output_dumps_records(tmp_path, monkeypatch):
    import json

    monkeypatch.delenv("GL_INTEGRITY_KEY", raising=False)
    store = ChangeRecordStore(store_dir=str(tmp_path))
    store.record(
        "src/app.py",
        "modified",
        "json-reason",
        old_content="a\n",
        new_content="b\n",
    )

    out = _run(_args(str(tmp_path), json=True))
    data = json.loads(out)
    assert isinstance(data, list)
    assert data[0]["file_path"] == "src/app.py"
    assert data[0]["reason"] == "json-reason"


# --------------------------------------------------------------------------
# _diff unit behaviour
# --------------------------------------------------------------------------

def test_diff_created_is_all_added():
    from general_ludd.integrity.change_log import ChangeLogEntry

    entry = ChangeLogEntry(
        file_path="new.py",
        change_type="created",
        new_content="alpha\nbeta\n",
    )
    d = _diff(entry)
    assert "+alpha" in d
    assert "+beta" in d
    assert "-alpha" not in d


def test_diff_modified_has_both_headers():
    from general_ludd.integrity.change_log import ChangeLogEntry

    entry = ChangeLogEntry(
        file_path="mod.py",
        change_type="modified",
        old_content="one\n",
        new_content="two\n",
    )
    d = _diff(entry)
    assert "--- " in d and "mod.py" in d
    assert "+++ " in d
    assert "-one" in d
    assert "+two" in d


# --------------------------------------------------------------------------
# classify: anchored on path COMPONENTS (not an unanchored substring)
# --------------------------------------------------------------------------

def test_classify_src_general_ludd_component_is_core():
    # A genuine ``src/general_ludd/`` checkout path is core.
    assert classify("/x/src/general_ludd/a.py") == "core"


def test_classify_sibling_containing_literal_is_user():
    # A sibling path that merely *contains* the literal must NOT match: the
    # anchor requires ``src/general_ludd`` as whole components, so
    # ``general_ludd_notes`` is user, not core.
    assert classify("/x/src/general_ludd_notes/a.py") == "user"


def test_classify_config_overlay_is_user():
    import os

    assert classify(os.path.expanduser("~/.config/gludd/x.py")) == "user"


def test_classify_case_variant_core_path_is_core():
    # Case-insensitive filesystems (macOS/Windows): an upper/mixed-case variant
    # of a core path still classifies as core (compare is case-folded).
    assert classify("/x/SRC/General_Ludd/A.py") == "core"


# --------------------------------------------------------------------------
# _excluded: separator + case normalisation
# --------------------------------------------------------------------------

def test_excluded_normalises_backslash_paths():
    # A Windows-style backslash path is separator-normalised before matching.
    assert _excluded("a\\__pycache__\\b.pyc") is True


def test_excluded_is_case_insensitive():
    # An upper-case compiled-artefact extension is still excluded.
    assert _excluded("pkg/module.PYC") is True
    assert _excluded("A\\.GIT/config") is True


# --------------------------------------------------------------------------
# the FIM exclude set is ONE shared constant used by every scan site
# --------------------------------------------------------------------------

def test_exclude_constant_is_shared_across_call_sites():
    from general_ludd import cli, cli_core_changes
    from general_ludd.integrity.fim_excludes import FIM_EXCLUDE_PATTERNS

    # Both the change-export module and the CLI scan site reference the SAME
    # object — they cannot drift apart.
    assert cli_core_changes.FIM_EXCLUDE_PATTERNS is FIM_EXCLUDE_PATTERNS
    assert cli.FIM_EXCLUDE_PATTERNS is FIM_EXCLUDE_PATTERNS
    # Backward-compatible alias still points at the shared constant.
    assert cli_core_changes._EXCLUDES is FIM_EXCLUDE_PATTERNS
    # The shared set is unchanged from the historical literal (scan behaviour
    # preserved — this was a de-duplication, not a policy change).
    assert list(FIM_EXCLUDE_PATTERNS) == [
        r"\.pyc$",
        r"__pycache__",
        r"\.git/",
        r"\.db$",
    ]
